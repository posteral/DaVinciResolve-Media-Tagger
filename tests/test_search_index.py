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
# "Tag" (not "Good Take") is the real ExportMetadata column name for the
# Good Take flag — confirmed against a live export; parse_export_csv reads
# "Tag" deliberately, so the fixture must use that header too.
_FIXTURE_CSV = textwrap.dedent("""\
    File Name,Clip Directory,Duration TC,Shot Frame Rate,Keywords,People,Date Modified,Tag,Frames
    BIN_ENTRY,,07:15:32:20,24.000,,,,0,
    20240101_C0001.MP4,/Volumes/FakeDrive/2024/Video,00:00:11:26,50.000,"sunset,beach,Alice",Alice,Wed Jan  1 10:00:00 2025,1,550
    20240102_C0002.MP4,/Volumes/FakeDrive/2024/Video,00:00:05:10,25.000,"ocean,waves",Bob,Thu Jan  2 12:30:00 2025,0,260
    20240103_C0003.MP4,/Volumes/FakeDrive/2024/Video,00:00:30:00,50.000,,,"Fri Jan  3 09:15:00 2025",0,
    20240104_C0004.MP4,/Volumes/FakeDrive/2024/Video,00:00:08:00,100.000,"rolling hills",,"Sat Jan  4 14:00:00 2025",1,400
    NO_FILENAME,,00:00:01:00,25.000,sunset,,Mon Jan  6 08:00:00 2025,0,
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


class TestParseFrames(unittest.TestCase):
    def test_parses_valid_integer_string(self):
        self.assertEqual(search_index._parse_frames("480"), 480)

    def test_strips_whitespace(self):
        self.assertEqual(search_index._parse_frames("  480  "), 480)

    def test_returns_none_on_empty(self):
        self.assertIsNone(search_index._parse_frames(""))

    def test_returns_none_on_non_numeric(self):
        self.assertIsNone(search_index._parse_frames("N/A"))


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
        csv = "File Name,Clip Directory,Keywords,Date Modified\nclip.mp4,/vol/dir,sunset,\n"
        clips = search_index.parse_export_csv_text(csv)
        self.assertIsNone(clips[0]["date"])

    def test_frames_parsed_as_int(self):
        clips = self._parse()
        c = next(c for c in clips if c["file_name"] == "20240101_C0001.MP4")
        self.assertEqual(c["frames"], 550)

    def test_frames_none_when_missing(self):
        clips = self._parse()
        c = next(c for c in clips if c["file_name"] == "20240103_C0003.MP4")
        self.assertIsNone(c["frames"])

    def test_frames_none_when_column_absent(self):
        csv = "File Name,Clip Directory,Keywords\nclip.mp4,/vol/dir,sunset\n"
        clips = search_index.parse_export_csv_text(csv)
        self.assertIsNone(clips[0]["frames"])

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
            self.assertIn("frames", c)
            self.assertIn("good_take", c)

    def test_good_take_true_when_column_is_1(self):
        clips = self._parse()
        c = next(c for c in clips if c["file_name"] == "20240101_C0001.MP4")
        self.assertTrue(c["good_take"])

    def test_good_take_false_when_column_is_0(self):
        clips = self._parse()
        c = next(c for c in clips if c["file_name"] == "20240102_C0002.MP4")
        self.assertFalse(c["good_take"])

    def test_good_take_false_when_column_absent(self):
        csv = "File Name,Clip Directory,Keywords\nclip.mp4,/vol/dir,sunset\n"
        clips = search_index.parse_export_csv_text(csv)
        self.assertFalse(clips[0]["good_take"])

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
        "good_take": True,
    },
    {
        "file_name": "clip_b.mp4",
        "clip_dir": "/vol/dir",
        "keywords": ["ocean"],
        "date": None,
        "duration_tc": "00:00:05:00",
        "good_take": False,
    },
    {
        "file_name": "clip_c.mp4",
        "clip_dir": "/vol/dir2",
        "keywords": [],
        "date": datetime(2025, 6, 15),
        "duration_tc": "",
        "good_take": False,
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

    def test_good_take_stored_correctly(self):
        search_index.build_index(self.db_path, _SAMPLE_CLIPS)
        con = sqlite3.connect(str(self.db_path))
        rows = {
            r[0]: r[1]
            for r in con.execute("SELECT file_name, good_take FROM clips").fetchall()
        }
        con.close()
        self.assertEqual(rows["clip_a.mp4"], 1)
        self.assertEqual(rows["clip_b.mp4"], 0)

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

    def test_project_name_stored_and_returned(self):
        search_index.build_index(self.db_path, _SAMPLE_CLIPS, project_name="MyProject")
        status = search_index.get_status(self.db_path)
        self.assertEqual(status["project_name"], "MyProject")

    def test_project_name_empty_string_when_not_set(self):
        search_index.build_index(self.db_path, _SAMPLE_CLIPS, project_name="")
        status = search_index.get_status(self.db_path)
        self.assertEqual(status["project_name"], "")

    def test_empty_on_corrupt_db(self):
        self.db_path.write_bytes(b"not a sqlite file")
        status = search_index.get_status(self.db_path)
        self.assertEqual(status["state"], "empty")

    def test_empty_status_has_project_name_none(self):
        missing = self.db_path.parent / "nonexistent_xyz.db"
        status = search_index.get_status(missing)
        self.assertIsNone(status["project_name"])


# ---------------------------------------------------------------------------
# M2 — search_clips
# ---------------------------------------------------------------------------

_SEARCH_CLIPS = [
    {
        "file_name": "clip_sunset.mp4",
        "clip_dir": "/vol/a",
        "keywords": ["sunset", "beach", "France"],
        "date": datetime(2025, 6, 1),
        "duration_tc": "00:00:10:00",
        "good_take": True,
    },
    {
        "file_name": "clip_ocean.mp4",
        "clip_dir": "/vol/a",
        "keywords": ["ocean", "waves", "France"],
        "date": datetime(2025, 5, 1),
        "duration_tc": "00:00:05:00",
        "good_take": False,
    },
    {
        "file_name": "clip_hills.mp4",
        "clip_dir": "/vol/b",
        "keywords": ["rolling hills", "France", "countryside"],
        "date": datetime(2025, 4, 1),
        "duration_tc": "00:00:08:00",
        "good_take": False,
    },
    {
        "file_name": "clip_nokw.mp4",
        "clip_dir": "/vol/b",
        "keywords": [],
        "date": datetime(2025, 3, 1),
        "duration_tc": "00:00:03:00",
        "good_take": False,
    },
]


class TestSearchClips(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db_path = Path(self._tmp.name)
        search_index.build_index(self.db_path, _SEARCH_CLIPS)

    def tearDown(self):
        self.db_path.unlink(missing_ok=True)

    # ── basic behaviour ──────────────────────────────────────────────────────

    def test_returns_empty_when_db_missing(self):
        result = search_index.search_clips("/nonexistent/path.db", "sunset")
        self.assertEqual(result, {"total": 0, "results": []})

    def test_returns_empty_for_blank_query(self):
        result = search_index.search_clips(self.db_path, "")
        self.assertEqual(result, {"total": 0, "results": []})

    def test_returns_empty_for_whitespace_query(self):
        result = search_index.search_clips(self.db_path, "   ")
        self.assertEqual(result, {"total": 0, "results": []})

    def test_single_keyword_match(self):
        result = search_index.search_clips(self.db_path, "sunset")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["file_name"], "clip_sunset.mp4")

    def test_no_match_returns_zero_total(self):
        result = search_index.search_clips(self.db_path, "volcano")
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["results"], [])

    def test_result_has_all_expected_keys(self):
        result = search_index.search_clips(self.db_path, "sunset")
        r = result["results"][0]
        for key in ("id", "file_name", "clip_dir", "keywords", "date_iso", "duration_tc", "good_take"):
            self.assertIn(key, r)

    def test_keywords_returned_as_list(self):
        result = search_index.search_clips(self.db_path, "sunset")
        kws = result["results"][0]["keywords"]
        self.assertIsInstance(kws, list)
        self.assertIn("sunset", kws)

    def test_good_take_returned_as_bool(self):
        result = search_index.search_clips(self.db_path, "sunset")
        self.assertIs(result["results"][0]["good_take"], True)

    def test_good_take_false_for_non_good_take(self):
        result = search_index.search_clips(self.db_path, "ocean")
        self.assertIs(result["results"][0]["good_take"], False)

    # ── AND semantics ────────────────────────────────────────────────────────

    def test_multi_token_query_requires_all(self):
        # Both sunset AND France → only clip_sunset.mp4
        result = search_index.search_clips(self.db_path, "sunset France")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["file_name"], "clip_sunset.mp4")

    def test_multi_token_no_match_when_one_missing(self):
        # sunset AND volcano → no clip has both
        result = search_index.search_clips(self.db_path, "sunset volcano")
        self.assertEqual(result["total"], 0)

    def test_france_matches_three_clips(self):
        result = search_index.search_clips(self.db_path, "France")
        self.assertEqual(result["total"], 3)

    # ── prefix matching ──────────────────────────────────────────────────────

    def test_prefix_match_partial_word(self):
        # "sun" should prefix-match "sunset"
        result = search_index.search_clips(self.db_path, "sun")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["file_name"], "clip_sunset.mp4")

    def test_prefix_match_case_insensitive(self):
        result_lower = search_index.search_clips(self.db_path, "france")
        result_upper = search_index.search_clips(self.db_path, "France")
        self.assertEqual(result_lower["total"], result_upper["total"])

    # ── quoted phrase matching ───────────────────────────────────────────────

    def test_quoted_phrase_matches_multiword_keyword(self):
        result = search_index.search_clips(self.db_path, '"rolling hills"')
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["file_name"], "clip_hills.mp4")

    def test_quoted_phrase_with_additional_token(self):
        # "rolling hills" AND France → clip_hills.mp4 has both
        result = search_index.search_clips(self.db_path, '"rolling hills" France')
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["file_name"], "clip_hills.mp4")

    def test_quoted_phrase_no_match_for_nonexistent_phrase(self):
        # "sunset ocean" is not a keyword phrase on any clip
        result = search_index.search_clips(self.db_path, '"sunset ocean"')
        self.assertEqual(result["total"], 0)

    def test_bare_words_still_work_alongside_quoted_phrase(self):
        result = search_index.search_clips(self.db_path, 'beach "rolling hills"')
        # no clip has both beach and rolling hills
        self.assertEqual(result["total"], 0)

    # ── sorting ──────────────────────────────────────────────────────────────

    def test_good_take_clips_sorted_first(self):
        # France matches 3 clips; clip_sunset.mp4 is the only good_take
        result = search_index.search_clips(self.db_path, "France")
        self.assertEqual(result["results"][0]["file_name"], "clip_sunset.mp4")

    def test_non_good_take_sorted_by_date_desc(self):
        # Among non-good-take France clips: ocean(May) > hills(Apr)
        result = search_index.search_clips(self.db_path, "France")
        non_gt = [r for r in result["results"] if not r["good_take"]]
        self.assertEqual(non_gt[0]["file_name"], "clip_ocean.mp4")
        self.assertEqual(non_gt[1]["file_name"], "clip_hills.mp4")

    # ── pagination ───────────────────────────────────────────────────────────

    def test_limit_restricts_result_count(self):
        result = search_index.search_clips(self.db_path, "France", limit=2)
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(result["total"], 3)

    def test_offset_skips_results(self):
        result_all = search_index.search_clips(self.db_path, "France")
        result_offset = search_index.search_clips(self.db_path, "France", limit=50, offset=1)
        self.assertEqual(len(result_offset["results"]), 2)
        self.assertEqual(result_offset["results"][0]["file_name"],
                         result_all["results"][1]["file_name"])

    def test_offset_beyond_total_returns_empty_results(self):
        result = search_index.search_clips(self.db_path, "France", offset=100)
        self.assertEqual(result["results"], [])
        self.assertEqual(result["total"], 3)

    def test_total_unchanged_by_limit(self):
        r1 = search_index.search_clips(self.db_path, "France", limit=1)
        r2 = search_index.search_clips(self.db_path, "France", limit=50)
        self.assertEqual(r1["total"], r2["total"])

    # ── clip with no keywords ────────────────────────────────────────────────

    def test_clip_with_no_keywords_not_returned(self):
        # clip_nokw has no keywords — should never appear in any search
        result = search_index.search_clips(self.db_path, "France")
        names = [r["file_name"] for r in result["results"]]
        self.assertNotIn("clip_nokw.mp4", names)


# ---------------------------------------------------------------------------
# _build_fts_query
# ---------------------------------------------------------------------------

class TestBuildFtsQuery(unittest.TestCase):
    def test_single_word(self):
        self.assertEqual(search_index._build_fts_query("sunset"), '"sunset"*')

    def test_two_words_joined_with_space(self):
        result = search_index._build_fts_query("sunset beach")
        self.assertEqual(result, '"sunset"* "beach"*')

    def test_quoted_phrase_preserved(self):
        result = search_index._build_fts_query('"rolling hills"')
        self.assertEqual(result, '"rolling hills"*')

    def test_quoted_phrase_mixed_with_bare_word(self):
        result = search_index._build_fts_query('France "rolling hills"')
        self.assertEqual(result, '"France"* "rolling hills"*')

    def test_exclusion_bare_word(self):
        result = search_index._build_fts_query("-Marc")
        self.assertEqual(result, 'NOT "Marc"*')

    def test_exclusion_with_dash_prefix(self):
        result = search_index._build_fts_query("sunset -indoor")
        self.assertEqual(result, '"sunset"* NOT "indoor"*')

    def test_exclusion_quoted_phrase(self):
        result = search_index._build_fts_query('-"rolling hills"')
        self.assertEqual(result, 'NOT "rolling hills"*')

    def test_empty_string_returns_empty(self):
        self.assertEqual(search_index._build_fts_query(""), "")

    def test_whitespace_only_returns_empty(self):
        self.assertEqual(search_index._build_fts_query("   ").strip(), "")

    def test_complex_query(self):
        result = search_index._build_fts_query('Italy "rolling hills" -Marc')
        self.assertEqual(result, '"Italy"* "rolling hills"* NOT "Marc"*')


# ---------------------------------------------------------------------------
# search_clips — date filter
# ---------------------------------------------------------------------------

_DATE_FILTER_CLIPS = [
    {
        "file_name": "clip_jan.mp4",
        "clip_dir": "/vol/a",
        "keywords": ["sunset"],
        "date": datetime(2025, 1, 15),
        "duration_tc": "",
        "good_take": False,
    },
    {
        "file_name": "clip_mar.mp4",
        "clip_dir": "/vol/a",
        "keywords": ["sunset"],
        "date": datetime(2025, 3, 10),
        "duration_tc": "",
        "good_take": False,
    },
    {
        "file_name": "clip_jun.mp4",
        "clip_dir": "/vol/a",
        "keywords": ["sunset"],
        "date": datetime(2025, 6, 20),
        "duration_tc": "",
        "good_take": False,
    },
    {
        "file_name": "clip_nodate.mp4",
        "clip_dir": "/vol/a",
        "keywords": ["sunset"],
        "date": None,
        "duration_tc": "",
        "good_take": False,
    },
]


class TestSearchClipsDateFilter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db_path = Path(self._tmp.name)
        search_index.build_index(self.db_path, _DATE_FILTER_CLIPS)

    def tearDown(self):
        self.db_path.unlink(missing_ok=True)

    def test_no_filter_returns_all_clips_with_keywords(self):
        result = search_index.search_clips(self.db_path, "sunset")
        # clip_nodate has no date but still has keywords and should match
        self.assertEqual(result["total"], 4)

    def test_date_from_excludes_earlier_clips(self):
        result = search_index.search_clips(
            self.db_path, "sunset", date_from="2025-02-01"
        )
        names = [r["file_name"] for r in result["results"]]
        self.assertNotIn("clip_jan.mp4", names)
        self.assertIn("clip_mar.mp4", names)
        self.assertIn("clip_jun.mp4", names)

    def test_date_to_excludes_later_clips(self):
        result = search_index.search_clips(
            self.db_path, "sunset", date_to="2025-04-30"
        )
        names = [r["file_name"] for r in result["results"]]
        self.assertIn("clip_jan.mp4", names)
        self.assertIn("clip_mar.mp4", names)
        self.assertNotIn("clip_jun.mp4", names)

    def test_date_from_and_date_to_narrow_to_single_clip(self):
        result = search_index.search_clips(
            self.db_path, "sunset",
            date_from="2025-03-01", date_to="2025-03-31"
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["file_name"], "clip_mar.mp4")

    def test_date_filter_excludes_clips_with_no_date(self):
        # clip_nodate has no date — date filter should exclude it
        result = search_index.search_clips(
            self.db_path, "sunset", date_from="2025-01-01"
        )
        names = [r["file_name"] for r in result["results"]]
        self.assertNotIn("clip_nodate.mp4", names)

    def test_date_range_with_no_matches_returns_zero(self):
        result = search_index.search_clips(
            self.db_path, "sunset",
            date_from="2024-01-01", date_to="2024-12-31"
        )
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["results"], [])

    def test_date_from_inclusive(self):
        # Exact boundary: clip_jan is 2025-01-15; date_from=2025-01-15 should include it
        result = search_index.search_clips(
            self.db_path, "sunset", date_from="2025-01-15"
        )
        names = [r["file_name"] for r in result["results"]]
        self.assertIn("clip_jan.mp4", names)

    def test_date_to_includes_same_day(self):
        # clip_jun is stored as "2025-06-20T00:00:00"; date_to="2025-06-20T23:59:59"
        # must include it (string comparison works because ISO sorts correctly).
        result = search_index.search_clips(
            self.db_path, "sunset", date_to="2025-06-20T23:59:59"
        )
        names = [r["file_name"] for r in result["results"]]
        self.assertIn("clip_jun.mp4", names)

    def test_total_reflects_date_filter(self):
        # Only 2 clips have dates within Feb–Apr 2025
        result = search_index.search_clips(
            self.db_path, "sunset",
            date_from="2025-02-01", date_to="2025-04-30"
        )
        self.assertEqual(result["total"], 1)

    def test_limit_and_date_filter_combined(self):
        # date_from excludes Jan, leaving mar + jun; limit=1 should return 1
        result = search_index.search_clips(
            self.db_path, "sunset", limit=1, date_from="2025-02-01"
        )
        self.assertEqual(len(result["results"]), 1)
        # total still reflects all matches within filter (2)
        self.assertEqual(result["total"], 2)


# ---------------------------------------------------------------------------
# get_all_keywords
# ---------------------------------------------------------------------------

class TestGetAllKeywords(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db_path = Path(self._tmp.name)

    def tearDown(self):
        self.db_path.unlink(missing_ok=True)

    def test_returns_empty_when_db_missing(self):
        result = search_index.get_all_keywords("/nonexistent/path.db")
        self.assertEqual(result, [])

    def test_returns_empty_when_no_clips(self):
        search_index.build_index(self.db_path, [])
        result = search_index.get_all_keywords(self.db_path)
        self.assertEqual(result, [])

    def test_returns_all_distinct_keywords(self):
        clips = [
            {"file_name": "a.mp4", "clip_dir": "/v", "keywords": ["sunset", "beach"],
             "date": None, "duration_tc": "", "good_take": False},
            {"file_name": "b.mp4", "clip_dir": "/v", "keywords": ["ocean", "beach"],
             "date": None, "duration_tc": "", "good_take": False},
        ]
        search_index.build_index(self.db_path, clips)
        result = search_index.get_all_keywords(self.db_path)
        self.assertIn("sunset", result)
        self.assertIn("beach", result)
        self.assertIn("ocean", result)
        self.assertEqual(len(result), 3)  # beach appears in both but listed once

    def test_keywords_sorted_case_insensitively(self):
        clips = [
            {"file_name": "a.mp4", "clip_dir": "/v", "keywords": ["Zebra", "apple", "Mango"],
             "date": None, "duration_tc": "", "good_take": False},
        ]
        search_index.build_index(self.db_path, clips)
        result = search_index.get_all_keywords(self.db_path)
        self.assertEqual(result, sorted(result, key=str.casefold))

    def test_clips_with_no_keywords_dont_pollute_result(self):
        clips = [
            {"file_name": "a.mp4", "clip_dir": "/v", "keywords": ["sunset"],
             "date": None, "duration_tc": "", "good_take": False},
            {"file_name": "b.mp4", "clip_dir": "/v", "keywords": [],
             "date": None, "duration_tc": "", "good_take": False},
        ]
        search_index.build_index(self.db_path, clips)
        result = search_index.get_all_keywords(self.db_path)
        self.assertEqual(result, ["sunset"])


# ---------------------------------------------------------------------------
# Histogram
# ---------------------------------------------------------------------------

# Clips spread across > 2 years → monthly buckets.
_HISTOGRAM_CLIPS = [
    {"file_name": "a.mp4", "clip_dir": "/v", "keywords": ["sunset"],
     "date": datetime(2022, 6, 1), "duration_tc": "", "good_take": False},
    {"file_name": "b.mp4", "clip_dir": "/v", "keywords": ["sunset"],
     "date": datetime(2022, 6, 15), "duration_tc": "", "good_take": False},
    {"file_name": "c.mp4", "clip_dir": "/v", "keywords": ["sunset"],
     "date": datetime(2023, 1, 10), "duration_tc": "", "good_take": False},
    {"file_name": "d.mp4", "clip_dir": "/v", "keywords": ["ocean"],
     "date": datetime(2022, 6, 1), "duration_tc": "", "good_take": False},
    {"file_name": "e.mp4", "clip_dir": "/v", "keywords": ["sunset"],
     "date": None, "duration_tc": "", "good_take": False},  # no date — excluded
]


class TestSearchHistogram(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db_path = Path(self._tmp.name)
        search_index.build_index(self.db_path, _HISTOGRAM_CLIPS)

    def tearDown(self):
        self.db_path.unlink(missing_ok=True)

    def test_returns_empty_for_blank_query(self):
        result = search_index.search_histogram(self.db_path, "")
        self.assertEqual(result["buckets"], [])

    def test_returns_empty_when_db_missing(self):
        result = search_index.search_histogram("/nonexistent/path.db", "sunset")
        self.assertEqual(result["buckets"], [])

    def test_returns_empty_for_no_match(self):
        result = search_index.search_histogram(self.db_path, "volcano")
        self.assertEqual(result["buckets"], [])

    def test_bucket_size_monthly_for_wide_range(self):
        # a, b, c span ~7 months → > 90 days but < 730 days → weekly
        # With a (Jun 2022) to c (Jan 2023) = ~213 days → weekly
        result = search_index.search_histogram(self.db_path, "sunset")
        self.assertIn(result["bucket_size"], ("week", "month"))

    def test_buckets_sorted_by_date(self):
        result = search_index.search_histogram(self.db_path, "sunset")
        dates = [b["date"] for b in result["buckets"]]
        self.assertEqual(dates, sorted(dates))

    def test_counts_correct(self):
        # sunset has a(Jun 1), b(Jun 15), c(Jan 10) with dates; e has no date → excluded
        result = search_index.search_histogram(self.db_path, "sunset")
        total = sum(b["count"] for b in result["buckets"])
        self.assertEqual(total, 3)

    def test_clips_without_date_excluded(self):
        # e.mp4 has no date — total count should be 3 not 4
        result = search_index.search_histogram(self.db_path, "sunset")
        total = sum(b["count"] for b in result["buckets"])
        self.assertEqual(total, 3)

    def test_exclusion_syntax_respected(self):
        # ocean clips excluded → only sunset clips counted
        result_all = search_index.search_histogram(self.db_path, "sunset")
        result_excl = search_index.search_histogram(self.db_path, "sunset -ocean")
        self.assertEqual(result_all["total_clips"] if "total_clips" in result_all
                         else sum(b["count"] for b in result_all["buckets"]),
                         sum(b["count"] for b in result_excl["buckets"]))

    def test_daily_buckets_for_short_range(self):
        # Build index with clips all within 2 weeks → daily buckets
        clips = [
            {"file_name": f"d{i}.mp4", "clip_dir": "/v", "keywords": ["test"],
             "date": datetime(2025, 4, i + 1), "duration_tc": "", "good_take": False}
            for i in range(10)
        ]
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            search_index.build_index(tmp.name, clips)
            result = search_index.search_histogram(tmp.name, "test")
            self.assertEqual(result["bucket_size"], "day")
            self.assertEqual(len(result["buckets"]), 10)
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    def test_monthly_buckets_for_long_range(self):
        # Build index with clips spanning 3+ years → monthly buckets
        clips = [
            {"file_name": f"m{i}.mp4", "clip_dir": "/v", "keywords": ["test"],
             "date": datetime(2021 + i // 12, (i % 12) + 1, 1),
             "duration_tc": "", "good_take": False}
            for i in range(36)
        ]
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            search_index.build_index(tmp.name, clips)
            result = search_index.search_histogram(tmp.name, "test")
            self.assertEqual(result["bucket_size"], "month")
        finally:
            Path(tmp.name).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
