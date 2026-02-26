# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

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
