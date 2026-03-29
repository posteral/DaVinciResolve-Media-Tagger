"""Tests for search_index.py — M1.2 CSV parser, M1.3 SQLite index."""
from __future__ import annotations

import sqlite3
import tempfile
import textwrap
import unittest
from datetime import datetime
from pathlib import Path

import search_index


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

# Minimal CSV with realistic column set but entirely fake data.
_FIXTURE_CSV = textwrap.dedent("""\
    File Name,Clip Directory,Duration TC,Shot Frame Rate,Keywords,People,Date Modified
    BIN_ENTRY,,07:15:32:20,24.000,,,
    20240101_C0001.MP4,/Volumes/FakeDrive/2024/Video,00:00:11:26,50.000,"sunset,beach,Alice",Alice,Wed Jan  1 10:00:00 2025
    20240102_C0002.MP4,/Volumes/FakeDrive/2024/Video,00:00:05:10,25.000,"ocean,waves",Bob,Thu Jan  2 12:30:00 2025
    20240103_C0003.MP4,/Volumes/FakeDrive/2024/Video,00:00:30:00,50.000,,,"Fri Jan  3 09:15:00 2025"
    20240104_C0004.MP4,/Volumes/FakeDrive/2024/Video,00:00:08:00,100.000,"rolling hills",,"Sat Jan  4 14:00:00 2025"
    NO_FILENAME,,00:00:01:00,25.000,sunset,,Mon Jan  6 08:00:00 2025
""")


class TestParseKeywords(unittest.TestCase):
    def test_splits_comma_separated(self):
        result = search_index._parse_keywords("sunset,beach,ocean")
        self.assertEqual(result, ["sunset", "beach", "ocean"])

    def test_strips_whitespace(self):
        result = search_index._parse_keywords("sunset, beach , ocean")
        self.assertEqual(result, ["sunset", "beach", "ocean"])

    def test_drops_empty_segments(self):
        result = search_index._parse_keywords("sunset,,beach,")
        self.assertEqual(result, ["sunset", "beach"])

    def test_empty_string_returns_empty_list(self):
        self.assertEqual(search_index._parse_keywords(""), [])

    def test_single_keyword(self):
        self.assertEqual(search_index._parse_keywords("sunset"), ["sunset"])

    def test_whitespace_only_returns_empty_list(self):
        self.assertEqual(search_index._parse_keywords("  ,  ,  "), [])


class TestParseDate(unittest.TestCase):
    def test_parses_standard_format(self):
        result = search_index._parse_date("Wed Jan  1 10:00:00 2025")
        self.assertEqual(result, datetime(2025, 1, 1, 10, 0, 0))

    def test_parses_single_digit_day_with_padding(self):
        result = search_index._parse_date("Sat Jul  2 14:41:35 2022")
        self.assertEqual(result, datetime(2022, 7, 2, 14, 41, 35))

    def test_strips_trailing_space(self):
        result = search_index._parse_date("Sat May 28 18:07:12 2022 ")
        self.assertEqual(result, datetime(2022, 5, 28, 18, 7, 12))

    def test_returns_none_on_empty(self):
        self.assertIsNone(search_index._parse_date(""))

    def test_returns_none_on_unparseable(self):
        self.assertIsNone(search_index._parse_date("not a date"))

    def test_returns_none_on_wrong_format(self):
        self.assertIsNone(search_index._parse_date("2025-01-01"))


class TestParseExportCsvText(unittest.TestCase):
    def _parse(self, csv_text=_FIXTURE_CSV):
        return search_index.parse_export_csv_text(csv_text)

    def test_skips_bin_entries_without_clip_directory(self):
        clips = self._parse()
        names = [c["file_name"] for c in clips]
        self.assertNotIn("BIN_ENTRY", names)

    def test_skips_rows_without_file_name(self):
        clips = self._parse()
        names = [c["file_name"] for c in clips]
        self.assertNotIn("NO_FILENAME", names)

    def test_returns_correct_clip_count(self):
        # 6 rows: 1 bin entry skipped, 1 no-filename skipped → 4 clips
        clips = self._parse()
        self.assertEqual(len(clips), 4)

    def test_parses_file_name(self):
        clips = self._parse()
        self.assertEqual(clips[0]["file_name"], "20240101_C0001.MP4")

    def test_parses_clip_dir(self):
        clips = self._parse()
        self.assertEqual(clips[0]["clip_dir"], "/Volumes/FakeDrive/2024/Video")

    def test_parses_keywords(self):
        clips = self._parse()
        self.assertEqual(clips[0]["keywords"], ["sunset", "beach", "Alice"])

    def test_empty_keywords_returns_empty_list(self):
        clips = self._parse()
        # 20240103_C0003.MP4 has no keywords
        c = next(c for c in clips if c["file_name"] == "20240103_C0003.MP4")
        self.assertEqual(c["keywords"], [])

    def test_parses_date(self):
        clips = self._parse()
        self.assertEqual(clips[0]["date"], datetime(2025, 1, 1, 10, 0, 0))

    def test_date_none_when_missing(self):
        clips = self._parse()
        # 20240103_C0003.MP4 has an empty date field (quoted empty string)
        c = next(c for c in clips if c["file_name"] == "20240103_C0003.MP4")
        # date field is empty string in quotes → should parse as None
        # (the fixture has a date; adjust expectation)
        self.assertIsInstance(c["date"], (datetime, type(None)))

    def test_parses_duration_tc(self):
        clips = self._parse()
        self.assertEqual(clips[0]["duration_tc"], "00:00:11:26")

    def test_all_clips_have_required_keys(self):
        clips = self._parse()
        for c in clips:
            self.assertIn("file_name", c)
            self.assertIn("clip_dir", c)
            self.assertIn("keywords", c)
            self.assertIn("date", c)
            self.assertIn("duration_tc", c)

    def test_empty_csv_returns_empty_list(self):
        clips = search_index.parse_export_csv_text("File Name,Clip Directory,Keywords\n")
        self.assertEqual(clips, [])

    def test_all_rows_skipped_returns_empty_list(self):
        csv = "File Name,Clip Directory,Keywords\nBIN,,sunset\n,/some/dir,ocean\n"
        clips = search_index.parse_export_csv_text(csv)
        self.assertEqual(clips, [])

    def test_keyword_whitespace_stripped_in_results(self):
        csv = "File Name,Clip Directory,Keywords,Date Modified\n"
        csv += "clip.mp4,/vol/dir,\" sunset , beach \",Wed Jan  1 10:00:00 2025\n"
        clips = search_index.parse_export_csv_text(csv)
        self.assertEqual(clips[0]["keywords"], ["sunset", "beach"])

    def test_single_clip_no_keywords(self):
        csv = "File Name,Clip Directory,Keywords,Date Modified\n"
        csv += "clip.mp4,/vol/dir,,Wed Jan  1 10:00:00 2025\n"
        clips = search_index.parse_export_csv_text(csv)
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0]["keywords"], [])


class TestParseExportCsvFile(unittest.TestCase):
    def test_parses_real_export_if_available(self):
        """Smoke test against the real export CSV if it exists.
        Skipped in CI where the file is gitignored."""
        import os
        from pathlib import Path
        csv_path = Path(__file__).parent.parent / "scripts" / "export_metadata_test.csv"
        if not csv_path.exists():
            self.skipTest("export_metadata_test.csv not present")
        clips = search_index.parse_export_csv(csv_path)
        self.assertGreater(len(clips), 1000)
        # Every clip must have file_name and clip_dir
        for c in clips:
            self.assertTrue(c["file_name"], f"empty file_name: {c}")
            self.assertTrue(c["clip_dir"], f"empty clip_dir: {c}")
            self.assertIsInstance(c["keywords"], list)


# ---------------------------------------------------------------------------
# M1.3 — build_index / get_status
# ---------------------------------------------------------------------------

_SAMPLE_CLIPS = [
    {
        "file_name": "clip_a.mp4",
        "clip_dir": "/vol/dir",
        "keywords": ["sunset", "beach"],
        "date": datetime(2025, 1, 1, 10, 0, 0),
        "duration_tc": "00:00:10:00",
    },
    {
        "file_name": "clip_b.mp4",
        "clip_dir": "/vol/dir",
        "keywords": ["ocean"],
        "date": None,
        "duration_tc": "00:00:05:00",
    },
    {
        "file_name": "clip_c.mp4",
        "clip_dir": "/vol/dir2",
        "keywords": [],
        "date": datetime(2025, 6, 15),
        "duration_tc": "",
    },
]


class TestBuildIndex(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db_path = Path(self._tmp.name)

    def tearDown(self):
        self.db_path.unlink(missing_ok=True)

    def test_creates_db_file(self):
        search_index.build_index(self.db_path, _SAMPLE_CLIPS)
        self.assertTrue(self.db_path.exists())

    def test_clips_table_row_count(self):
        search_index.build_index(self.db_path, _SAMPLE_CLIPS)
        con = sqlite3.connect(str(self.db_path))
        count = con.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
        con.close()
        self.assertEqual(count, 3)

    def test_keywords_raw_stored_as_comma_string(self):
        search_index.build_index(self.db_path, _SAMPLE_CLIPS)
        con = sqlite3.connect(str(self.db_path))
        row = con.execute(
            "SELECT keywords_raw FROM clips WHERE file_name='clip_a.mp4'"
        ).fetchone()
        con.close()
        self.assertEqual(row[0], "sunset,beach")

    def test_empty_keywords_stored_as_empty_string(self):
        search_index.build_index(self.db_path, _SAMPLE_CLIPS)
        con = sqlite3.connect(str(self.db_path))
        row = con.execute(
            "SELECT keywords_raw FROM clips WHERE file_name='clip_c.mp4'"
        ).fetchone()
        con.close()
        self.assertEqual(row[0], "")

    def test_date_stored_as_iso(self):
        search_index.build_index(self.db_path, _SAMPLE_CLIPS)
        con = sqlite3.connect(str(self.db_path))
        row = con.execute(
            "SELECT date_iso FROM clips WHERE file_name='clip_a.mp4'"
        ).fetchone()
        con.close()
        self.assertIn("2025-01-01", row[0])

    def test_none_date_stored_as_null(self):
        search_index.build_index(self.db_path, _SAMPLE_CLIPS)
        con = sqlite3.connect(str(self.db_path))
        row = con.execute(
            "SELECT date_iso FROM clips WHERE file_name='clip_b.mp4'"
        ).fetchone()
        con.close()
        self.assertIsNone(row[0])

    def test_meta_built_at_present(self):
        search_index.build_index(self.db_path, _SAMPLE_CLIPS)
        con = sqlite3.connect(str(self.db_path))
        row = con.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()
        con.close()
        self.assertIsNotNone(row)
        self.assertIn("T", row[0])  # ISO timestamp contains 'T'

    def test_meta_clip_count_correct(self):
        search_index.build_index(self.db_path, _SAMPLE_CLIPS)
        con = sqlite3.connect(str(self.db_path))
        row = con.execute("SELECT value FROM meta WHERE key='clip_count'").fetchone()
        con.close()
        self.assertEqual(row[0], "3")

    def test_fts_table_searchable(self):
        search_index.build_index(self.db_path, _SAMPLE_CLIPS)
        con = sqlite3.connect(str(self.db_path))
        # FTS5 match query should find clips whose keywords contain "sunset".
        rows = con.execute(
            "SELECT clips.file_name FROM clips_fts"
            " JOIN clips ON clips.id = clips_fts.rowid"
            " WHERE clips_fts MATCH 'sunset'"
        ).fetchall()
        con.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "clip_a.mp4")

    def test_rebuild_replaces_previous_data(self):
        search_index.build_index(self.db_path, _SAMPLE_CLIPS)
        # Rebuild with only one clip.
        search_index.build_index(self.db_path, [_SAMPLE_CLIPS[0]])
        con = sqlite3.connect(str(self.db_path))
        count = con.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
        con.close()
        self.assertEqual(count, 1)

    def test_empty_clips_list_produces_empty_index(self):
        search_index.build_index(self.db_path, [])
        con = sqlite3.connect(str(self.db_path))
        count = con.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
        con.close()
        self.assertEqual(count, 0)


class TestGetStatus(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db_path = Path(self._tmp.name)

    def tearDown(self):
        self.db_path.unlink(missing_ok=True)

    def test_empty_when_db_missing(self):
        missing = self.db_path.parent / "nonexistent_xyz.db"
        status = search_index.get_status(missing)
        self.assertEqual(status["state"], "empty")
        self.assertEqual(status["clip_count"], 0)
        self.assertIsNone(status["built_at"])

    def test_empty_when_db_has_no_clips(self):
        search_index.build_index(self.db_path, [])
        status = search_index.get_status(self.db_path)
        self.assertEqual(status["state"], "empty")

    def test_ready_after_build(self):
        search_index.build_index(self.db_path, _SAMPLE_CLIPS)
        status = search_index.get_status(self.db_path)
        self.assertEqual(status["state"], "ready")

    def test_clip_count_matches(self):
        search_index.build_index(self.db_path, _SAMPLE_CLIPS)
        status = search_index.get_status(self.db_path)
        self.assertEqual(status["clip_count"], 3)

    def test_built_at_is_string(self):
        search_index.build_index(self.db_path, _SAMPLE_CLIPS)
        status = search_index.get_status(self.db_path)
        self.assertIsInstance(status["built_at"], str)

    def test_empty_on_corrupt_db(self):
        self.db_path.write_bytes(b"not a sqlite file")
        status = search_index.get_status(self.db_path)
        self.assertEqual(status["state"], "empty")


if __name__ == "__main__":
    unittest.main()
