from __future__ import annotations

import subprocess
import sys
import os
import time
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
    # Normalise at the write boundary: strip, dedup, sort.
    keywords = _dedup_keywords(_normalize_keywords(keywords))
    joined = ",".join(sorted(keywords, key=str.casefold))
    result = media_pool_item.SetMetadata("Keywords", joined)
    # Also write to ClipProperty so Resolve's Keyword Manager indexes the words.
    try:
        media_pool_item.SetClipProperty("Keywords", joined)
    except Exception:
        pass
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

# Proximity suggestions pre-computed for the last navigated/loaded clip.
# Keyed by media_id so stale results from a previous clip are never served.
# Populated by navigate_clip() and suggest_keywords(); read by get_cached_suggestions().
_last_suggestions: tuple[str, list[str]] | None = None  # (media_id, suggestions)


def get_cached_suggestions(media_id: str) -> list[str] | None:
    """Return cached proximity suggestions for media_id, or None if stale/absent."""
    if _last_suggestions is not None and _last_suggestions[0] == media_id:
        return _last_suggestions[1]
    return None


def invalidate_folder_cache() -> None:
    """Force the next suggest_keywords/navigate call to rebuild the cache.
    Call this after writing keywords to any clip."""
    global _folder_cache, _last_suggestions
    _folder_cache = None
    _last_suggestions = None


def _get_folder_cache(folder: Any, raw: list | None = None) -> tuple[list, dict, dict, dict]:
    """Return (sorted_clips, date_by_id, keywords_by_id, proxy_by_id) for the folder,
    building and caching all per-clip data in a single pass.

    Pass raw (already-fetched GetClipList result) to avoid a redundant IPC call
    when the caller already has the clip list."""
    global _folder_cache
    folder_name = folder.GetName()
    # Cache hit: same folder name and cache is still valid (not invalidated by Save).
    if _folder_cache is not None and _folder_cache[0] == folder_name:
        _, sorted_clips, date_by_id, keywords_by_id, proxy_by_id = _folder_cache
        return sorted_clips, date_by_id, keywords_by_id, proxy_by_id

    # Cache miss: fetch clip list if the caller didn't supply it.
    if raw is None:
        raw = _as_sequence(folder.GetClipList())

    # Build per-clip data once.
    date_by_id: dict[str, Any] = {}
    keywords_by_id: dict[str, list[str]] = {}
    proxy_by_id: dict[str, str] = {}
    for clip in raw:
        mid = clip.GetMediaId()
        date_by_id[mid] = _clip_date_key(clip)[0]
        keywords_by_id[mid] = get_keywords(clip)
        proxy_by_id[mid] = clip.GetClipProperty("Proxy Media Path") or ""

    sorted_clips = sorted(raw, key=lambda c: (date_by_id[c.GetMediaId()], c.GetName() or ""))
    _folder_cache = (folder_name, sorted_clips, date_by_id, keywords_by_id, proxy_by_id)
    return sorted_clips, date_by_id, keywords_by_id, proxy_by_id


def _get_sorted_clips(folder: Any, raw: list | None = None) -> list:
    sorted_clips, _, _, _ = _get_folder_cache(folder, raw)
    return sorted_clips


def get_neighbours(media_id: str) -> tuple[str, str]:
    """Return (prev_proxy_path, next_proxy_path) for the clip with the given media_id.

    Reads from _folder_cache only — zero Resolve IPC. Returns ('', '') if cache
    is cold or the clip is not found."""
    if _folder_cache is None:
        return "", ""
    _, sorted_clips, _, _, proxy_by_id = _folder_cache
    ids = [c.GetMediaId() for c in sorted_clips]
    try:
        idx = ids.index(media_id)
    except ValueError:
        return "", ""
    prev_path = proxy_by_id.get(ids[idx - 1], "") if idx > 0 else ""
    next_path = proxy_by_id.get(ids[idx + 1], "") if idx < len(ids) - 1 else ""
    return prev_path, next_path


def _find_folder_for_clip(folder: Any, target_id: str) -> Any | None:
    """Recursively search the folder tree for the folder containing target_id.
    Returns the folder object, or None if not found."""
    for clip in _as_sequence(folder.GetClipList()):
        if clip.GetMediaId() == target_id:
            return folder
    for subfolder in _as_sequence(folder.GetSubFolderList()):
        found = _find_folder_for_clip(subfolder, target_id)
        if found is not None:
            return found
    return None


def _resolve_folder(
    media_pool: Any, current_item: Any
) -> tuple[Any, list] | tuple[None, None]:
    """Return (folder, raw_clip_list) for current_item.

    Tries GetCurrentFolder() first (fast). Falls back to a tree walk when
    Resolve has no current folder set — which happens when the clip is
    selected from the timeline rather than the Media Pool browser.

    Returns the raw clip list alongside the folder so callers can pass it
    directly to _get_folder_cache and avoid a redundant GetClipList() IPC call."""
    folder = media_pool.GetCurrentFolder()
    if folder is not None:
        # Verify the current clip actually lives in this folder; if not
        # (e.g. Resolve reports a stale current folder) fall through to walk.
        current_id = current_item.GetMediaId()
        clips = _as_sequence(folder.GetClipList())
        if any(c.GetMediaId() == current_id for c in clips):
            return folder, clips

    # Fall back: walk the full tree to find the right folder.
    root = media_pool.GetRootFolder()
    if root is None:
        return None, None
    fallback = _find_folder_for_clip(root, current_item.GetMediaId())
    return fallback, None  # raw unknown; _get_folder_cache will fetch it


def navigate_clip(
    resolve: Any, direction: int
) -> tuple[Any, dict[str, float]] | tuple[None, dict[str, float]]:
    """Select the next (+1) or previous (-1) clip in the current Media Pool
    folder, ordered by Date Created (matching Resolve's default UI sort).
    Returns (MediaPoolItem, timing_ms) where timing_ms contains sub-phase
    breakdowns, or (None, timing_ms) if at boundary or on error."""
    timing: dict[str, float] = {}

    t_pre = time.perf_counter()
    project_manager = resolve.GetProjectManager()
    if project_manager is None:
        return None, timing
    project = project_manager.GetCurrentProject()
    if project is None:
        return None, timing
    media_pool = project.GetMediaPool()
    if media_pool is None:
        return None, timing
    timing["pre_ipc_ms"] = (time.perf_counter() - t_pre) * 1000

    t_sel = time.perf_counter()
    current_item = get_selected_media_pool_item(resolve)
    timing["get_selected_ms_pre"] = (time.perf_counter() - t_sel) * 1000
    if current_item is None:
        return None, timing

    current_id = current_item.GetMediaId()

    # Fast path: if the folder cache is warm and the current clip is in it,
    # skip _resolve_folder entirely (saves ~200ms of GetCurrentFolder IPC).
    timing["resolve_folder_ms"] = 0.0
    timing["cache_miss"] = False
    clips = None
    if _folder_cache is not None:
        _, cached_clips, _, _, _ = _folder_cache
        if any(c.GetMediaId() == current_id for c in cached_clips):
            clips = cached_clips
            timing["folder_cache_ms"] = 0.0

    if clips is None:
        # Slow path: cache is cold or clip not found — fall back to IPC.
        t0 = time.perf_counter()
        folder, raw = _resolve_folder(media_pool, current_item)
        timing["resolve_folder_ms"] = (time.perf_counter() - t0) * 1000
        timing["cache_miss"] = True
        if folder is None:
            return None, timing
        t1 = time.perf_counter()
        clips, _, _, _ = _get_folder_cache(folder, raw)
        timing["folder_cache_ms"] = (time.perf_counter() - t1) * 1000
        if not clips:
            return None, timing

    indices = [i for i, c in enumerate(clips) if c.GetMediaId() == current_id]
    if not indices:
        return None, timing

    new_index = indices[0] + direction
    if new_index < 0 or new_index >= len(clips):
        return None, timing  # already at boundary

    new_item = clips[new_index]
    t2 = time.perf_counter()
    media_pool.SetSelectedClip(new_item)
    timing["set_selected_ms"] = (time.perf_counter() - t2) * 1000
    return new_item, timing


def suggest_keywords_from_cache(media_id: str, keywords: list[str]) -> list[str] | None:
    """Compute proximity suggestions from _folder_cache only — zero Resolve IPC.

    Returns the suggestion list, or None if the cache is cold or the clip is
    not found (caller should fall back to suggest_keywords with the lock)."""
    if _folder_cache is None:
        return None

    _, sorted_clips, date_by_id, keywords_by_id, _ = _folder_cache

    current_index = next(
        (i for i, c in enumerate(sorted_clips) if c.GetMediaId() == media_id), None
    )
    if current_index is None:
        return None

    current_kws = {k.lower() for k in keywords}

    WINDOW = 50
    lo = max(0, current_index - WINDOW)
    hi = min(len(sorted_clips), current_index + WINDOW + 1)

    best_score: dict[str, float] = {}
    first_seen: dict[str, str] = {}
    for i in range(lo, hi):
        c = sorted_clips[i]
        cid = c.GetMediaId()
        if cid == media_id:
            continue
        if date_by_id.get(cid, datetime.max) == datetime.max:
            continue
        weight = 1.0 / abs(i - current_index)
        for kw in keywords_by_id.get(cid, []):
            key = kw.lower()
            if key not in current_kws:
                if weight > best_score.get(key, 0.0):
                    best_score[key] = weight
                if key not in first_seen or (kw[0].isupper() and not first_seen[key][0].isupper()):
                    first_seen[key] = kw

    ranked = sorted(best_score.keys(), key=lambda k: -best_score[k])
    suggestions = [first_seen[k] for k in ranked[:12]]

    global _last_suggestions
    _last_suggestions = (media_id, suggestions)
    return suggestions


def suggest_keywords(resolve: Any, current_item: Any = None) -> tuple[list[str], dict]:
    """Return up to 5 keyword suggestions for the current clip.

    Keywords are scored by proximity: each neighbouring clip at sequential
    distance d contributes 1/d to every keyword it carries. All dated clips
    in the folder are considered — the distance weighting naturally down-ranks
    distant clips without needing a day boundary filter.
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

    folder, raw = _resolve_folder(media_pool, current_item)
    if folder is None:
        return [], {"reason": "no folder"}

    clips, date_by_id, keywords_by_id, _ = _get_folder_cache(folder, raw)
    if not clips:
        return [], {"reason": "no clips in folder"}

    current_id = current_item.GetMediaId()
    current_kws = {k.lower() for k in keywords_by_id.get(current_id, [])}

    # Find the index of the current clip in the sorted list.
    current_index = next(
        (i for i, c in enumerate(clips) if c.GetMediaId() == current_id), None
    )

    if current_index is None:
        return [], {"reason": "current clip not found in folder"}

    # Score each keyword by the distance to its NEAREST carrying clip (not the
    # sum across all clips). This prevents high-frequency keywords that appear
    # on many clips in the window from drowning out unique keywords that appear
    # only on the immediately-adjacent clip.
    # A keyword on the clip at distance 1 scores 1.0 regardless of how many
    # other clips further away also carry it.
    WINDOW = 50
    lo = max(0, current_index - WINDOW)
    hi = min(len(clips), current_index + WINDOW + 1)

    # best_score[key] = 1/d of the nearest clip that carries this keyword
    best_score: dict[str, float] = {}
    first_seen: dict[str, str] = {}
    neighbour_count = 0
    for i in range(lo, hi):
        c = clips[i]
        cid = c.GetMediaId()
        if cid == current_id:
            continue
        # Skip clips with no parseable date — their sort position is unreliable.
        if date_by_id.get(cid, datetime.max) == datetime.max:
            continue
        neighbour_count += 1
        weight = 1.0 / abs(i - current_index)
        for kw in keywords_by_id.get(cid, []):
            key = kw.lower()
            if key not in current_kws:
                if weight > best_score.get(key, 0.0):
                    best_score[key] = weight
                if key not in first_seen or (kw[0].isupper() and not first_seen[key][0].isupper()):
                    first_seen[key] = kw

    ranked = sorted(best_score.keys(), key=lambda k: -best_score[k])
    suggestions = [first_seen[k] for k in ranked[:12]]

    global _last_suggestions
    _last_suggestions = (current_id, suggestions)

    debug = {
        "clip": current_item.GetName(),
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


def _collect_timeline_media(timeline: Any) -> dict[str, Any]:
    """Return {media_id: MediaPoolItem} for every unique clip cut into any
    video or audio track of `timeline`. Items with no backing Media Pool
    item (generators, titles, adjustment clips, etc.) are skipped."""
    used: dict[str, Any] = {}
    for track_type in ("video", "audio"):
        track_count = timeline.GetTrackCount(track_type) or 0
        for track_index in range(1, track_count + 1):
            for item in _as_sequence(timeline.GetItemListInTrack(track_type, track_index)):
                mpi = item.GetMediaPoolItem()
                if mpi is not None:
                    used[mpi.GetMediaId()] = mpi
    return used


def _collect_all_clips(folder: Any, result: list) -> None:
    """Recursively walk a Media Pool folder tree, collecting every clip."""
    result.extend(_as_sequence(folder.GetClipList()))
    for subfolder in _as_sequence(folder.GetSubFolderList()):
        _collect_all_clips(subfolder, result)


def _majority_used_tag(used_clips: Iterable[Any]) -> str | None:
    """Return the `Used:...` keyword already carried by a strict majority of
    `used_clips`, if any — so a previously-customised tag wins over
    recomputing the default from the (possibly ugly) project name. Returns
    None if no `Used:...` keyword reaches a majority."""
    used_clips = list(used_clips)
    if not used_clips:
        return None

    counts: dict[str, int] = {}
    original: dict[str, str] = {}
    for clip in used_clips:
        for kw in get_keywords(clip):
            if kw.lower().startswith("used:"):
                low = kw.lower()
                counts[low] = counts.get(low, 0) + 1
                original.setdefault(low, kw)

    if not counts:
        return None
    best_low, best_count = max(counts.items(), key=lambda kv: kv[1])
    return original[best_low] if best_count > len(used_clips) / 2 else None


def sync_timeline_used_tag(resolve: Any, dry_run: bool = False, tag: str | None = None) -> dict:
    """Reconcile a keyword against the active timeline's current contents.
    Adds the tag to clips newly cut into the timeline, removes it from
    clips that no longer are. When dry_run=True, computes the same diff
    without writing anything (preview mode).

    `tag` overrides the default tag string — pass a stripped non-empty
    string to use a custom tag instead. When omitted, the default is
    whichever `Used:...` keyword already carries a majority of the clips
    currently on the timeline (i.e. a previously-applied custom tag wins),
    falling back to `Used:{project_name}` if no such majority tag exists.

    Raises RuntimeError with a descriptive message if there's no current
    project, no active timeline, no media pool, or the tag contains a
    comma/semicolon (which would be split apart by the keyword storage
    format).
    """
    project_manager = resolve.GetProjectManager()
    project = project_manager.GetCurrentProject() if project_manager else None
    if project is None:
        raise RuntimeError("No current project")
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise RuntimeError("No active timeline")
    media_pool = project.GetMediaPool()
    if media_pool is None:
        raise RuntimeError("No media pool")

    project_name = project.GetName() or "Untitled Project"
    used_by_id = _collect_timeline_media(timeline)

    tag = (tag or "").strip()
    if not tag:
        tag = _majority_used_tag(used_by_id.values()) or f"Used:{project_name}"
    if "," in tag or ";" in tag:
        raise RuntimeError("Tag cannot contain commas or semicolons")

    root = media_pool.GetRootFolder()
    all_clips: list = []
    if root is not None:
        _collect_all_clips(root, all_clips)

    added, removed, already_tagged, untouched = [], [], 0, 0
    for clip in all_clips:
        mid = clip.GetMediaId()
        kws = get_keywords(clip)
        has_tag = any(k.lower() == tag.lower() for k in kws)
        should_have = mid in used_by_id

        if should_have and has_tag:
            already_tagged += 1
        elif should_have and not has_tag:
            if not dry_run:
                set_keywords(clip, kws + [tag])
            added.append(clip.GetName() or mid)
        elif has_tag and not should_have:
            if not dry_run:
                set_keywords(clip, [k for k in kws if k.lower() != tag.lower()])
            removed.append(clip.GetName() or mid)
        else:
            # Not on the timeline and correctly untagged — not relevant to this sync.
            untouched += 1

    return {
        "project_name": project_name,
        "timeline_name": timeline.GetName() or "",
        "tag": tag,
        "used_count": len(used_by_id),
        "added": added,
        "removed": removed,
        "already_tagged": already_tagged,
        "untouched": untouched,
        "applied": not dry_run,
    }


def _dominant_used_tag(keyword_lists: Iterable[list[str]]) -> str | None:
    """Return the most common `Used:...` keyword across `keyword_lists` (one
    list per clip; the mode, no majority threshold) — used to suggest a
    default tag for reconciliation. Returns None if none carry a
    `Used:...` keyword."""
    counts: dict[str, int] = {}
    original: dict[str, str] = {}
    for kws in keyword_lists:
        for kw in kws:
            if kw.lower().startswith("used:"):
                low = kw.lower()
                counts[low] = counts.get(low, 0) + 1
                original.setdefault(low, kw)
    if not counts:
        return None
    best_low = max(counts.items(), key=lambda kv: kv[1])[0]
    return original[best_low]


def _collect_clip_lookup_recursive(folder: Any, result: dict) -> None:
    """Walk folder tree, populating result[(file_name, frames)] = clip.

    Matches on (file_name, Frames) rather than file path — confirmed live
    that Resolve's media-management "copy into project" glues a
    project-bundle prefix onto the path (e.g. ".../Project X.dra/MediaFiles/
    <original path>"), which breaks any path-based match between a working
    project's managed copy and the same clip's entry in another project.
    Date Modified looked like a good second signal alongside filename, but
    isn't reliable — confirmed live that it drifts by a fixed offset (e.g.
    exactly 6 hours) for some clips between a project's copy and the
    original, almost certainly a timezone bug in whatever tool ingested
    that batch. Frames is a total frame count derived straight from the
    video content, immune to that kind of drift, and matched exactly in
    every case checked — combined with filename it's still effectively
    collision-proof against generic camera filenames (e.g. "C0233.MP4")
    repeating across unrelated shoots in a 25k+-clip catalog. Clips with no
    parseable frame count are skipped entirely — an unparseable value
    can't be trusted as an identity signal, and would otherwise collide
    with every other clip missing it under the same filename.

    Cheap pass — only GetName()/GetClipProperty("Frames"), no full
    metadata reads — used to get a writable clip reference for the small
    subset of clips matched via a bulk ExportMetadata read."""
    import search_index
    for clip in _as_sequence(folder.GetClipList()):
        frames = search_index._parse_frames(clip.GetClipProperty("Frames") or "")
        if frames is not None:
            result[(clip.GetName(), frames)] = clip
    for subfolder in _as_sequence(folder.GetSubFolderList()):
        _collect_clip_lookup_recursive(subfolder, result)


def list_projects(resolve: Any) -> dict:
    """Return {"current": name, "current_database": name, "projects": [...],
    "databases": [...]} for the current Resolve database's Project Manager.

    `projects` only lists what's visible in the *current* database — it
    does not search other databases (that would mean switching away from
    whatever the user has open just to populate a list). `databases` is
    informational only, listing every database name Reconcile will search
    through when actually run.

    Purely read-only browsing — does not load or switch the active project
    or database (GotoRootFolder only changes Project Manager navigation
    state)."""
    pm = resolve.GetProjectManager()
    if pm is None:
        return {"current": "", "current_database": "", "projects": [], "databases": []}
    current_project = pm.GetCurrentProject()
    current = current_project.GetName() if current_project else ""
    current_database = pm.GetCurrentDatabase() or {}
    pm.GotoRootFolder()
    projects = _as_sequence(pm.GetProjectListInCurrentFolder())
    databases = [db.get("DbName", "") for db in _as_sequence(pm.GetDatabaseList())]
    return {
        "current": current,
        "current_database": current_database.get("DbName", ""),
        "projects": sorted(projects, key=str.casefold),
        "databases": sorted({d for d in databases if d}, key=str.casefold),
    }


def reconcile_project_keywords(
    resolve: Any, target_project_name: str, tag: str | None = None, dry_run: bool = False
) -> dict:
    """Union-merge keywords from every `Used:...`-tagged clip in the current
    project into the matching clips (by (file_name, Frames) — see
    _collect_clip_lookup_recursive for why) of a different target project,
    then restore the original project as current.

    This is a general cross-project reconciliation, not tied to any
    particular "master catalog" — the target project is whatever name is
    passed in. Never overwrites or intersects: each matched target clip
    ends up with the union of its own keywords and the source clip's
    keywords. The source (current) project's clips are never modified.

    Switches Resolve's active project via ProjectManager.LoadProject() to
    reach the target. The target project may live in a different Resolve
    database than the current one (e.g. a master catalog kept on an
    internal drive while working projects live on an external one) —
    LoadProject() only ever searches the current database, so this first
    checks the current database, then searches every other known database
    (GetDatabaseList()) for the target project by name. Always attempts to
    restore the original database *and* explicitly reload the original
    project before returning (even on error) — switching database alone
    does not restore whatever project was previously loaded (confirmed
    live: it leaves an "Untitled Project" behind, not the prior project).
    Saves the source project before switching away so nothing unsaved is
    at risk.

    Raises RuntimeError with a descriptive message for: no current
    project/media pool, an empty target name, target_project_name equal to
    the current project (case-insensitive), no clip in the current project
    carrying any `Used:...` keyword when `tag` is not given, a tag with a
    comma/semicolon, SaveProject() failure, the target project not found in any
    known database, or LoadProject() failure to reach it once found (in
    either not-found case, the original database/project is restored
    before raising).
    """
    import tempfile
    import search_index

    project_manager = resolve.GetProjectManager()
    source_project = project_manager.GetCurrentProject() if project_manager else None
    if source_project is None:
        raise RuntimeError("No current project")
    source_media_pool = source_project.GetMediaPool()
    if source_media_pool is None:
        raise RuntimeError("No media pool")

    source_project_name = source_project.GetName() or "Untitled Project"
    target_project_name = (target_project_name or "").strip()
    if not target_project_name:
        raise RuntimeError("Target project is required")
    if target_project_name.lower() == source_project_name.lower():
        raise RuntimeError("Target project cannot be the same as the current project")

    # Gather the source side entirely before touching project state. A live
    # per-clip walk rather than a bulk ExportMetadata read (unlike the
    # target below) — the source project is small (hundreds, not tens of
    # thousands, of clips), and ExportMetadata silently skips any row with
    # no "Clip Directory" (e.g. a Fusion title/generator clip with no disk
    # path), which would make a tagged-but-pathless clip vanish from the
    # counts entirely instead of correctly showing up as unmatched.
    source_root = source_media_pool.GetRootFolder()
    all_source_clips: list = []
    if source_root is not None:
        _collect_all_clips(source_root, all_source_clips)
    source_clip_keywords = [get_keywords(clip) for clip in all_source_clips]

    tag = (tag or "").strip()
    if not tag:
        tag = _dominant_used_tag(source_clip_keywords) or ""
    if not tag:
        raise RuntimeError("No clip in the current project carries a Used:... keyword")
    if "," in tag or ";" in tag:
        raise RuntimeError("Tag cannot contain commas or semicolons")

    used_entries = []
    for clip, kws in zip(all_source_clips, source_clip_keywords):
        if any(k.lower() == tag.lower() for k in kws):
            frames = search_index._parse_frames(clip.GetClipProperty("Frames") or "")
            used_entries.append({
                "file_name": clip.GetName() or "",
                "frames": frames,
                "keywords": kws,
            })

    if not used_entries:
        raise RuntimeError(f"No clip in {source_project_name!r} carries the tag {tag!r}")

    if not project_manager.SaveProject():
        raise RuntimeError(
            "Could not save the current project before switching — aborted, nothing was changed"
        )

    # The target project may live in a different Resolve database (e.g. a
    # master catalog on an internal drive while working projects live on an
    # external one) — LoadProject() only ever searches the *current*
    # database, so find which database actually has it first. Try the
    # current database before searching others (fast path — most runs will
    # target something already reachable).
    source_database = project_manager.GetCurrentDatabase()
    target_database = source_database

    def _restore_source() -> bool:
        """Best-effort restore of the original database + project, verified
        and retried — confirmed live that switching back immediately after
        a heavy write (hundreds of keyword writes plus a full project
        save) can transiently fail silently: SetCurrentDatabase/LoadProject
        just return without actually landing back on the source, no
        exception raised. Returns True only once GetCurrentProject()
        actually confirms we're back."""
        for _ in range(5):
            project_manager.SetCurrentDatabase(source_database)
            project_manager.LoadProject(source_project_name)
            current = project_manager.GetCurrentProject()
            if current is not None and current.GetName() == source_project_name:
                return True
            time.sleep(0.5)
        return False

    project_manager.GotoRootFolder()
    found = target_project_name in _as_sequence(project_manager.GetProjectListInCurrentFolder())
    if not found:
        for db in _as_sequence(project_manager.GetDatabaseList()):
            if db == source_database:
                continue
            if not project_manager.SetCurrentDatabase(db):
                continue
            project_manager.GotoRootFolder()
            if target_project_name in _as_sequence(project_manager.GetProjectListInCurrentFolder()):
                target_database = db
                found = True
                break
        if not found:
            restored = _restore_source()
            msg = f"Could not find project {target_project_name!r} in any known database"
            if not restored:
                msg += f". Also failed to switch Resolve back to {source_project_name!r} — check it manually"
            raise RuntimeError(msg)

    # At this point Resolve is already positioned on target_database,
    # either unchanged (found in the current one) or switched to it above.
    if not project_manager.LoadProject(target_project_name):
        restored = _restore_source()
        msg = f"Could not load project {target_project_name!r}"
        if not restored:
            msg += f". Also failed to switch Resolve back to {source_project_name!r} — check it manually"
        raise RuntimeError(msg)

    try:
        target_project = project_manager.GetCurrentProject()
        target_media_pool = target_project.GetMediaPool()

        # Bulk read of the target's current keywords via ExportMetadata —
        # avoids a slow per-clip GetMetadata() walk across a large catalog.
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            csv_path = f.name
        try:
            if not export_metadata(resolve, csv_path):
                raise RuntimeError(f"ExportMetadata failed for {target_project_name!r}")
            target_clips_meta = search_index.parse_export_csv(csv_path)
        finally:
            try:
                os.unlink(csv_path)
            except OSError:
                pass

        # Keyed by (file_name, Frames) — not path, and not Date Modified.
        # Resolve's media-management "copy into project" glues a
        # project-bundle prefix onto the path, breaking any path-based
        # match for a managed/copied clip. Date Modified looked like a good
        # second signal but isn't reliable: confirmed live that some of
        # this catalog's more recently-ingested clips have a Date Modified
        # that drifts by a fixed offset (e.g. exactly 6 hours) between a
        # project's copy and the original — almost certainly a timezone
        # bug in whatever tool ingested that batch, not anything Resolve
        # or this code controls. Frames is derived straight from the video
        # content and matched exactly in every case checked, including the
        # ones where Date Modified drifted, so it's the reliable half of
        # the fingerprint; combined with filename it's still effectively
        # collision-proof against generic camera filenames (e.g.
        # "C0233.MP4") repeating across unrelated shoots in a large
        # catalog. Clips with no parseable frame count are dropped rather
        # than risking a collision under a shared key.
        target_keywords_by_key = {
            (c["file_name"], c["frames"]): c["keywords"]
            for c in target_clips_meta
            if c["frames"] is not None
        }

        # Lightweight object lookup (no full metadata reads) — only needed
        # to write the small subset of clips that actually change.
        target_root = target_media_pool.GetRootFolder()
        clip_lookup: dict = {}
        if target_root is not None:
            _collect_clip_lookup_recursive(target_root, clip_lookup)

        updated: list = []
        unmatched: list = []
        already_synced = 0
        for entry in used_entries:
            if entry["frames"] is None:
                unmatched.append(entry["file_name"])
                continue
            key = (entry["file_name"], entry["frames"])
            if key not in target_keywords_by_key:
                unmatched.append(entry["file_name"])
                continue
            target_kws = target_keywords_by_key[key]
            target_kws_lower = {k.lower() for k in target_kws}
            added = [k for k in entry["keywords"] if k.lower() not in target_kws_lower]
            if not added:
                already_synced += 1
                continue
            target_clip = clip_lookup.get(key)
            if target_clip is None:
                # Matched via the bulk export but not found in the live
                # object walk (tree changed mid-operation) — surface it
                # rather than silently dropping the update.
                unmatched.append(entry["file_name"])
                continue
            if not dry_run:
                set_keywords(target_clip, target_kws + added)
            updated.append({"clip": entry["file_name"], "added": added})

        # Writes above only land in Resolve's in-memory project state.
        # Switching projects/databases without saving first discards them
        # silently, exactly like closing a document without saving — this
        # must happen while target is still current, before we switch away.
        if not dry_run and updated:
            project_manager.SaveProject()
    finally:
        # Switching database does not by itself restore whatever project
        # was previously loaded (confirmed live: it leaves an "Untitled
        # Project" behind) — both steps are required, in this order, and
        # verified/retried since the switch-back can transiently fail
        # silently right after a heavy write (see _restore_source above).
        restored = _restore_source()

    return {
        "source_project": source_project_name,
        "source_database": source_database.get("DbName", ""),
        "target_project": target_project_name,
        "target_database": target_database.get("DbName", ""),
        "tag": tag,
        "used_count": len(used_entries),
        "updated": updated,
        "unmatched": unmatched,
        "source_restored": restored,
        "already_synced": already_synced,
        "applied": not dry_run,
    }


def get_project_name(resolve: Any) -> str:
    """Return the current project name, or empty string on failure."""
    try:
        pm = resolve.GetProjectManager()
        if pm is None:
            return ""
        project = pm.GetCurrentProject()
        if project is None:
            return ""
        return project.GetName() or ""
    except Exception:
        return ""


def export_metadata(resolve: Any, csv_path: str) -> bool:
    """Call media_pool.ExportMetadata(csv_path) and return True on success."""
    try:
        pm = resolve.GetProjectManager()
        if pm is None:
            return False
        project = pm.GetCurrentProject()
        if project is None:
            return False
        media_pool = project.GetMediaPool()
        if media_pool is None:
            return False
        return bool(media_pool.ExportMetadata(csv_path))
    except Exception:
        return False


def _find_clip_by_name_and_dir(
    folder: Any, file_name: str, clip_dir: str
) -> tuple[Any, Any] | tuple[None, None]:
    """Recursively search for a clip matching file_name and clip_dir.

    Returns (folder, clip) or (None, None) if not found.
    clip_dir is matched against the directory part of the clip's File Path.
    """
    for clip in _as_sequence(folder.GetClipList()):
        if clip.GetName() == file_name:
            file_path = clip.GetClipProperty("File Path") or ""
            # Normalise both sides: strip trailing slash, compare directory part.
            if os.path.dirname(file_path).rstrip("/\\") == clip_dir.rstrip("/\\"):
                return folder, clip
    for subfolder in _as_sequence(folder.GetSubFolderList()):
        result = _find_clip_by_name_and_dir(subfolder, file_name, clip_dir)
        if result[0] is not None:
            return result
    return None, None


def _collect_proxy_paths_recursive(folder: Any, result: dict) -> None:
    """Walk folder tree, populating result[(file_name, clip_dir)] = proxy_path."""
    for clip in _as_sequence(folder.GetClipList()):
        proxy = clip.GetClipProperty("Proxy Media Path") or ""
        if proxy:
            file_path = clip.GetClipProperty("File Path") or ""
            clip_dir = os.path.dirname(file_path).rstrip("/\\") if file_path else ""
            result[(clip.GetName(), clip_dir)] = proxy
    for subfolder in _as_sequence(folder.GetSubFolderList()):
        _collect_proxy_paths_recursive(subfolder, result)


def collect_proxy_paths(resolve: Any) -> dict[tuple, str]:
    """Return {(file_name, clip_dir): proxy_path} for all clips that have a proxy.

    Keyed by (file_name, clip_dir) so clips with the same filename in different
    folders are matched correctly. Single tree walk — called once during index build.
    Returns empty dict on failure.
    """
    try:
        pm = resolve.GetProjectManager()
        if pm is None:
            return {}
        project = pm.GetCurrentProject()
        if project is None:
            return {}
        media_pool = project.GetMediaPool()
        if media_pool is None:
            return {}
        root = media_pool.GetRootFolder()
        if root is None:
            return {}
        result: dict[tuple, str] = {}
        _collect_proxy_paths_recursive(root, result)
        print(f"[search] collected {len(result)} proxy paths")
        return result
    except Exception:
        return {}


def select_clip_in_resolve(resolve: Any, file_name: str, clip_dir: str) -> dict:
    """Navigate the media pool to the clip identified by file_name + clip_dir.

    Calls SetCurrentFolder on the containing folder and SetSelectedClip on
    the clip so Resolve shows it in the media pool viewer.

    Returns {"ok": True} on success or {"ok": False, "error": str} on failure.
    """
    try:
        pm = resolve.GetProjectManager()
        if pm is None:
            return {"ok": False, "error": "no project manager"}
        project = pm.GetCurrentProject()
        if project is None:
            return {"ok": False, "error": "no current project"}
        media_pool = project.GetMediaPool()
        if media_pool is None:
            return {"ok": False, "error": "no media pool"}

        root = media_pool.GetRootFolder()
        if root is None:
            return {"ok": False, "error": "no root folder"}

        folder, clip = _find_clip_by_name_and_dir(root, file_name, clip_dir)
        if clip is None:
            return {"ok": False, "error": f"clip not found: {file_name}"}

        # Only call SetCurrentFolder when necessary. SetCurrentFolder resets
        # Resolve's media pool sort order to File Name every time it is called,
        # and the scripting API has no way to restore it. Skipping the call when
        # the clip is already in the current folder preserves the user's sort.
        current_folder = media_pool.GetCurrentFolder()
        if current_folder is None or current_folder.GetName() != folder.GetName():
            folder_result = media_pool.SetCurrentFolder(folder)
            print(f"[select] file={file_name!r} folder={folder.GetName()!r} SetCurrentFolder={folder_result}")
        else:
            print(f"[select] file={file_name!r} folder={folder.GetName()!r} SetCurrentFolder=skipped (already current)")

        select_result = media_pool.SetSelectedClip(clip)
        print(f"[select] SetSelectedClip={select_result}")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
    n: int = 10,
) -> list[str]:
    """Return up to n AI-generated keyword suggestions for a clip by sending
    multiple sampled frames to a locally running Ollama VLM. Returns [] if
    Ollama is unreachable or no frames are available."""
    import base64
    import json

    # Use a single representative frame (midpoint) for speed.
    # Sending 5 frames multiplies inference time ~5× with minimal quality gain.
    frames = frames_from_file_path(file_path, percentages=(0.5,))
    if not frames:
        return []

    existing_str = ", ".join(existing_keywords) if existing_keywords else ""
    if existing_str:
        kw_context = (
            f"This clip already has these keywords: {existing_str}. "
            f"Suggest {n} additional keywords not already in that list. "
        )
    else:
        kw_context = f"Suggest {n} keywords for this clip. "

    payload = json.dumps({
        "model": model,
        "prompt": (
            f"{kw_context}"
            "Rules: each keyword must be 1-4 words only, no sentences or punctuation. "
            "Named places, landmarks, and people use Title Case. "
            "Generic objects, activities, and features use lowercase. "
            f"Respond with exactly {n} comma-separated keywords and nothing else. "
            f"Example format: keyword one, Keyword Two, keyword three"
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
        with urllib.request.urlopen(req, timeout=20) as resp:
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
            # Reject sentence fragments — real keywords are at most 4 words (~40 chars).
            if len(kw) > 40 or len(kw.split()) > 5:
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


_FFMPEG_PATH: str | None = None
_FFPROBE_PATH: str | None = None


def _ffmpeg_path() -> str:
    global _FFMPEG_PATH
    if _FFMPEG_PATH is not None:
        return _FFMPEG_PATH
    import shutil
    exe = shutil.which("ffmpeg")
    if exe:
        _FFMPEG_PATH = exe
        return exe
    for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
        if os.path.isfile(candidate):
            _FFMPEG_PATH = candidate
            return candidate
    raise FileNotFoundError("ffmpeg not found; install it with: brew install ffmpeg")


def _ffprobe_path() -> str:
    global _FFPROBE_PATH
    if _FFPROBE_PATH is not None:
        return _FFPROBE_PATH
    import shutil
    exe = shutil.which("ffprobe")
    if exe:
        _FFPROBE_PATH = exe
        return exe
    for candidate in ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", "/usr/bin/ffprobe"):
        if os.path.isfile(candidate):
            _FFPROBE_PATH = candidate
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
    """Extract one PNG frame per percentage position of the clip duration in a
    single ffmpeg pass. Returns a list of raw PNG bytes (may be shorter than
    percentages if the duration is unknown). No Resolve IPC."""
    try:
        ffmpeg = _ffmpeg_path()
        ffprobe = _ffprobe_path()
    except FileNotFoundError:
        return []

    frames, _, _ = frames_from_file_path_timed(file_path, percentages)
    return frames


def frames_from_file_path_timed(
    file_path: str,
    percentages: tuple[float, ...] = (0.1, 0.3, 0.5, 0.7, 0.9),
) -> tuple[list[bytes], float, float]:
    """Like frames_from_file_path but also returns (probe_ms, extract_ms)."""
    import time as _time
    try:
        ffmpeg = _ffmpeg_path()
        ffprobe = _ffprobe_path()
    except FileNotFoundError:
        return [], 0.0, 0.0

    t0 = _time.perf_counter()
    duration = _probe_duration(file_path, ffprobe)
    probe_ms = (_time.perf_counter() - t0) * 1000

    if duration > 0:
        seeks = [duration * p for p in percentages]
    else:
        seeks = [0.0]

    t1 = _time.perf_counter()
    frames = _extract_frames_single_pass(file_path, ffmpeg, seeks)
    extract_ms = (_time.perf_counter() - t1) * 1000

    return frames, probe_ms, extract_ms


def _extract_frames_single_pass(file_path: str, ffmpeg: str, seeks: list[float]) -> list[bytes]:
    """Extract multiple frames concurrently, one ffmpeg subprocess per seek.

    Runs all extractions in parallel via ThreadPoolExecutor so the wall-clock
    time is roughly that of a single extraction, not N × single extraction.
    The duration probe has already been done by the caller so there is no
    redundant ffprobe here."""
    if not seeks:
        return []

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(seeks)) as pool:
        results = list(pool.map(lambda s: _extract_frame(file_path, ffmpeg, s), seeks))
    return [f for f in results if f]


