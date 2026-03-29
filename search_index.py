"""search_index.py — Shot Finder index build pipeline.

M1.2: CSV parser (pure Python, no Resolve dependency).
M1.3: SQLite schema + FTS5 index build.
"""
from __future__ import annotations

import csv
import io
import sqlite3
from datetime import datetime, timezone
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


# ---------------------------------------------------------------------------
# M1.3 — SQLite schema + FTS5 index
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS clips (
    id           INTEGER PRIMARY KEY,
    file_name    TEXT NOT NULL,
    clip_dir     TEXT NOT NULL,
    keywords_raw TEXT NOT NULL,
    date_iso     TEXT,
    duration_tc  TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS clips_fts USING fts5(
    keywords,
    content='clips',
    content_rowid='id'
);
"""


def build_index(db_path: str | Path, clips: list[dict[str, Any]]) -> None:
    """Create (or replace) the SQLite index at db_path from a list of clip dicts.

    Drops and recreates the clips and clips_fts tables so the index is always
    a clean snapshot. Writes a built_at ISO timestamp to the meta table.
    """
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(_SCHEMA)

        # Full rebuild: clear existing rows.
        # FTS5 content table: use the special delete-all command first,
        # then delete the underlying clips rows.
        con.execute("INSERT INTO clips_fts(clips_fts) VALUES('delete-all')")
        con.execute("DELETE FROM clips")

        rows = []
        for c in clips:
            date_iso = c["date"].isoformat() if isinstance(c.get("date"), datetime) else None
            keywords_raw = ",".join(c.get("keywords") or [])
            rows.append((
                c["file_name"],
                c["clip_dir"],
                keywords_raw,
                date_iso,
                c.get("duration_tc") or "",
            ))

        con.executemany(
            "INSERT INTO clips (file_name, clip_dir, keywords_raw, date_iso, duration_tc)"
            " VALUES (?, ?, ?, ?, ?)",
            rows,
        )

        # Populate FTS table (keywords column).
        con.execute(
            "INSERT INTO clips_fts (rowid, keywords)"
            " SELECT id, keywords_raw FROM clips"
        )

        built_at = datetime.now(timezone.utc).isoformat()
        con.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('built_at', ?)",
            (built_at,),
        )
        con.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('clip_count', ?)",
            (str(len(clips)),),
        )
        con.commit()
    finally:
        con.close()


def get_status(db_path: str | Path) -> dict[str, Any]:
    """Return index status dict: {state, clip_count, built_at}.

    state is 'ready' if the index exists and has clips, otherwise 'empty'.
    built_at is an ISO string or None.
    """
    path = Path(db_path)
    if not path.exists():
        return {"state": "empty", "clip_count": 0, "built_at": None}

    try:
        con = sqlite3.connect(str(path))
        try:
            built_at = con.execute(
                "SELECT value FROM meta WHERE key='built_at'"
            ).fetchone()
            clip_count_row = con.execute(
                "SELECT value FROM meta WHERE key='clip_count'"
            ).fetchone()
            clip_count = int(clip_count_row[0]) if clip_count_row else 0
            if clip_count > 0:
                return {
                    "state": "ready",
                    "clip_count": clip_count,
                    "built_at": built_at[0] if built_at else None,
                }
            return {"state": "empty", "clip_count": 0, "built_at": None}
        finally:
            con.close()
    except Exception:
        return {"state": "empty", "clip_count": 0, "built_at": None}
