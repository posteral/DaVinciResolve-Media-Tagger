from __future__ import annotations

import sys
import os
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


def _dedupe_preserve_order(keywords: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for kw in keywords:
        lower = kw.lower()
        if lower not in seen:
            seen.add(lower)
            result.append(kw)
    return result


def merge_keywords(existing: list[str], incoming: list[str], mode: str) -> list[str]:
    if mode in ("set", "replace"):
        return _dedupe_preserve_order(incoming)
    if mode == "append":
        return _dedupe_preserve_order(existing + incoming)
    raise ValueError(f"Unknown merge mode: {mode!r}")


def set_keywords(media_pool_item: Any, keywords: list[str]) -> bool:
    joined = ", ".join(keywords)
    result = media_pool_item.SetMetadata("Keywords", joined)
    return result is True


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
    import subprocess

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


