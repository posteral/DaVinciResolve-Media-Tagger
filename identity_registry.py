from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

MAX_EMBEDDINGS = 20  # FIFO cap per identity


def _registry_path() -> Path:
    return Path(__file__).parent / "identity_registry.json"


def _faces_dir() -> Path:
    return Path(__file__).parent / "faces"


def load_registry() -> dict:
    """Load the identity registry from disk. Returns an empty registry on
    missing file, invalid JSON, or any read error."""
    path = _registry_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "identities" not in data:
            raise ValueError("unexpected registry shape")
        return data
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"[identity_registry] warning: could not load registry: {exc}")
    return {"version": 1, "identities": []}


def save_registry(registry: dict) -> None:
    """Atomically write the registry to disk (write to .tmp then rename).
    A .bak copy of the previous file is kept for manual recovery."""
    path = _registry_path()
    tmp = path.with_suffix(".tmp")
    bak = path.with_suffix(".json.bak")

    try:
        if path.exists():
            path.replace(bak)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2)
        tmp.replace(path)
    except Exception as exc:
        print(f"[identity_registry] error: could not save registry: {exc}")
        raise


def save_face_crop(identity_id: str, crop_bytes: bytes) -> str:
    """Save a face crop PNG/JPEG to the faces/ directory. Returns the
    relative path string stored in the registry."""
    faces = _faces_dir()
    faces.mkdir(exist_ok=True)
    n = len(list(faces.glob(f"{identity_id}_*.jpg")))
    filename = f"{identity_id}_{n}.jpg"
    dest = faces / filename
    dest.write_bytes(crop_bytes)
    return f"faces/{filename}"


def add_identity(
    registry: dict,
    display_name: str,
    keyword_string: str,
    embedding: list[float],
    crop_bytes: bytes | None,
) -> tuple[dict, str]:
    """Add a new identity to the registry. Returns (updated_registry, identity_id)."""
    identity_id = str(uuid.uuid4())
    thumbnail_path = ""
    if crop_bytes:
        thumbnail_path = save_face_crop(identity_id, crop_bytes)

    registry["identities"].append({
        "identity_id": identity_id,
        "display_name": display_name,
        "keyword_string": keyword_string,
        "embeddings": [embedding],
        "thumbnail_path": thumbnail_path,
    })
    return registry, identity_id


def update_identity_embedding(
    registry: dict,
    identity_id: str,
    embedding: list[float],
    crop_bytes: bytes | None,
) -> dict:
    """Append a new embedding to an existing identity (FIFO cap MAX_EMBEDDINGS).
    Optionally updates the thumbnail."""
    for identity in registry["identities"]:
        if identity["identity_id"] != identity_id:
            continue
        identity["embeddings"].append(embedding)
        if len(identity["embeddings"]) > MAX_EMBEDDINGS:
            identity["embeddings"] = identity["embeddings"][-MAX_EMBEDDINGS:]
        if crop_bytes:
            identity["thumbnail_path"] = save_face_crop(identity_id, crop_bytes)
        break
    return registry


def list_identities(registry: dict) -> list[dict]:
    """Return a lightweight list of identities (no embeddings) for the UI."""
    return [
        {
            "identity_id": i["identity_id"],
            "display_name": i["display_name"],
            "keyword_string": i["keyword_string"],
        }
        for i in registry.get("identities", [])
    ]


def find_identity_by_name(registry: dict, display_name: str) -> dict | None:
    """Return the first identity whose display_name matches (case-insensitive)."""
    lower = display_name.strip().lower()
    for identity in registry.get("identities", []):
        if identity["display_name"].strip().lower() == lower:
            return identity
    return None
