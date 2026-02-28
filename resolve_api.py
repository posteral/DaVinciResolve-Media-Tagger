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


def _dedup_keywords(keywords: list[str]) -> list[str]:
    """Remove duplicates case-insensitively, preserving first occurrence."""
    seen: set[str] = set()
    result: list[str] = []
    for kw in keywords:
        if kw.lower() not in seen:
            seen.add(kw.lower())
            result.append(kw)
    return result


def get_keywords(media_pool_item: Any) -> list[str]:
    metadata = media_pool_item.GetMetadata()
    if isinstance(metadata, dict):
        for key, value in metadata.items():
            if "keyword" in str(key).lower():
                keywords = _dedup_keywords(_normalize_keywords(value))
                if keywords:
                    return sorted(keywords, key=str.casefold)

    for key in ("Keywords", "keywords", "Keyword", "keyword"):
        value = media_pool_item.GetMetadata(key)
        keywords = _dedup_keywords(_normalize_keywords(value))
        if keywords:
            return sorted(keywords, key=str.casefold)

    clip_property = media_pool_item.GetClipProperty("Keywords")
    return sorted(_dedup_keywords(_normalize_keywords(clip_property)), key=str.casefold)


def set_keywords(media_pool_item: Any, keywords: list[str]) -> bool:
    joined = ", ".join(sorted(keywords, key=str.casefold))
    result = media_pool_item.SetMetadata("Keywords", joined)
    return result is True


_DATE_FORMATS = (
    "%m/%d/%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%a %b %d %Y %H:%M:%S",
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


# Cache for the current folder's clip data.
# Stores (cache_key, sorted_clips, date_by_id, keywords_by_id) where:
#   cache_key    = (folder_name, clip_count)
#   sorted_clips = clips sorted by date
#   date_by_id   = {media_id: datetime} — avoids re-calling GetClipProperty
#   keywords_by_id = {media_id: list[str]} — avoids re-calling GetMetadata
_folder_cache: tuple | None = None


def invalidate_folder_cache() -> None:
    """Force the next suggest_keywords/navigate call to rebuild the cache.
    Call this after writing keywords to any clip."""
    global _folder_cache
    _folder_cache = None


def _get_folder_cache(folder: Any) -> tuple[list, dict, dict]:
    """Return (sorted_clips, date_by_id, keywords_by_id) for the folder,
    building and caching all per-clip data in a single pass."""
    global _folder_cache
    raw = _as_sequence(folder.GetClipList())
    cache_key = (folder.GetName(), len(raw))
    if _folder_cache is not None and _folder_cache[0] == cache_key:
        _, sorted_clips, date_by_id, keywords_by_id = _folder_cache
        return sorted_clips, date_by_id, keywords_by_id

    # Build per-clip data once.
    date_by_id: dict[str, Any] = {}
    keywords_by_id: dict[str, list[str]] = {}
    for clip in raw:
        mid = clip.GetMediaId()
        date_by_id[mid] = _clip_date_key(clip)[0]
        keywords_by_id[mid] = get_keywords(clip)

    sorted_clips = sorted(raw, key=lambda c: (date_by_id[c.GetMediaId()], c.GetName() or ""))
    _folder_cache = (cache_key, sorted_clips, date_by_id, keywords_by_id)
    return sorted_clips, date_by_id, keywords_by_id


def _get_sorted_clips(folder: Any) -> list:
    sorted_clips, _, _ = _get_folder_cache(folder)
    return sorted_clips


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

    clips = _get_sorted_clips(folder)
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


def suggest_keywords(resolve: Any, current_item: Any = None) -> tuple[list[str], dict]:
    """Return up to 5 keyword suggestions for the current clip.

    Keywords are scored by proximity: each neighbouring clip at sequential
    distance d contributes 1/d to every keyword it carries.  Only clips
    recorded on the same calendar day in the same folder are considered.
    Keywords already on the current clip are excluded.

    If current_item is provided it is used directly, avoiding a redundant
    get_selected_media_pool_item() IPC call."""
    project_manager = resolve.GetProjectManager()
    if project_manager is None:
        return [], {"reason": "no project manager"}
    project = project_manager.GetCurrentProject()
    if project is None:
        return [], {"reason": "no project"}
    media_pool = project.GetMediaPool()
    if media_pool is None:
        return [], {"reason": "no media pool"}

    if current_item is None:
        current_item = get_selected_media_pool_item(resolve)
    if current_item is None:
        return [], {"reason": "no current item"}

    folder = media_pool.GetCurrentFolder()
    if folder is None:
        return [], {"reason": "no folder"}

    clips, date_by_id, keywords_by_id = _get_folder_cache(folder)
    if not clips:
        return [], {"reason": "no clips in folder"}

    current_id = current_item.GetMediaId()
    current_date_key = date_by_id.get(current_id, datetime.max)

    if current_date_key == datetime.max:
        return [], {"reason": "no parseable date", "clip": current_item.GetName()}

    current_date = current_date_key.date()
    current_kws = {k.lower() for k in keywords_by_id.get(current_id, [])}

    # Find the index of the current clip in the sorted list.
    current_index = next(
        (i for i, c in enumerate(clips) if c.GetMediaId() == current_id), None
    )

    scores: dict[str, float] = {}
    first_seen: dict[str, str] = {}
    neighbour_count = 0
    for i, c in enumerate(clips):
        cid = c.GetMediaId()
        if cid == current_id:
            continue
        d = date_by_id.get(cid, datetime.max)
        if d == datetime.max or d.date() != current_date:
            continue
        neighbour_count += 1
        # Weight by inverse sequential distance when position is known.
        if current_index is not None:
            weight = 1.0 / abs(i - current_index)
        else:
            weight = 1.0
        for kw in keywords_by_id.get(cid, []):
            key = kw.lower()
            if key not in current_kws:
                scores[key] = scores.get(key, 0.0) + weight
                if key not in first_seen:
                    first_seen[key] = kw

    ranked = sorted(scores.keys(), key=lambda k: -scores[k])
    suggestions = [first_seen[k] for k in ranked[:5]]

    debug = {
        "clip": current_item.GetName(),
        "date": str(current_date),
        "neighbours": neighbour_count,
        "suggestions": suggestions,
    }
    return suggestions, debug


def _collect_folder_keywords(folder: Any, seen: set[str], result: list[str]) -> None:
    """Recursively walk a Media Pool folder tree and collect all unique keywords."""
    for clip in _as_sequence(folder.GetClipList()):
        for kw in get_keywords(clip):
            low = kw.lower()
            if low not in seen:
                seen.add(low)
                result.append(kw)
    for subfolder in _as_sequence(folder.GetSubFolderList()):
        _collect_folder_keywords(subfolder, seen, result)


def get_all_project_keywords(resolve: Any) -> list[str]:
    """Return a sorted, deduplicated list of all keywords used across the
    entire project media pool (all folders, recursively)."""
    project_manager = resolve.GetProjectManager()
    if project_manager is None:
        return []
    project = project_manager.GetCurrentProject()
    if project is None:
        return []
    media_pool = project.GetMediaPool()
    if media_pool is None:
        return []

    root = media_pool.GetRootFolder()
    if root is None:
        return []

    seen: set[str] = set()
    result: list[str] = []
    _collect_folder_keywords(root, seen, result)
    return sorted(result, key=str.casefold)


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


def ai_suggest_keywords(
    file_path: str,
    model: str = "llava",
    existing_keywords: list[str] | None = None,
    proximity_suggestions: list[str] | None = None,
    catalog: list[str] | None = None,
    n: int = 5,
) -> list[str]:
    """Return up to n AI-generated keyword suggestions for a clip by sending
    multiple sampled frames to a locally running Ollama VLM. Returns [] if
    Ollama is unreachable or no frames are available."""
    import base64
    import json

    frames = frames_from_file_path(file_path)
    if not frames:
        return []

    marker = "ProxyMedia/"
    idx = file_path.find(marker)
    display_path = file_path[idx + len(marker):] if idx != -1 else file_path
    path_context = f"The file path of this clip is: {display_path}. "

    context_parts: list[str] = []
    if existing_keywords:
        context_parts.append(f"This clip already has these keywords: {', '.join(existing_keywords)}.")
    if proximity_suggestions:
        context_parts.append(f"Nearby clips in the same shoot have these keywords: {', '.join(proximity_suggestions)}.")
    all_context = " ".join(context_parts)

    if all_context:
        kw_context = (
            f"{all_context} "
            f"Suggest {n} additional keywords not already in that list. "
        )
    else:
        kw_context = f"Suggest {n} keywords for this clip. "

    catalog_context = ""
    if catalog:
        catalog_context = (
            f"Prefer exact wording from this existing keyword catalog when relevant: "
            f"{', '.join(catalog)}. "
        )

    payload = json.dumps({
        "model": model,
        "prompt": (
            f"{path_context}"
            f"{kw_context}"
            f"{catalog_context}"
            "Each keyword is a phrase of 1-4 words describing a distinct aspect of the clip. "
            "If a subject is a specific named place, landmark, or person use Title Case. "
            "If a subject is a generic object, animal, activity, or natural feature use lowercase. "
            "Examples: 'sunset', 'rolling hills', 'prayer flags', 'Eiffel Tower', 'Trevi Fountain'. "
            f"Reply with exactly {n} keyword phrases separated by commas, nothing else."
        ),
        "images": [base64.b64encode(f).decode() for f in frames],
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
            return []

        existing_lower = {kw.lower() for kw in (existing_keywords or [])}
        suggestions: list[str] = []
        seen: set[str] = set()
        for part in text.split(","):
            kw = part.strip().strip(".")
            if not kw:
                continue
            normalised = _normalise_ai_keyword(kw, existing_keywords)
            lower = normalised.lower()
            if lower in existing_lower or lower in seen:
                continue
            seen.add(lower)
            suggestions.append(normalised)
            if len(suggestions) == n:
                break

        return suggestions
    except Exception:
        return []


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


def _probe_duration(file_path: str, ffprobe: str) -> float:
    """Return the duration of a media file in seconds, or 0.0 on failure."""
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
        return float(probe.stdout.strip()) if probe.returncode == 0 else 0.0
    except Exception:
        return 0.0


def _extract_frame(file_path: str, ffmpeg: str, seek: float) -> bytes | None:
    """Extract a single PNG frame at the given seek position."""
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


def thumbnail_from_file_path(file_path: str) -> bytes | None:
    """Extract a mid-point frame from a media file via ffmpeg. No Resolve IPC."""
    try:
        ffmpeg = _ffmpeg_path()
        ffprobe = _ffprobe_path()
    except FileNotFoundError:
        return None

    duration = _probe_duration(file_path, ffprobe)
    seek = duration / 2 if duration > 0 else 0.0
    return _extract_frame(file_path, ffmpeg, seek)


def frames_from_file_path(
    file_path: str,
    percentages: tuple[float, ...] = (0.1, 0.3, 0.5, 0.7, 0.9),
) -> list[bytes]:
    """Extract one PNG frame per percentage position of the clip duration.
    Returns a list of raw PNG bytes (may be shorter than percentages if some
    frames fail). Falls back to a single mid-point frame if duration is unknown.
    No Resolve IPC."""
    try:
        ffmpeg = _ffmpeg_path()
        ffprobe = _ffprobe_path()
    except FileNotFoundError:
        return []

    duration = _probe_duration(file_path, ffprobe)
    if duration > 0:
        seeks = [duration * p for p in percentages]
    else:
        seeks = [0.0]  # unknown duration — fall back to start

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(seeks)) as pool:
        results = list(pool.map(lambda s: _extract_frame(file_path, ffmpeg, s), seeks))
    return [f for f in results if f]


