"""search_index.py — Shot Finder index build pipeline.

M1.2: CSV parser (pure Python, no Resolve dependency).
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path
from typing import Any


# Date format produced by DaVinci Resolve's ExportMetadata:
# "Sat May 28 18:07:12 2022" (weekday abbrev, trailing space stripped)
_DATE_FMT = "%a %b %d %H:%M:%S %Y"


def _parse_date(raw: str) -> datetime | None:
    """Parse a Resolve ExportMetadata date string. Returns None on failure."""
    s = raw.strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, _DATE_FMT)
    except ValueError:
        return None


def _parse_keywords(raw: str) -> list[str]:
    """Split a comma-separated keyword string, strip whitespace, drop empties."""
    return [kw.strip() for kw in raw.split(",") if kw.strip()]


def parse_export_csv(path: str | Path) -> list[dict[str, Any]]:
    """Parse a DaVinci Resolve ExportMetadata CSV (UTF-16) into a list of clip dicts.

    Each returned dict has:
        file_name   : str           — clip filename (e.g. "20220528_C9031.MP4")
        clip_dir    : str           — folder path on disk
        keywords    : list[str]     — parsed keyword list (may be empty)
        date        : datetime|None — Date Modified, or None if unparseable
        duration_tc : str           — Duration TC string (e.g. "00:00:11:26")

    Rows without a Clip Directory are skipped (they are bin/folder entries).
    """
    clips: list[dict[str, Any]] = []

    with open(path, newline="", encoding="utf-16") as f:
        reader = csv.DictReader(f)
        for row in reader:
            clip_dir = (row.get("Clip Directory") or "").strip()
            if not clip_dir:
                continue  # bin / folder entry — skip

            file_name = (row.get("File Name") or "").strip()
            if not file_name:
                continue

            clips.append({
                "file_name": file_name,
                "clip_dir": clip_dir,
                "keywords": _parse_keywords(row.get("Keywords") or ""),
                "date": _parse_date(row.get("Date Modified") or ""),
                "duration_tc": (row.get("Duration TC") or "").strip(),
            })

    return clips


def parse_export_csv_text(text: str) -> list[dict[str, Any]]:
    """Same as parse_export_csv but accepts an already-decoded string.
    Used in tests to avoid writing UTF-16 fixture files."""
    clips: list[dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        clip_dir = (row.get("Clip Directory") or "").strip()
        if not clip_dir:
            continue
        file_name = (row.get("File Name") or "").strip()
        if not file_name:
            continue
        clips.append({
            "file_name": file_name,
            "clip_dir": clip_dir,
            "keywords": _parse_keywords(row.get("Keywords") or ""),
            "date": _parse_date(row.get("Date Modified") or ""),
            "duration_tc": (row.get("Duration TC") or "").strip(),
        })
    return clips
