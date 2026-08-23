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
- Full-text keyword search: type "sunset coastline" → clips tagged with both.
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

**M2 — Search API** ✅
- `GET /search/api/query?q=&limit=&offset=` → paginated FTS5 results
- Full-text search via SQLite FTS5 with prefix matching
- Quoted phrase support (`"rolling hills"`), exclusion syntax (`-Alex`)
- Results sorted: Good Takes first, then date descending

**M3 — Search UI** ✅
- New page at `/search` with dark theme matching tagger
- Debounced search input; quoted phrase + `-exclusion` syntax supported
- Result cards: filename, Good Take badge, clip dir, keyword tags, date
- Keyword tags: click to add (green), click again to exclude (red), click again to remove
- "Rebuild index" button with project name + last-built timestamp
- Load more pagination (50 per page)
- `Shot Finder →` link in tagger header; `← Tagger` link in Shot Finder

**M4 — Jump to Resolve** ✅
- Click any result row → `POST /search/api/select` `{file_name, clip_dir}`
- Server walks media pool tree to find clip, calls `SetCurrentFolder` + `SetSelectedClip`
- Row highlights green and shows "✓ Selected in Resolve" (sticks until next selection)
- Red border + error message if Resolve unavailable or clip not found

**M5 — Smart search (stretch)**
- "Find similar to current clip" — takes the tagger's current clip keywords
  and finds the closest matches in the index
- Co-occurrence ranking: clips that share multiple keywords with your query
  rank higher than clips with just one match
- Natural language query via Ollama (llama3): parse "outdoor clips from
  the coast with people" into keyword filters

---

## Keyword Tree (`/tree` or Shot Finder panel)

A co-occurrence drill-down navigator for the full archive. Lets you narrow
from 23k clips to a handful by clicking through keyword combinations, then
jump directly to the matching clips in Resolve.

### Core idea

Every keyword becomes a node showing how many clips carry it. Clicking a node
**adds it to the active filter set** — the tree re-renders showing only
keywords that co-occur with everything selected so far, and the clip count
updates. Clicking an active filter removes it (drill back up). When the result
set is small enough, a "Select in Resolve" CTA jumps to those clips.

### Example flow

1. Tree root: all 23,293 clips, all keywords listed by frequency.
2. Click **France** → filter set: {France}, 847 clips.
   Tree now shows only keywords that co-occur with France (Paris, Alex,
   the coast, …), each with their France-filtered count.
3. Click **Paris** → filter set: {France, Paris}, 312 clips.
4. Click **Alex** → filter set: {France, Paris, Alex}, 41 clips.
5. Click **Select in Resolve** → server selects those 41 clips in the
   media pool (or opens the matching Smart Bin if one exists).

### Architecture

**Data source**
- Uses the same `search.db` SQLite index built by Shot Finder M1.
- No additional index needed: co-occurrence is computed at query time via
  `SELECT DISTINCT clip_id FROM clip_keywords WHERE keyword IN (...)`.
- `clip_keywords` is a normalised junction table (one row per clip+keyword)
  added to the schema in this milestone.

**API**
- `GET /tree/api/nodes?kw=France,Paris` → list of `{keyword, clip_count}`
  for all keywords that co-occur with the current filter set, sorted by
  clip_count desc.
- `GET /tree/api/clips?kw=France,Paris,Alex` → list of matching
  `{clip_id, file_name, clip_dir, keywords}` (capped at 200).
- Reuses `POST /search/api/select` (M4) for the jump-to-Resolve CTA.

**UI**
- Panel (location TBD — Shot Finder sidebar or standalone `/tree` page).
- Active filter set shown as removable chips at the top.
- Keyword list below: each row shows keyword + clip count as a bar/badge.
  Clicking adds to filter; active keywords highlighted.
- Clip count and "Select in Resolve" CTA update live as filters change.
- "Clear all" resets to root.

### Milestones

**T1 — clip_keywords junction table**
- Extend `build_index` to populate a `clip_keywords(clip_id, keyword)`
  table from the existing `clips` rows.
- `GET /tree/api/nodes?kw=...` endpoint.

**T2 — clip list endpoint + Select in Resolve**
- `GET /tree/api/clips?kw=...` endpoint.
- Reuse or extend `POST /search/api/select` to accept a list of clip IDs.

**T3 — UI**
- Filter chip bar + keyword list panel.
- Live clip count + "Select in Resolve" CTA.
- Decision on placement (Shot Finder panel vs standalone page) deferred
  until M3 Shot Finder UI is built.
