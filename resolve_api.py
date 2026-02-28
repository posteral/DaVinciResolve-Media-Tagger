from __future__ import annotations

import subprocess
import sys
import os
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


def _load_source(module_name: str, file_path: str) -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def get_resolve() -> Any:
    try:
        import DaVinciResolveScript as dvr_script
    except ImportError:
        if sys.platform.startswith("darwin"):
            module_dir = Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules")
        elif sys.platform.startswith(("win", "cygwin")):
            program_data = os.getenv("PROGRAMDATA", r"C:\ProgramData")
            module_dir = Path(program_data) / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "Developer" / "Scripting" / "Modules"
        elif sys.platform.startswith("linux"):
            module_dir = Path("/opt/resolve/Developer/Scripting/Modules")
        else:
            raise RuntimeError(f"Unsupported platform for Resolve scripting: {sys.platform}")

        module_file = module_dir / "DaVinciResolveScript.py"
        if not module_file.exists():
            raise RuntimeError(
                "Could not find DaVinciResolveScript.py. "
                f"Expected at: {module_file}"
            )

        _load_source("DaVinciResolveScript", str(module_file))
        import DaVinciResolveScript as dvr_script

    resolve = dvr_script.scriptapp("Resolve")
    if resolve is None:
        raise RuntimeError(
            "Unable to connect to DaVinci Resolve. "
            "Make sure Resolve is open and External Scripting is enabled."
        )
    return resolve


def _as_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [v for v in value.values() if v is not None]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def get_selected_media_pool_item(resolve: Any) -> Any | None:
    project_manager = resolve.GetProjectManager()
    if project_manager is None:
        return None

    project = project_manager.GetCurrentProject()
    if project is None:
        return None

    timeline = project.GetCurrentTimeline()
    if timeline is not None:
        timeline_item = timeline.GetCurrentVideoItem()
        if timeline_item is not None:
            media_pool_item = timeline_item.GetMediaPoolItem()
            if media_pool_item is not None:
                return media_pool_item

    media_pool = project.GetMediaPool()
    if media_pool is None:
        return None

    selected_clips = _as_sequence(media_pool.GetSelectedClips())
    return selected_clips[0] if selected_clips else None


def _normalize_keywords(raw: Any) -> list[str]:
    values: Iterable[Any]
    if isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        values = [raw]

    keywords: list[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if "," in text:
            keywords.extend([part.strip() for part in text.split(",") if part.strip()])
        elif ";" in text:
            keywords.extend([part.strip() for part in text.split(";") if part.strip()])
        else:
            keywords.append(text)
    return keywords


def get_keywords(media_pool_item: Any) -> list[str]:
    metadata = media_pool_item.GetMetadata()
    if isinstance(metadata, dict):
        for key, value in metadata.items():
            if "keyword" in str(key).lower():
                keywords = _normalize_keywords(value)
                if keywords:
                    return keywords

    for key in ("Keywords", "keywords", "Keyword", "keyword"):
        value = media_pool_item.GetMetadata(key)
        keywords = _normalize_keywords(value)
        if keywords:
            return keywords

    clip_property = media_pool_item.GetClipProperty("Keywords")
    return _normalize_keywords(clip_property)


def set_keywords(media_pool_item: Any, keywords: list[str]) -> bool:
    joined = ", ".join(keywords)
    result = media_pool_item.SetMetadata("Keywords", joined)
    return result is True


_DATE_FORMATS = (
    "%m/%d/%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
)


def _clip_date_key(clip: Any) -> tuple:
    """Return a sort key for a clip based on its Date Created property.

    Returns a (datetime, name) tuple so clips with the same timestamp are
    broken by name, matching the secondary sort Resolve uses in the UI."""
    raw = ""
    try:
        raw = clip.GetClipProperty("Date Created") or ""
        for fmt in _DATE_FORMATS:
            try:
                return (datetime.strptime(raw.strip(), fmt), clip.GetName() or "")
            except ValueError:
                continue
    except Exception:
        pass
    # Fall back: sort unknown dates to the end, then by name.
    return (datetime.max, clip.GetName() or "")


def navigate_clip(resolve: Any, direction: int) -> Any | None:
    """Select the next (+1) or previous (-1) clip in the current Media Pool
    folder, ordered by Date Created (matching Resolve's default UI sort).
    Returns the newly selected MediaPoolItem, or None if at boundary."""
    project_manager = resolve.GetProjectManager()
    if project_manager is None:
        return None
    project = project_manager.GetCurrentProject()
    if project is None:
        return None
    media_pool = project.GetMediaPool()
    if media_pool is None:
        return None

    current_item = get_selected_media_pool_item(resolve)
    if current_item is None:
        return None

    folder = media_pool.GetCurrentFolder()
    if folder is None:
        return None

    clips = sorted(_as_sequence(folder.GetClipList()), key=_clip_date_key)
    if not clips:
        return None

    current_id = current_item.GetMediaId()
    indices = [i for i, c in enumerate(clips) if c.GetMediaId() == current_id]
    if not indices:
        return None

    new_index = indices[0] + direction
    if new_index < 0 or new_index >= len(clips):
        return None  # already at boundary

    new_item = clips[new_index]
    media_pool.SetSelectedClip(new_item)
    return new_item


def suggest_keywords(resolve: Any, n_neighbours: int = 10) -> list[str]:
    """Return up to 3 keyword suggestions for the current clip based on
    keywords used by the N temporally closest clips in the same folder."""
    project_manager = resolve.GetProjectManager()
    if project_manager is None:
        return []
    project = project_manager.GetCurrentProject()
    if project is None:
        return []
    media_pool = project.GetMediaPool()
    if media_pool is None:
        return []

    current_item = get_selected_media_pool_item(resolve)
    if current_item is None:
        return []

    folder = media_pool.GetCurrentFolder()
    if folder is None:
        return []

    clips = sorted(_as_sequence(folder.GetClipList()), key=_clip_date_key)
    if not clips:
        return []

    current_id = current_item.GetMediaId()
    indices = [i for i, c in enumerate(clips) if c.GetMediaId() == current_id]
    if not indices:
        return []

    idx = indices[0]
    half = n_neighbours // 2
    start = max(0, idx - half)
    end = min(len(clips), idx + half + 1)
    neighbours = [c for i, c in enumerate(clips[start:end], start) if i != idx]

    current_kws = {k.lower() for k in get_keywords(current_item)}

    counts: dict[str, int] = {}
    first_seen: dict[str, str] = {}
    for clip in neighbours:
        for kw in get_keywords(clip):
            key = kw.lower()
            if key not in current_kws:
                counts[key] = counts.get(key, 0) + 1
                if key not in first_seen:
                    first_seen[key] = kw

    ranked = sorted(counts.keys(), key=lambda k: -counts[k])
    return [first_seen[k] for k in ranked[:3]]


def _normalise_ai_keyword(
    text: str, existing_keywords: list[str] | None = None
) -> str:
    """Apply keyword casing conventions to a VLM response.

    Strategy:
    1. Build a lookup of known proper-noun words from existing_keywords
       (e.g. 'New York City' → {'new': 'New', 'york': 'York', 'city': 'City'}).
    2. Lowercase the entire suggestion.
    3. Restore capitalisation word-by-word from the lookup.
    4. For any word not in the lookup, keep lowercase — we default to
       generic unless we have evidence of proper-noun status.
    """
    # Words that are too generic to restore even when they appear capitalised
    # inside a proper noun (e.g. "City" in "New York City", "Street" in
    # "Wall Street").
    _GENERIC = {
        "a", "an", "the", "and", "or", "of", "in", "on", "at", "to",
        "with", "by", "for", "from",
        "city", "town", "state", "county", "district", "region",
        "street", "road", "avenue", "boulevard", "lane", "way",
        "park", "garden", "square", "place",
        "lake", "river", "bay", "sea", "ocean", "island", "mountain",
        "north", "south", "east", "west", "central",
        "upper", "lower", "old", "new", "great", "little", "big",
        "national", "international", "royal",
    }

    # Build lookup: lowercase word → canonical capitalised form.
    # Single-word keywords (e.g. "Maria", "Portugal") contribute directly.
    # Multi-word keywords (e.g. "New York City") contribute only as a full
    # phrase, not word-by-word — this prevents "York" from matching "york"
    # in an unrelated context.
    known_words: dict[str, str] = {}   # from single-word keywords only
    known_phrases: dict[str, str] = {} # from multi-word keywords

    for kw in (existing_keywords or []):
        if not kw or not kw[0].isupper():
            continue
        words_in_kw = kw.split()
        if len(words_in_kw) == 1:
            w = words_in_kw[0]
            if w.lower() not in _GENERIC:
                known_words[w.lower()] = w
        else:
            known_phrases[kw.lower()] = kw

    lower_text = text.strip().lower()
    if not lower_text:
        return text

    # Apply multi-word phrase substitutions first (longest first).
    result = lower_text
    for phrase_lower, phrase_orig in sorted(known_phrases.items(), key=lambda x: -len(x[0])):
        result = result.replace(phrase_lower, phrase_orig)

    # Apply single-word substitutions on remaining lowercase tokens.
    return " ".join(known_words.get(w, w) for w in result.split())


def ai_suggest_keyword(
    file_path: str,
    model: str = "llava",
    existing_keywords: list[str] | None = None,
) -> str | None:
    """Return a single AI-generated keyword for a clip by sending its thumbnail
    to a locally running Ollama VLM. Returns None if Ollama is unreachable."""
    import base64
    import json

    png = thumbnail_from_file_path(file_path)
    if not png:
        return None

    if existing_keywords:
        kw_context = (
            f"This clip already has these keywords: {', '.join(existing_keywords)}. "
            "Suggest one additional keyword not already in that list. "
        )
    else:
        kw_context = ""

    payload = json.dumps({
        "model": model,
        "prompt": (
            f"{kw_context}"
            "Describe the main subject of this image as a media archive keyword phrase. "
            "Use 1-4 words. "
            "If the subject is a specific named place, landmark, or person use Title Case. "
            "If the subject is a generic object, animal, activity, or natural feature use lowercase. "
            "Examples: 'sunset', 'rolling hills', 'prayer flags', 'Eiffel Tower', 'Trevi Fountain'. "
            "Reply with only the keyword phrase, no punctuation, no explanation."
        ),
        "images": [base64.b64encode(png).decode()],
        "stream": False,
    }).encode()

    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
        text = result.get("response", "").strip()
        if not text:
            return None
        return _normalise_ai_keyword(text, existing_keywords)
    except Exception:
        return None


def _ffmpeg_path() -> str:
    import shutil
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError("ffmpeg not found; install it with: brew install ffmpeg")


def _ffprobe_path() -> str:
    import shutil
    exe = shutil.which("ffprobe")
    if exe:
        return exe
    for candidate in ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", "/usr/bin/ffprobe"):
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError("ffprobe not found; install it with: brew install ffmpeg")


def thumbnail_from_file_path(file_path: str) -> bytes | None:
    """Extract a mid-point frame from a media file via ffmpeg. No Resolve IPC."""
    try:
        ffmpeg = _ffmpeg_path()
        ffprobe = _ffprobe_path()
    except FileNotFoundError:
        return None

    try:
        probe = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                file_path,
            ],
            capture_output=True,
            timeout=10,
        )
        duration = float(probe.stdout.strip()) if probe.returncode == 0 else 0.0
    except Exception:
        duration = 0.0

    seek = duration / 2 if duration > 0 else 0.0

    try:
        result = subprocess.run(
            [
                ffmpeg, "-ss", str(seek),
                "-i", file_path,
                "-frames:v", "1",
                "-f", "image2pipe",
                "-vcodec", "png",
                "-",
            ],
            capture_output=True,
            timeout=15,
        )
    except Exception:
        return None

    if result.returncode != 0 or not result.stdout:
        return None

    return result.stdout


