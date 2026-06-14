# Spec: Identity Recognition for Recurring People

## 1. Overview

Detect human faces across sampled frames in a clip, group repeated appearances,
match them against a local identity library, and present detected individuals in a
dedicated right-side panel where the user can confirm or assign names. Confirmed
names are stored for future recognition and added to the clip's keywords.

**Scope:** People only. Dogs/pets deferred to a future milestone.
**Library:** `face_recognition` (dlib-based).
**Registry:** Local JSON file (`identity_registry.json`).

---

## 2. UI Layout

```
┌──────────────────────────────────────────────────────────────────────┐
│  Clip Keywords                     │  Detected Identities            │
│  ─────────────────────────────     │  ──────────────────────────     │
│  [thumbnail]                       │  ┌─────────────────────────┐   │
│                                    │  │ [face crop]             │   │
│  Clip: A001_C003_0215AB            │  │ ● known                 │   │
│                                    │  │ Maria Santos            │   │
│  Keywords                          │  │ [Maria Santos      ▾]   │   │
│  [interview ×] [city ×]            │  │ ☑ Add as keyword        │   │
│                                    │  └─────────────────────────┘   │
│  Suggested                         │                                 │
│  [night] [outdoors]                │  ┌─────────────────────────┐   │
│                                    │  │ [face crop]             │   │
│  AI Suggested ⠋                    │  │ ◌ unknown               │   │
│  rolling hills  [+ Add]            │  │ Who is this?            │   │
│                                    │  │ [________________  ▾]   │   │
│  Add keyword                       │  │ ☑ Add as keyword        │   │
│  [_________________________]       │  └─────────────────────────┘   │
│                                    │                                 │
│                                    │  [ Apply identities to kw ]    │
└──────────────────────────────────────────────────────────────────────┘
```

- Existing content stays in the left/center column.
- Right panel is a new `<div id="identity-panel">` alongside the `.card` div.
- Panel is hidden until a clip with a proxy is loaded.
- On mobile / narrow viewports the panel stacks below the card.

---

## 3. New Files

| File | Purpose |
|------|---------|
| `identity_recognition.py` | Face detection, embedding extraction, intra-clip clustering, registry matching |
| `identity_registry.py` | Load/save JSON registry, add/update identities, save face crops |
| `test_identity_recognition.py` | Unit tests for recognition pipeline |
| `test_identity_registry.py` | Unit tests for registry operations |
| `identity_registry.json` | Runtime data file (gitignored) |
| `faces/` | Directory of saved face crop PNGs (gitignored) |

### Existing files to modify

- `app.py` — 4 new routes, 2 new process-level caches
- `templates/index.html` — right panel layout, CSS, 3 new JS functions
- `requirements.txt` — add `face_recognition>=1.3.0`, `Pillow>=10.0`, `numpy>=1.24`
- `.gitignore` — add `identity_registry.json` and `faces/`

---

## 4. Identity Registry JSON Schema

```json
{
  "version": 1,
  "identities": [
    {
      "identity_id": "uuid4-string",
      "display_name": "Maria Santos",
      "keyword_string": "Maria Santos",
      "embeddings": [
        [0.12, -0.34, ...]
      ],
      "thumbnail_path": "faces/uuid4-string_0.jpg"
    }
  ]
}
```

- `embeddings`: list of 128-float lists (face_recognition encoding format). Max 20 per identity (FIFO cap — drop oldest when adding a 21st).
- `thumbnail_path`: relative path to the best face crop saved as JPEG. Updated on each new confirmed match.
- `keyword_string`: what gets written to Resolve. Defaults to `display_name`; user can override.

### Registry file location

`{project_root}/identity_registry.json`
Face crops: `{project_root}/faces/{identity_id}_{n}.jpg`

---

## 5. Matching Algorithm and Thresholds

```
KNOWN_THRESHOLD       = 0.55   # distance ≤ 0.55 → known
LOW_CONF_THRESHOLD    = 0.70   # 0.55 < distance ≤ 0.70 → low confidence
CLUSTER_DISTANCE      = 0.50   # intra-clip grouping threshold
MAX_EMBEDDINGS        = 20     # FIFO cap per identity
```

### Match lookup

```python
def match_cluster(cluster_embedding, registry):
    best_dist = float("inf")
    best_id = None
    for identity in registry["identities"]:
        for ref_emb in identity["embeddings"]:
            dist = face_recognition.face_distance([ref_emb], cluster_embedding)[0]
            if dist < best_dist:
                best_dist = dist
                best_id = identity["identity_id"]

    if best_dist <= KNOWN_THRESHOLD:
        return best_id, "known", best_dist
    elif best_dist <= LOW_CONF_THRESHOLD:
        return best_id, "low_confidence", best_dist
    else:
        return None, "unknown", best_dist
```

---

## 6. Intra-Clip Clustering

Goal: if the same person appears in 3 of 5 sampled frames, show 1 card, not 3.

**Algorithm:** greedy single-linkage clustering on face embeddings.

```python
def cluster_faces(detected_faces):
    # detected_faces: list of (embedding, crop_bytes, frame_idx)
    clusters = []
    for embedding, crop, frame_idx in detected_faces:
        matched = None
        for cluster in clusters:
            dist = face_recognition.face_distance(
                [cluster["representative_embedding"]], embedding
            )[0]
            if dist < CLUSTER_DISTANCE:
                matched = cluster
                break
        if matched:
            matched["embeddings"].append(embedding)
            matched["crops"].append(crop)
        else:
            clusters.append({
                "representative_embedding": embedding,
                "embeddings": [embedding],
                "crops": [crop],
                "frame_indices": [frame_idx],
            })

    # Build cluster dict: use first crop as thumbnail, mean embedding for matching
    result = []
    for c in clusters:
        mean_emb = np.mean(c["embeddings"], axis=0)
        result.append({
            "mean_embedding": mean_emb.tolist(),
            "best_crop": c["crops"][0],
            "occurrence_count": len(c["embeddings"]),
        })
    return result
```

---

## 7. API Endpoints

### `POST /api/clip/detect-identities`

Runs the full detection pipeline on the current clip's proxy frames.
Expensive — runs outside `_resolve_lock`. File path passed in body to avoid re-acquiring lock.

**Request:**
```json
{ "path": "/Volumes/T7/proxies/clip.mov" }
```

**Response:**
```json
{
  "detections": [
    {
      "face_token": "random-uuid",
      "status": "known",
      "identity_id": "uuid4",
      "display_name": "Maria Santos",
      "keyword_string": "Maria Santos",
      "distance": 0.42,
      "occurrence_count": 3
    },
    {
      "face_token": "random-uuid-2",
      "status": "unknown",
      "identity_id": null,
      "display_name": null,
      "keyword_string": null,
      "distance": null,
      "occurrence_count": 1
    }
  ]
}
```

`face_token` is a server-side key into `_face_crop_cache` (crop PNG bytes) and
`_detection_cache` (mean embedding). Expires when the server restarts.

---

### `GET /api/clip/face-crop?token=<face_token>`

Returns the face crop PNG for a given token. `404` if token unknown.

---

### `GET /api/identities`

Returns all known identities (for the assign dropdown datalist).

**Response:**
```json
{
  "identities": [
    { "identity_id": "uuid4", "display_name": "Maria Santos", "keyword_string": "Maria Santos" }
  ]
}
```

---

### `POST /api/identities/confirm`

Commits user assignments from the review panel.

**Request:**
```json
{
  "assignments": [
    {
      "face_token": "random-uuid",
      "display_name": "Maria Santos",
      "keyword_string": "Maria Santos",
      "identity_id": "uuid4",
      "is_new_identity": false,
      "add_as_keyword": true
    },
    {
      "face_token": "random-uuid-2",
      "display_name": "Pedro Alves",
      "keyword_string": "Pedro Alves",
      "identity_id": null,
      "is_new_identity": true,
      "add_as_keyword": true
    }
  ]
}
```

**Response:**
```json
{
  "keywords_added": ["Maria Santos", "Pedro Alves"]
}
```

The caller (browser) then appends `keywords_added` to `currentKeywords` and shows
the Save button — the existing Save flow handles writing to Resolve.

---

## 8. Module Architecture

```
app.py
  ├── _face_crop_cache: dict[str, bytes]     # face_token → PNG bytes
  ├── _detection_cache: dict[str, list]      # face_token → mean embedding
  ├── POST /api/clip/detect-identities
  ├── GET  /api/clip/face-crop
  ├── GET  /api/identities
  └── POST /api/identities/confirm

identity_recognition.py
  ├── detect_faces_in_frames(frames) → list[(embedding, crop_bytes, frame_idx)]
  ├── cluster_faces(detected_faces) → list[cluster_dict]
  ├── match_cluster(cluster_embedding, registry) → (identity_id, status, distance)
  └── run_detection_pipeline(frames, registry) → list[detection_dict]

identity_registry.py
  ├── load_registry() → dict
  ├── save_registry(registry) → None          # atomic write via .tmp + rename
  ├── add_identity(registry, display_name, keyword_string, embedding, crop_bytes) → (registry, identity_id)
  ├── update_identity_embedding(registry, identity_id, embedding, crop_bytes) → registry
  └── list_identities(registry) → list[dict]
```

---

## 9. Implementation Phases

### Phase 1 — Backend core (no UI changes)

- [ ] `identity_registry.py`: load/save, add/update, atomic write, FIFO cap
- [ ] `identity_recognition.py`: detect, cluster, match, pipeline
- [ ] `test_identity_registry.py`: load empty, add identity, update embedding, FIFO cap, atomic save
- [ ] `test_identity_recognition.py`: detect returns embeddings, cluster dedupes, match known/unknown/low_conf, pipeline end-to-end
- [ ] `.gitignore` entries for `identity_registry.json` and `faces/`
- [ ] `requirements.txt` updated

### Phase 2 — API routes

- [ ] `POST /api/clip/detect-identities` in `app.py`
- [ ] `GET /api/clip/face-crop` in `app.py`
- [ ] `GET /api/identities` in `app.py`
- [ ] `POST /api/identities/confirm` in `app.py`
- [ ] `_face_crop_cache` and `_detection_cache` process-level dicts

### Phase 3 — Review UI

- [ ] Two-column layout in `templates/index.html` (`.layout-row` flex wrapper)
- [ ] `#identity-panel` CSS: card style, per-card layout, status badges (known=green, low_confidence=amber, unknown=grey)
- [ ] `loadIdentities(filePath)` JS: POST detect-identities, call `renderIdentityCards`
- [ ] `renderIdentityCards(detections)` JS: build card DOM, `<img src="/api/clip/face-crop?token=...">`, assign field with `<datalist>`, add-as-keyword checkbox
- [ ] `applyIdentities()` JS: collect assignments, POST confirm, append `keywords_added` to `currentKeywords`, call `renderKeywords()`, show Save button
- [ ] Hook `loadIdentities` into `renderClip()` (only when `!data.no_proxy`)
- [ ] Hide panel when no detections

---

## 10. Key Edge Cases

| Case | Behaviour |
|------|-----------|
| No faces detected | Panel shows "No faces detected in this clip." Apply button hidden. |
| No proxy file | `loadIdentities` not called. Panel stays hidden. |
| Low confidence match | Assign field pre-filled; amber badge. User must confirm (pre-fill counts). |
| Same person in multiple clips | Each clip detection is independent; registry grows with new embeddings each confirm. |
| Same person split into 2 clusters | Two cards, likely same suggested name. Both checked → keyword written once (dedup in `set_keywords`). |
| Unknown face → new identity | `is_new_identity: true` → new registry entry created with embedding saved. |
| Unknown face → assign to existing | `identity_id` set from datalist lookup → embedding appended to existing entry. |
| `face_recognition` not installed | Lazy import; returns `[]`; panel shows "Face detection unavailable". |
| Corrupt registry JSON | `load_registry` returns empty registry; logs warning; file replaced on next save. |
| Server restart before confirm | Face tokens lost; confirm creates registry entry without embedding. Keyword still written correctly. |
| Empty registry (first use) | All faces unknown. First confirms populate the registry. |

---

## 11. Dependencies

Add to `requirements.txt`:

```
face_recognition>=1.3.0
Pillow>=10.0
numpy>=1.24
```

**CI (GitHub Actions):** Add before `pip install -r requirements.txt`:

```yaml
- name: Install dlib build dependencies
  run: sudo apt-get install -y build-essential cmake libopenblas-dev liblapack-dev libx11-dev
```
