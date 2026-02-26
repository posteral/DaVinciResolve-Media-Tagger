# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Planned

- `v0.1` safe keyword write-back for selected clip.
- CLI modes: `--read`, `--set`, `--append`, `--replace`, `--dry-run`, `--json`.
- Deterministic keyword merge and dedupe behavior.
- Unit tests with mocked DaVinci Resolve scripting objects.

### Documentation

- Expanded `README.md` with roadmap and target final version.

## [0.0.1] - 2026-02-26

### Added

- `main.py` v0 implementation that:
  - connects to DaVinci Resolve,
  - resolves selected clip from timeline or Media Pool,
  - reads keywords metadata,
  - prints clip name and keywords in terminal.
- Initial project `README.md` with usage and scope.
