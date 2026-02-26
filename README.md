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

## v0.1 (Implemented)

Goal: move from read-only to safe metadata updates for the selected clip.

### CLI Modes

```bash
# Read keywords (default)
python3 main.py

# Set keywords, replacing all existing
python3 main.py --set "interview, city, night"

# Append keywords to existing ones
python3 main.py --append "extra, tag"

# Replace all keywords (alias for --set)
python3 main.py --replace "final"

# Preview what would be written without writing
python3 main.py --set "a, b" --dry-run

# Machine-readable JSON output
python3 main.py --json
python3 main.py --set "a, b" --dry-run --json
```

### Keyword Policy

- Keywords are deduplicated case-insensitively; first occurrence wins.
- `--append` preserves existing keywords and adds new ones.
- `--set` / `--replace` discard existing keywords and set new ones.
- `--dry-run` shows the result without writing anything to Resolve.

### Tests

```bash
python3 -m unittest test_main -v
```

## v0.2 (Implemented)

Goal: browser-based read-only UI for the selected clip's name and keywords.

### What It Does

- Starts a local Flask server on `http://localhost:5000`.
- Displays the currently selected clip name and its keywords in the browser.
- "Refresh" button fetches live data from Resolve without reloading the page.
- Shows a loading state while fetching and an error message if no clip is selected or Resolve is not running.

### Requirements

All v0 requirements, plus:

```bash
pip install flask
```

Or use `requirements.txt`:

```bash
pip install -r requirements.txt
```

### Run

```bash
python app.py
```

Then open `http://localhost:5000` in your browser and click **Refresh**.

### API

`GET /api/clip` — returns JSON:

```json
{"clip": "A001_C003_0215AB", "keywords": ["interview", "city", "night"]}
```

On error:

```json
{"error": "No clip selected"}
```

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
