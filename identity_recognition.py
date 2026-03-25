from __future__ import annotations

import io
from typing import Any

import numpy as np

KNOWN_THRESHOLD = 0.45           # min-distance ≤ this → known
LOW_CONF_THRESHOLD = 0.58        # this < min-distance ≤ LOW_CONF → low_confidence
CLUSTER_DISTANCE = 0.50          # intra-clip grouping threshold (embedding-only path)
CLUSTER_DISTANCE_SPATIAL = 0.70  # looser threshold used when bounding boxes also overlap
MIN_EMBEDDINGS_FOR_KNOWN = 5     # identity needs at least this many embeddings to be "known"


def _import_face_recognition() -> Any:
    try:
        import face_recognition
        return face_recognition
    except ImportError:
        return None


def _frame_to_rgb(png_bytes: bytes):
    """Convert raw PNG bytes to an RGB numpy array."""
    from PIL import Image
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    return np.array(img)


def _crop_face(rgb_array, location: tuple, pad_fraction: float = 0.2) -> bytes:
    """Crop a face from an RGB array with optional padding. Returns JPEG bytes."""
    from PIL import Image
    top, right, bottom, left = location
    h, w = rgb_array.shape[:2]
    pad_y = int((bottom - top) * pad_fraction)
    pad_x = int((right - left) * pad_fraction)
    top = max(0, top - pad_y)
    bottom = min(h, bottom + pad_y)
    left = max(0, left - pad_x)
    right = min(w, right + pad_x)
    crop = Image.fromarray(rgb_array[top:bottom, left:right])
    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _boxes_overlap(a: tuple, b: tuple) -> bool:
    """Return True if two face bounding boxes (top, right, bottom, left) overlap."""
    a_top, a_right, a_bottom, a_left = a
    b_top, b_right, b_bottom, b_left = b
    return a_top < b_bottom and a_bottom > b_top and a_left < b_right and a_right > b_left


def detect_faces_in_frames(
    frames: list[bytes],
) -> list[tuple[list[float], bytes, int, tuple]]:
    """Run face detection and encoding on a list of PNG frames.

    Returns a list of (embedding, crop_bytes, frame_idx, location) — one entry per
    detected face across all frames. Returns [] if face_recognition is not
    installed or no faces are found."""
    fr = _import_face_recognition()
    if fr is None:
        print("[identity_recognition] warning: face_recognition not installed")
        return []

    results = []
    for frame_idx, png in enumerate(frames):
        try:
            rgb = _frame_to_rgb(png)
            locations = fr.face_locations(rgb, model="hog")
            if not locations:
                locations = fr.face_locations(rgb, model="cnn")
            encodings = fr.face_encodings(rgb, locations)
            for location, encoding in zip(locations, encodings):
                crop = _crop_face(rgb, location)
                results.append((encoding.tolist(), crop, frame_idx, location))
        except Exception as exc:
            print(f"[identity_recognition] warning: frame {frame_idx} failed: {exc}")
            continue
    return results


def cluster_faces(
    detected_faces: list[tuple[list[float], bytes, int, tuple]],
) -> list[dict]:
    """Group detected faces from multiple frames into per-person clusters using
    greedy single-linkage clustering.

    Merging uses two signals:
      - Embedding distance < CLUSTER_DISTANCE (embedding-only)
      - Bounding boxes overlap AND embedding distance < CLUSTER_DISTANCE_SPATIAL
        (positional confirmation allows a looser embedding threshold)

    Returns a list of cluster dicts, each with:
      - mean_embedding: list[float]
      - best_crop: bytes
      - occurrence_count: int
    """
    fr = _import_face_recognition()
    if fr is None:
        return []

    clusters: list[dict] = []
    for embedding, crop, frame_idx, location in detected_faces:
        emb_array = np.array(embedding)
        matched = None
        for cluster in clusters:
            rep = np.array(cluster["representative_embedding"])
            dist = fr.face_distance([rep], emb_array)[0]
            overlap = any(_boxes_overlap(location, loc) for loc in cluster["locations"])
            if dist < CLUSTER_DISTANCE or (overlap and dist < CLUSTER_DISTANCE_SPATIAL):
                matched = cluster
                break
        if matched is not None:
            matched["embeddings"].append(embedding)
            matched["crops"].append(crop)
            matched["frame_indices"].append(frame_idx)
            matched["locations"].append(location)
        else:
            clusters.append({
                "representative_embedding": embedding,
                "embeddings": [embedding],
                "crops": [crop],
                "frame_indices": [frame_idx],
                "locations": [location],
            })

    result = []
    for c in clusters:
        mean_emb = np.mean(np.array(c["embeddings"]), axis=0).tolist()
        result.append({
            "mean_embedding": mean_emb,
            "best_crop": c["crops"][0],
            "occurrence_count": len(c["embeddings"]),
        })
    return result


def match_cluster(
    cluster_embedding: list[float], registry: dict
) -> tuple[str | None, str, float | None]:
    """Compare a cluster's mean embedding against the identity registry.

    Returns (identity_id, status, distance) where status is one of:
      'known', 'low_confidence', 'unknown'
    """
    fr = _import_face_recognition()
    if fr is None:
        return None, "unknown", None

    identities = registry.get("identities", [])
    if not identities:
        return None, "unknown", None

    emb_array = np.array(cluster_embedding)
    best_dist = float("inf")
    best_id = None

    scores = []
    for identity in identities:
        refs = identity.get("embeddings", [])
        if not refs:
            continue
        refs_array = np.array(refs)
        dists = fr.face_distance(refs_array, emb_array)
        min_dist = float(np.min(dists))
        mean_dist = float(np.mean(dists))
        scores.append((min_dist, mean_dist, identity))
        if min_dist < best_dist:
            best_dist = min_dist
            best_id = identity["identity_id"]

    scores.sort(key=lambda x: x[0])
    print(f"[match_cluster] top-3: {[(i['display_name'], round(md,3), round(me,3)) for md,me,i in scores[:3]]}")

    if best_id:
        identity = next(i for i in identities if i["identity_id"] == best_id)
        n_embs = len(identity.get("embeddings", []))
        if best_dist <= KNOWN_THRESHOLD and n_embs >= MIN_EMBEDDINGS_FOR_KNOWN:
            return best_id, "known", best_dist
        elif best_dist <= LOW_CONF_THRESHOLD:
            return best_id, "low_confidence", best_dist
        else:
            return None, "unknown", None
    return None, "unknown", None


def run_detection_pipeline(
    frames: list[bytes], registry: dict
) -> list[dict]:
    """Full pipeline: detect → cluster → match.

    Returns a list of detection dicts ready for the API response:
      - mean_embedding: list[float]  (used server-side for confirm)
      - best_crop: bytes             (used server-side for face-crop endpoint)
      - occurrence_count: int
      - status: 'known' | 'low_confidence' | 'unknown'
      - identity_id: str | None
      - display_name: str | None
      - keyword_string: str | None
      - distance: float | None
    """
    detected = detect_faces_in_frames(frames)
    if not detected:
        return []

    clusters = cluster_faces(detected)
    if not clusters:
        return []

    # Build a quick lookup: identity_id → identity dict
    id_lookup = {i["identity_id"]: i for i in registry.get("identities", [])}

    results = []
    for cluster in clusters:
        identity_id, status, distance = match_cluster(
            cluster["mean_embedding"], registry
        )
        identity = id_lookup.get(identity_id) if identity_id else None
        results.append({
            "mean_embedding": cluster["mean_embedding"],
            "best_crop": cluster["best_crop"],
            "occurrence_count": cluster["occurrence_count"],
            "status": status,
            "identity_id": identity_id,
            "display_name": identity["display_name"] if identity else None,
            "keyword_string": identity["keyword_string"] if identity else None,
            "distance": round(distance, 4) if distance is not None else None,
        })
    return results
