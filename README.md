# DaVinci Resolve Media Tagger

A browser-based tool for fast, assisted keyword tagging of clips in DaVinci Resolve. It runs alongside Resolve and connects via the official scripting API, letting you navigate clips, apply keywords from multiple suggestion sources, and write them back to the media pool — all without touching Resolve's UI.

**Current release: v0.22.4**

---

## What it does

Resolve's built-in keyword tagging is slow: you type every keyword manually, one clip at a time, with no context from neighbouring clips. This tool fixes that.

Open the app in a browser while Resolve is running. It shows the current clip's thumbnail (filmstrip), keywords, and several suggestion panels. You navigate with arrow keys, click suggestions to stage them, and press `s` to save. Keywords are written directly to Resolve's media pool metadata.

**Suggestion sources:**

- **Proximity (purple)** — keywords from adjacent clips in the folder, ranked by inverse-distance weighting. Clips immediately next to the current one rank highest.
- **AI (teal)** — a locally-running VLM (llava via Ollama) analyses a midpoint frame and suggests up to 10 keywords.
- **Identity recognition** — face detection runs on proxy frames, clusters detections within the clip, and matches against a local registry of named people. Confirmed identities are added as keywords and remembered for future clips.

**Other features:**

- **Filmstrip** — 5 frames at 10/30/50/70/90% of the clip shown as a film strip for visual context
- **Free-text input** with autocomplete from the full project keyword catalog
- **Pinned keywords** highlighted in blue in the tag list and suggestion panels
- **Proxy-only policy** — all ffmpeg operations use the proxy file; original media is never read
- **Session counter** — tracks how many distinct clips you've tagged in the current session

---

## Requirements

- Python 3.10+
- DaVinci Resolve installed and running with external scripting enabled:
  `Preferences → System → General → External scripting using`
- `ffmpeg`:

```bash
brew install ffmpeg
```

- [Ollama](https://ollama.com) with the `llava` model (required for AI suggestions; the rest of the UI works without it):

```bash
brew install ollama
ollama pull llava
```

- Python dependencies:

```bash
pip install -r requirements.txt
```

- `face_recognition` (optional — required for identity recognition; needs dlib):

```bash
pip install face_recognition
```

---

## Setup

### Pinned keywords

Copy `keywords_config.template.json` to `keywords_config.json` and list the keywords you want highlighted in blue:

```json
{
  "pinned_keywords": ["Alice", "Bob", "My Place"]
}
```

`keywords_config.json` is gitignored — your personal keywords are never committed.

---

## Run

Start Ollama if you want AI suggestions:

```bash
ollama serve
```

Start the app:

```bash
python app.py
```

Open `http://localhost:5001` in your browser.

---

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| `←` or `↑` | Previous clip |
| `→` or `↓` | Next clip |
| `s` | Save |

Shortcuts are active whenever focus is not in a text input.

---

## Proxy-only policy

All ffmpeg operations (filmstrip, AI suggestion frames, face detection) use the `Proxy Media Path` of each clip. If no proxy is available, the filmstrip shows "No proxy available" and AI/identity features are skipped. Original camera files are never read.

---

## API

| Method | Route | Description |
|--------|-------|-------------|
| `GET` | `/api/clip` | Selected clip name, keywords, proxy path, proximity suggestions |
| `GET` | `/api/clip/thumbnail?path=` | PNG thumbnail (`204` if unavailable) |
| `GET` | `/api/clip/filmstrip?path=` | All 5 filmstrip frames as base64 JSON |
| `GET` | `/api/clip/suggestions` | Up to 12 proximity-based keyword suggestions |
| `POST` | `/api/clip/ai-suggestion` | Up to 10 AI keyword suggestions from llava |
| `POST` | `/api/clip/keywords` | Write updated keyword list to selected clip |
| `POST` | `/api/clip/navigate` | Navigate to next/previous clip |
| `POST` | `/api/clip/detect-identities` | Run face detection on proxy frames |
| `GET` | `/api/clip/face-crop?token=` | JPEG face crop for a given face token |
| `GET` | `/api/identities` | List of known identities in the local registry |
| `POST` | `/api/identities/confirm` | Commit identity assignments, update registry |
| `GET` | `/api/config/pinned-keywords` | Pinned keywords from `keywords_config.json` |
| `GET` | `/api/profiler/report` | Live session performance stats |
| `POST` | `/api/profiler/dump` | Write profiling report to a JSON file |

---

## Fixing existing metadata (keyword cleanup)

If your project already has clips with malformed keywords (leading/trailing spaces,
duplicates, unsorted order) — visible as duplicate Smart Bins in Resolve — run the
bulk-fix script on an exported metadata CSV:

1. **Export** from Resolve: `File → Export → Metadata...` → save as a CSV file.

2. **Run the script:**

```bash
.venv/bin/python3 scripts/fix_metadata_keywords.py "/path/to/export.csv"
```

This writes a `export FIXED.csv` next to the original. You can also specify an
explicit output path:

```bash
.venv/bin/python3 scripts/fix_metadata_keywords.py input.csv output.csv
```

The script strips whitespace, removes duplicates, and sorts keywords alphabetically
— identical to what the tagger does on every save.

3. **Import** back into Resolve: `File → Import → Timeline Markers / Metadata...`
   → select the `FIXED` file.

The original file is never modified.

---

## Tests

```bash
python3 -m unittest discover -s tests -v
```

308 tests, all passing.

---

## Changelog

Full history and planned milestones: [`CHANGELOG.md`](CHANGELOG.md)
