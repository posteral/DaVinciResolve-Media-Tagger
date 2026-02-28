# DaVinci Resolve AI Keywording Agent

Python agent for reading and writing keyword metadata on clips in DaVinci Resolve, with an AI-assisted pipeline planned for later iterations.

## Current Status

- `v0.6.1` is the current release.
- Browser-based UI (`app.py`) for reading, editing, and receiving keyword suggestions for the selected clip.

## Requirements

- Python 3.10+
- DaVinci Resolve installed and running
- External scripting enabled in Resolve (`Preferences → System → General`)
- `ffmpeg` on your system (used to extract the thumbnail frame):

```bash
brew install ffmpeg
```

- Python dependencies:

```bash
pip install -r requirements.txt
```

## Run

```bash
python app.py
```

Then open `http://localhost:5000` in your browser.

## What It Does

- Connects to an open DaVinci Resolve instance through the official scripting API.
- Finds the selected clip from the current timeline video item, falling back to the Media Pool selection.
- Displays a thumbnail of the clip (extracted from the proxy file if available, otherwise the original), the clip name, and its keywords in the browser.
- **Refresh** fetches live data from Resolve without reloading the page.
- Each keyword tag has a × button; clicking it opens an inline confirmation modal.
- **Remove** deletes the keyword from the list; a **Save** button then appears.
- **Save** writes the updated keyword list back to Resolve, with a brief "Saved" confirmation on success.
- **Prev / Next** buttons navigate between clips in the current Media Pool folder, ordered by Date Created.
- **Suggested** keywords appear as purple buttons below the keyword list. Up to 3 keywords are recommended based on frequency across the 10 temporally closest clips in the same folder. Clicking a suggestion adds it to the pending list; Save writes it to Resolve.

## API

`GET /api/clip` — returns the selected clip name and keywords:

```json
{"clip": "A001_C003_0215AB", "keywords": ["interview", "city", "night"]}
```

`GET /api/clip/thumbnail` — returns a PNG thumbnail of the selected clip (`204` if unavailable).

`GET /api/clip/suggestions` — returns up to 3 keyword suggestions based on neighbouring clips:

```json
{"suggestions": ["city", "night", "interview"]}
```

`POST /api/clip/keywords` — writes an updated keyword list to the selected clip:

```json
{"keywords": ["interview", "city"]}
```

## Tests

```bash
python3 -m unittest test_resolve_api -v
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
