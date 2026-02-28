# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

## [0.14.0] - 2026-02-28

### Changed

- Proximity keyword suggestions now use **inverse-distance weighting** instead
  of raw frequency. Each neighbouring clip contributes `1 / distance` to the
  score of every keyword it carries, where distance is the sequential position
  gap in the folder (sorted by Date Created). A clip immediately adjacent scores
  1.0; two away scores 0.5; three away 0.33, etc. Keywords that appear both
  before and after the current clip now rank higher than keywords that appear
  many times only at the far end of the shoot day.

## [0.13.0] - 2026-02-28

### Changed

- AI keyword suggestion prompt now includes the clip's file path as context.
  Everything preceding and including `ProxyMedia/` is stripped so only the
  meaningful portion (shoot folder, date, filename) is sent to the model.
  The model can now use shoot date, location hints, and folder structure
  alongside the visual frames to ground its suggestions.

## [0.12.2] - 2026-02-28

### Fixed

- Apply button stayed disabled when navigating to a new clip — `_resetIdentityPanel()`
  now resets `disabled`, `textContent`, and `onclick` on every clip load.
- `onclick` attribute on a `<button disabled>` fires in all browsers; replaced with
  `apply.onclick` assigned in `renderIdentityCards` and a `disabled` guard at the
  top of `applyIdentities()`.
- `confirm_identities` returned duplicate entries in `keywords_added` when the same
  person was detected in multiple face clusters — now deduplicates case-insensitively.

## [0.12.1] - 2026-02-28

### Changed

- Test files moved into `tests/` directory. CI and README updated accordingly.

## [0.12.0] - 2026-02-28

### Added

- **Identity recognition for recurring people.** When a clip with a proxy is
  loaded, the app samples 5 frames, detects faces, clusters repeated
  appearances, and presents a **Detected Identities** panel to the right of
  the keyword card.
- Each detected person appears as a card with a face crop thumbnail, a status
  badge (known / low confidence / unknown), an assign input pre-filled when a
  match is found, and an "Add as keyword" toggle (on by default).
- **Apply identities to keywords** button commits confirmed names to the local
  identity registry and appends them to the clip's keyword list. Status badges
  update to green "known" after applying.
- Editing a name after applying re-enables the Apply button so corrections can
  be committed immediately.
- Local identity registry (`identity_registry.json`) stores face embeddings per
  person (FIFO cap of 20). The registry grows over time — once a person is
  named once, they are recognised automatically on future clips.
- `identity_recognition.py`: face detection via `face_recognition` (lazy
  import — app works without it), greedy intra-clip clustering, known /
  low-confidence / unknown matching at 0.55 / 0.70 distance thresholds.
- `identity_registry.py`: atomic JSON persistence with `.bak` copy, add /
  update identities, face crop storage.
- 4 new API routes: `POST /api/clip/detect-identities`,
  `GET /api/clip/face-crop`, `GET /api/identities`,
  `POST /api/identities/confirm`.
- 101 unit tests (46 new across `test_identity_registry`,
  `test_identity_recognition`, `test_identity_routes`).

### Requirements

```bash
pip install face_recognition  # requires dlib
```

## [0.11.1] - 2026-02-28

### Fixed

- `get_keywords()` now deduplicates keywords case-insensitively before
  returning. Clips where Resolve has stored the same keyword twice will show
  the Save button automatically on Refresh, allowing a one-click fix.

## [0.11.0] - 2026-02-28

### Added

- Loading spinner next to the **AI Suggested** label while suggestions are
  being fetched from Ollama. Disappears when results arrive or the section
  collapses.
- `no_proxy` flag in `/api/clip` and `/api/clip/navigate` responses.

### Changed

- All ffmpeg calls (thumbnail extraction and AI suggestion frames) now use
  the proxy file (`Proxy Media Path`) only. The original media file
  (`File Path`) is never read, preventing accidental access to slow original
  media drives.
- When no proxy exists the thumbnail area shows **"No proxy available"** and
  the AI suggestion section is skipped entirely.

## [0.10.0] - 2026-02-28

### Changed

- AI keyword suggestions now sample 5 frames at 10/30/50/70/90% of the clip
  duration instead of a single midpoint frame, giving the VLM a broader view
  of the clip's content. All frames are extracted in parallel via
  `ThreadPoolExecutor` so latency is unchanged relative to the single-frame
  approach.

### Added

- `frames_from_file_path(file_path, percentages)` in `resolve_api.py`: extracts
  multiple frames at specified positions using parallel ffmpeg calls.
- `_probe_duration()` and `_extract_frame()` extracted as shared helpers used by
  both `thumbnail_from_file_path` and `frames_from_file_path`.
- 6 unit tests in `TestFramesFromFilePath`: returns 5 frames, seeks at correct
  percentages, fallback to single frame when duration unknown, skips failed
  frames, empty when ffmpeg not found.

## [0.9.0] - 2026-02-28

### Added

- Keyboard shortcuts: **← / ↑** previous clip, **→ / ↓** next clip,
  **s** save. Suppressed when focus is inside a text input. Body
  regains focus automatically after every navigate/refresh so shortcuts
  work immediately without clicking the page.

### Fixed

- Autocomplete dropdown no longer shows bullet points.
- Autocomplete pre-selects the first item; Enter commits it instantly.
- Autocomplete retries the catalog fetch until data arrives — previously
  an empty response (background build not yet done) permanently disabled
  the dropdown for the session.
- Folder cache is invalidated and rebuilt in the background after every
  Save, so proximity suggestions stay accurate without blocking the next
  navigate press.
- Per-clip date and keyword data cached in `_folder_cache` — eliminates
  34+ `GetMetadata` IPC calls per navigate press on large same-day
  folders; cache is shared between `navigate_clip` and
  `suggest_keywords`.
- `/api/clip` (Refresh) now also warms the folder cache and returns
  `file_path` + `suggestions` in the response, matching the navigate
  response shape.

## [0.8.0] - 2026-02-28

### Added

- Free-text **Add keyword** input at the bottom of the clip view.
  While typing, a dropdown shows up to 8 matching keywords from the
  project (case-insensitive substring match, already-applied keywords
  excluded). Arrow keys navigate the list; Enter or click a suggestion
  commits the keyword via the existing Save flow; Escape dismisses.
- `get_all_project_keywords(resolve)` in `resolve_api.py`: walks the
  full Media Pool tree (root folder + all subfolders recursively) and
  returns a sorted, deduplicated keyword list. No new pip dependencies.
- `GET /api/keywords/catalog`: returns the project keyword catalog.
  Built on the first request, then refreshed in the background after
  every successful Save so newly added keywords appear immediately.

### Performance

- Keyword catalog rebuild runs in a background daemon thread that
  acquires `_resolve_lock` with a 0.1 s timeout, yielding to
  interactive requests if the lock is busy. Saves no longer block
  waiting for the full project tree walk.
- `_get_sorted_clips(folder)`: caches the date-sorted clip list keyed
  by `(folder_name, clip_count)`. Consecutive Next/Prev presses in the
  same folder reuse the cache instead of calling `GetClipProperty`
  on every clip again.
- Navigate route now gathers `file_path` and proximity suggestions
  inside its single lock acquisition and returns them in the response.
  The browser no longer fires separate `/api/clip/suggestions` and
  `/api/clip/ai-suggestion` IPC requests after each navigation,
  eliminating the post-navigate lock contention that caused 7–9 s delays.
- `/api/clip/thumbnail` and `/api/clip/ai-suggestion` accept a `?path=`
  query param; when the path is already known (from the navigate
  response) they skip `_resolve_lock` entirely.

### Fixed

- `UnboundLocalError` for `_catalog_refresh_pending` in `set_keywords`
  caused by missing `global` declaration.

## [0.7.6] - 2026-02-28

### Added

- Keywords are now displayed and written back to Resolve in
  case-insensitive alphabetical order, making the order deterministic
  for any given set of keywords.
- Save button appears automatically on clip load when the stored keyword
  order in Resolve differs from alphabetical — allows one-click
  re-sorting of existing clips without making any other change.

### Fixed

- Save button now hides immediately after a successful save.

### Performance

- Removed `all_clip_dates` debug field from the `[suggestions]` log
  line; it was calling `_clip_date_key` on every clip a second time,
  making the suggestions route slow on large folders.

## [0.7.5] - 2026-02-28

### Fixed

- Proximity suggestions now correctly handle clips where Resolve returns
  `Date Created` in the format `Sat Sep 28 2024 19:35:21`
  (`%a %b %d %Y %H:%M:%S`). Previously these clips silently fell back to
  `datetime.max` and produced no suggestions.
- Clips with no parseable `Date Created` are now excluded from the
  same-day neighbour pool rather than falsely matching each other via
  `datetime.max`.

### Changed

- `suggest_keywords` now returns a `(suggestions, debug)` tuple; the
  debug dict is printed to stdout as `[suggestions]` on every request,
  showing the current clip's parsed date, total clips in folder, matched
  same-day neighbours, and all clip dates — useful for diagnosing missing
  suggestions.

## [0.7.4] - 2026-02-28

### Changed

- Proximity keyword suggestions now consider all clips in the current folder
  recorded on the same calendar day as the current clip, instead of a fixed
  window of 10 nearest clips by index. This produces more relevant suggestions
  when a shoot day contains many clips.

## [0.7.3] - 2026-02-28

### Added

- Up to 3 AI keyword suggestions returned in a single Ollama call. The model
  is asked for 3 comma-separated keyword phrases; results are parsed,
  normalised, and deduplicated against existing keywords and against each
  other. The UI renders up to 3 editable rows under "AI Suggested".

### Changed

- AI suggestion is now editable before being staged: each suggestion appears
  as a pre-filled text input alongside a `+ Add` button. The keyword can be
  corrected (capitalisation, wording) before being added to the pending list.
  Enter key also triggers Add.
- `ai_suggest_keyword` renamed to `ai_suggest_keywords`; returns `list[str]`
  instead of `str | None`. Route response key changed from `suggestion` to
  `suggestions`.

### Fixed

- AI suggestion is now suppressed when the model returns a keyword already
  present on the clip (case-insensitive check after normalisation).

## [0.7.0] - 2026-02-28

### Added

- AI keyword suggestion via Ollama VLM: a teal "AI Suggested" button appears
  below the proximity suggestions after each Refresh or clip navigation,
  showing a single keyword phrase generated by the llava model from the
  clip's thumbnail frame. Runs entirely locally — no API key, no cloud.
- `ai_suggest_keyword(file_path, model="llava", existing_keywords=None)` in
  `resolve_api.py`: reuses the existing thumbnail bytes, base64-encodes them,
  and POSTs to Ollama's local `/api/generate` endpoint using stdlib `urllib`.
  Passes the clip's existing keywords as context so the model suggests a
  complementary keyword rather than duplicating what is already there.
  Returns `None` if Ollama is unreachable or no thumbnail is available,
  keeping the section hidden without affecting the rest of the UI.
- `_normalise_ai_keyword(text, existing_keywords)`: post-processes the VLM
  response to match the established casing convention — lowercase for generic
  subjects, Title Case for proper nouns. Casing is derived from the clip's
  own existing keywords: single-word proper nouns are matched word-by-word;
  multi-word proper nouns are matched as a full phrase only.
- `GET /api/clip/ai-suggestion` route in `app.py`: retrieves the file path
  and existing keywords under the Resolve lock, then calls
  `ai_suggest_keyword` outside the lock (same pattern as the thumbnail route).
- Teal `.ai-suggestion-btn` CSS class alongside the existing purple
  `.suggestion-btn`.
- Debug log line `[ai-suggestion]` printed to stdout on every request,
  showing the file path, existing keywords, and the suggestion returned.
- Unit tests: `TestAiSuggestKeyword` (happy path, Ollama unreachable,
  thumbnail unavailable, empty response, existing keywords in prompt) and
  `TestNormaliseAiKeyword` (generic lowercasing, single-word proper noun
  restoration, multi-word phrase restoration, partial phrase stays lowercase).

### Changed

- VLM switched from moondream to llava for significantly more coherent
  scene descriptions on real-world footage.

## [0.6.1] - 2026-02-28

### Removed

- `merge_keywords()` and `_dedupe_preserve_order()` deleted from `resolve_api.py`.
  Both were written for the v0.1 CLI which was removed in v0.3.3; no production
  code called them. Their 12 unit tests removed from `test_resolve_api.py`.

## [0.6.0] - 2026-02-28

### Added

- Keyword suggestion UI: up to 3 purple "Suggested" buttons appear below
  the keyword list after each Refresh or clip navigation, showing the most
  frequently used keywords on temporally adjacent clips in the same folder.
- `suggest_keywords(resolve, n_neighbours=10)` in `resolve_api.py`: collects
  keywords from the N closest clips by index in a date-sorted folder list,
  counts frequencies, excludes keywords already on the current clip
  (case-insensitive), and returns the top 3 by frequency.
- `GET /api/clip/suggestions` route in `app.py`: returns
  `{"suggestions": [...]}`.
- Clicking a suggestion button adds the keyword to the pending list and
  disables the button; the existing Save flow writes it to Resolve.
- Suggestions section is hidden entirely when no suggestions are available;
  failures are silently ignored (best-effort).
- 5 unit tests in `TestSuggestKeywords`: top-3 ranking, current-keyword
  exclusion, empty result when neighbours have no keywords, empty result
  when no clip is selected, and fewer-than-3 candidates.

## [0.5.0] - 2026-02-26

### Added

- Prev / Next buttons in the web UI for navigating between clips in the
  current Media Pool folder without touching Resolve.
- `navigate_clip(resolve, direction)` added to `resolve_api.py`; uses
  `MediaPool.GetCurrentFolder()`, `Folder.GetClipList()`, and
  `MediaPool.SetSelectedClip()` to move to the adjacent clip by index.
- `POST /api/clip/navigate` — accepts `{"direction": "next"|"prev"}`;
  returns the new clip name and keywords (200), or `{"error": "No more
  clips"}` (404) when already at a boundary.
- Nav buttons are hidden on initial load and revealed after the first
  successful Refresh. They are disabled for the duration of the request
  to prevent double-clicks.
- A brief "No more clips" flash message is shown (and then cleared) when
  navigating past the boundary instead of a persistent error box.

### Fixed

- Prev/Next navigation now steps through clips in the same order shown
  in the Media Pool UI when sorted by Date Created. `GetClipList()`
  returns clips in internal insertion order, not UI order; clips are now
  sorted by `GetClipProperty("Date Created")` before navigating.
  Handles `MM/DD/YYYY`, `YYYY-MM-DD`, and `DD/MM/YYYY` formats; falls
  back to name sort if the date string is absent or unparseable.

## [0.4.1] - 2026-02-26

### Added

- 8 unit tests for `thumbnail_from_file_path()` covering: success,
  ffmpeg failure, empty output, empty file path, ffmpeg not found,
  midpoint seek, zero seek on probe failure, and subprocess exception.

### Changed

- `import subprocess` moved to module level in `resolve_api.py` so it
  can be patched cleanly in tests.

## [0.4.0] - 2026-02-26

### Added

- Clip thumbnail displayed in the web UI above the clip name, served as
  PNG from `GET /api/clip/thumbnail`. Refreshes alongside clip data.
  Uses the proxy file (`Proxy Media Path`) when available, otherwise
  falls back to the original (`File Path`). A mid-point frame is
  extracted via `ffmpeg`. Shows "No thumbnail available" placeholder
  when no file path is available or ffmpeg fails.
- `thumbnail_from_file_path()` added to `resolve_api.py`; uses ffmpeg
  to extract a frame with no Resolve IPC involved.
- `GET /api/clip/thumbnail` fetched via `fetch()` in JS rather than a
  direct `<img src>`, so a `204 No Content` response reliably shows the
  placeholder instead of leaving the image in a broken loading state.

### Fixed

- Resolve scripting API is not thread-safe: all IPC calls are now
  serialised through a single `threading.Lock` in `app.py`, with one
  cached `resolve` object reused across requests.
- `_get_resolve()` was acquiring the lock inside itself while all
  callers already held it — a re-entrant deadlock that hung every
  request. Fixed by making `_get_resolve()` lock-free and requiring
  callers to hold the lock.
- Flask runs with `threaded=True` so the thumbnail route (which runs
  ffmpeg outside the lock) cannot block `/api/clip`.

## [0.3.3] - 2026-02-26

### Removed

- `main.py` — CLI entry point deleted; the web UI (`app.py`) is the only entry point.
- `format_output()` and `_error()` removed from `resolve_api.py` (CLI-only helpers with no remaining callers).
- `test_main.py` replaced by `test_resolve_api.py`; CLI test class (`TestMain`) and `TestFormatOutput` removed.

## [0.3.2] - 2026-02-26

### Changed

- Extracted Resolve scripting API functions into `resolve_api.py`; `main.py` is now CLI-only.
- `app.py` now imports from `resolve_api` directly.

## [0.3.1] - 2026-02-26

### Fixed

- × button on keyword tags did not fire — inline `onclick` with `JSON.stringify` produced unescaped quotes in the HTML attribute, which the browser silently rejected. Replaced `innerHTML` string-building with DOM element construction and `addEventListener`.

## [0.3.0] - 2026-02-26

### Added

- `POST /api/clip/keywords` — writes an updated keyword list back to the selected clip in Resolve.
- Keyword tags in the UI now show a × button; clicking it opens an inline confirmation modal.
- Inline confirmation modal shows the keyword name with Cancel / Remove actions.
- Save button (hidden until a keyword is removed) posts the current keyword list to Resolve and shows a brief "Saved" confirmation.
- `renderKeywords()` extracted so both `refresh()` and the modal confirm path redraw just the keyword list.

## [0.2.1] - 2026-02-26

### Fixed

- "Current Status" section now correctly reflects v0.2 as the current release.
- No-clip-selected error example corrected to match actual stderr output (`ERROR: ...`).
- v0.2 requirements section is now self-contained instead of referencing v0.

## [0.2.0] - 2026-02-26

### Added

- `app.py` — Flask web server with two routes:
  - `GET /` — serves the browser UI.
  - `GET /api/clip` — returns the selected clip name and keywords as JSON.
- `templates/index.html` — minimal browser UI with a Refresh button, loading state, keyword tag list, and error display; no external dependencies.
- `requirements.txt` — pins `flask>=3.0`.

## [0.1.0] - 2026-02-26

### Added

- CLI argument support via `argparse`:
  - `--set "k1,k2"` — replace all keywords on the selected clip.
  - `--replace` — alias for `--set`.
  - `--append "k1"` — add keywords to existing ones.
  - `--dry-run` — preview the result without writing to Resolve.
  - `--json` — machine-readable JSON output for all modes.
- `set_keywords()` — writes keyword metadata back to Resolve via `SetMetadata`.
- `merge_keywords()` — deterministic merge with append/set/replace modes.
- `_dedupe_preserve_order()` — case-insensitive deduplication, first occurrence wins.
- `format_output()` — unified human-readable and JSON output formatting.
- `test_main.py` — 39 unit tests covering all functions and CLI paths using mocked Resolve objects.

### Changed

- `main()` rewritten to wire CLI args, merge logic, write-back, and error handling.
- Error messages now go to stderr and respect `--json` format.
- `README.md` updated with CLI usage examples and keyword policy documentation.

## [0.0.1] - 2026-02-26

### Added

- `main.py` v0 implementation that:
  - connects to DaVinci Resolve,
  - resolves selected clip from timeline or Media Pool,
  - reads keywords metadata,
  - prints clip name and keywords in terminal.
- Initial project `README.md` with usage and scope.
