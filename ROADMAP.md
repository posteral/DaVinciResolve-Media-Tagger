# Roadmap

## Current state

The tagger (v0.21.1) handles clip-by-clip keyword tagging with proximity
suggestions, AI suggestions, face recognition, and a filmstrip panel. It
operates on the currently selected clip in Resolve and navigates within a
single folder at a time.

---

## Next: Shot Finder (`/search`)

A second page on the same Flask server — a research tool for browsing a
30k-clip archive to find the right shot for a scene. Completely independent
from the tagger; shares the server but has its own index and UI.

### Core idea

Build a persistent keyword index from the full project once per session
(via `media_pool.ExportMetadata(csv_path)`), then expose a fast search
interface. When you find the right clip, one click selects it in Resolve's
media pool viewer.

### Architecture

**Index build**
- One `ExportMetadata` IPC call exports all clip metadata (name, keywords,
  folder path, proxy path, date) to a CSV file.
- Python parses the CSV and stores a clip index in SQLite (fast full-text
  search, persists across restarts, survives server restarts without rebuild).
- Index is rebuilt once per session on demand ("Rebuild index" button) or
  automatically on first visit if the DB is empty.
- Build runs in a background thread; UI shows a progress indicator.

**Search**
- Full-text keyword search: type "sunset Corsica" → clips tagged with both.
- Browse by keyword: click any keyword tag in results to filter by it.
- Results sorted by relevance (keyword match score), with date as tiebreaker.
- Pagination or virtual scroll for large result sets.

**Result card**
- Proxy thumbnail (reuse `/api/clip/thumbnail` infrastructure).
- Clip name, date, folder path.
- Full keyword tag list (clickable — click a tag to add it to the search).

**Jump to Resolve**
- Click a result → server calls `SetCurrentFolder(folder)` +
  `SetSelectedClip(item)` via the Resolve scripting API.
- Resolve selects the clip in the media pool and shows it in the viewer.
- Requires a live Resolve connection (same constraint as the tagger).

### Open questions to resolve before building

1. **`ExportMetadata` performance on 30k clips** — needs to be tested on
   the actual project. If <30s, run synchronously on first visit. If longer,
   run in background with a progress indicator.
2. **`SetSelectedClip` across folders** — needs to confirm that calling
   `SetCurrentFolder` before `SetSelectedClip` reliably shows the clip in
   Resolve's viewer, especially when the media pool browser is not currently
   focused on that folder.
3. **Index staleness** — newly tagged clips won't appear in search until the
   next rebuild. A "last built" timestamp in the UI makes this transparent.

### Milestones

**M1 — Index build pipeline** ✅
- `ExportMetadata` call + CSV parse + SQLite schema
- Background build thread with status endpoint
- `GET /search/api/status` → `{state: "building"|"ready"|"empty", clip_count, built_at}`
- `POST /search/api/build` → triggers rebuild

  **M1.1 ✅** — `ExportMetadata` smoke test complete. Key findings:
  - 2.1s export + 0.44s parse for 23,293 clips (14.6 MB CSV, UTF-16)
  - Columns: `File Name`, `Clip Directory`, `Keywords`, `Date Modified`
  - No proxy path in export — fetch lazily at click time via IPC
  - Rows without `Clip Directory` are bin/folder entries and must be skipped
  - 22,017 / 23,293 clips have keywords (94.5% coverage)
  - Fast enough to run synchronously on demand — no background threading needed

  **M1.2 ✅** — CSV parser (`search_index.parse_export_csv` / `parse_export_csv_text`).

  **M1.3 ✅** — SQLite schema + FTS5 index.
  - `build_index(db_path, clips)` — creates `clips` + `clips_fts` (FTS5 content table) + `meta`; full rebuild on each call
  - `get_status(db_path)` → `{state, clip_count, built_at}`
  - `resolve_api.export_metadata(resolve, csv_path)` wraps `media_pool.ExportMetadata`
  - `GET /search/api/status` and `POST /search/api/build` routes live in `app.py`
  - DB written to `search.db` next to `app.py` (gitignored)

**M2 — Search API**
- `GET /search/api/clips?q=sunset+corsica` → paginated results with clip
  metadata and match score
- `GET /search/api/keywords` → full keyword list for browse/autocomplete
- Full-text search via SQLite FTS5

**M3 — Search UI**
- New page at `/search`
- Search input with autocomplete from keyword list
- Result grid: thumbnail + name + date + keyword tags
- Keyword tags clickable (add to search query)
- "Rebuild index" button with last-built timestamp

**M4 — Jump to Resolve**
- Click result → `POST /search/api/select` `{clip_id}` → server calls
  `SetCurrentFolder` + `SetSelectedClip`
- Status feedback in UI (selected / error)

**M5 — Smart search (stretch)**
- "Find similar to current clip" — takes the tagger's current clip keywords
  and finds the closest matches in the index
- Co-occurrence ranking: clips that share multiple keywords with your query
  rank higher than clips with just one match
- Natural language query via Ollama (llama3): parse "outdoor clips from
  Corsica with people" into keyword filters
