# DaVinci Resolve AI Keywording Agent

Python agent for reading and writing keyword metadata on clips in DaVinci Resolve, with an AI-assisted pipeline planned for later iterations.

## Current Status

- `v0` is implemented and working.
- Current behavior is read-only for one selected clip and terminal output.

## v0 (Implemented)

### What It Does

- Connects to an open DaVinci Resolve instance through the official scripting API.
- Finds the selected clip from the current timeline video item first.
- Falls back to the Media Pool selection if needed.
- Reads keyword metadata from the clip.
- Prints the clip name and keyword list, or `(none)` when empty.

### Requirements

- Python 3.10+
- DaVinci Resolve installed
- DaVinci Resolve running
- External scripting enabled in Resolve

### Run

```bash
python3 main.py
```

### Example Output

```text
Clip: A001_C003_0215AB
Keywords:
- interview
- city
- night
```

If no keywords are set:

```text
Clip: A001_C003_0215AB
Keywords: (none)
```

If no clip is selected:

```text
No selected clip found.
Select a clip in the timeline (or media pool) and run again.
```

## v0.1 (Next Iteration)

Goal: move from read-only to safe metadata updates for the selected clip.

Planned scope:

- Add CLI modes:
  - `--read` (existing behavior)
  - `--set "k1,k2"`
  - `--append`
  - `--replace`
  - `--dry-run`
  - `--json`
- Implement deterministic merge and dedupe policy for keywords.
- Preserve manual keywords by default unless `--replace` is explicitly requested.
- Add explicit, user-friendly error states for connection and selection failures.
- Add unit tests using mocked Resolve objects for read, merge, and write flows.

## Target Final Version (v1 Vision)

The intended end-state is an AI-assisted keywording workflow that:

- Pulls clip context from Resolve and project metadata.
- Generates suggested keywords using an LLM pipeline.
- Applies decision logic and confidence thresholds before write-back.
- Writes metadata safely with traceable logs and configurable behavior.
- Supports both single-clip and batch processing modes.
- Uses a clear configuration model for providers, prompts, and policies.

## Changelog

Project history and planned milestones are tracked in [`CHANGELOG.md`](CHANGELOG.md).
