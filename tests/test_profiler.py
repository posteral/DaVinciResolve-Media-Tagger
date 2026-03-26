"""Tests for profiler.py — accumulation, stats, summary, and dump."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import profiler


def _reset():
    """Reset profiler session state between tests."""
    with profiler._lock:
        profiler._session["navigate"].clear()
        profiler._session["suggest_bg"].clear()
        profiler._session["filmstrip"].clear()
        profiler._session["filmstrip_cache_hits"] = 0


class TestStats(unittest.TestCase):
    def test_empty_list(self):
        self.assertEqual(profiler._stats([]), {"n": 0})

    def test_single_value(self):
        s = profiler._stats([100.0])
        self.assertEqual(s["n"], 1)
        self.assertEqual(s["min_ms"], 100)
        self.assertEqual(s["max_ms"], 100)
        self.assertEqual(s["avg_ms"], 100)

    def test_multiple_values(self):
        s = profiler._stats([100.0, 200.0, 300.0, 400.0, 500.0])
        self.assertEqual(s["n"], 5)
        self.assertEqual(s["min_ms"], 100)
        self.assertEqual(s["max_ms"], 500)
        self.assertEqual(s["avg_ms"], 300)

    def test_rounds_to_int(self):
        s = profiler._stats([1.4, 1.6])
        self.assertIsInstance(s["avg_ms"], int)

    def test_p90_p95_within_range(self):
        values = list(range(1, 101))  # 1..100
        s = profiler._stats([float(v) for v in values])
        self.assertLessEqual(s["p90_ms"], 100)
        self.assertLessEqual(s["p95_ms"], 100)
        self.assertGreaterEqual(s["p90_ms"], s["p50_ms"])
        self.assertGreaterEqual(s["p95_ms"], s["p90_ms"])


class TestRecordNavigate(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_appends_entry(self):
        profiler.record_navigate(1000.0, 700.0)
        self.assertEqual(len(profiler._session["navigate"]), 1)

    def test_stores_all_fields(self):
        timing = {
            "lock_wait_ms": 50.0,
            "resolve_folder_ms": 100.0,
            "folder_cache_ms": 0.0,
            "cache_miss": True,
            "set_selected_ms": 80.0,
            "post_nav_ipc_ms": 40.0,
        }
        profiler.record_navigate(1000.0, 700.0, timing)
        entry = profiler._session["navigate"][0]
        self.assertEqual(entry["total_ms"], 1000)
        self.assertEqual(entry["resolve_ipc_ms"], 700)
        self.assertEqual(entry["lock_wait_ms"], 50)
        self.assertEqual(entry["resolve_folder_ms"], 100)
        self.assertEqual(entry["folder_cache_ms"], 0)
        self.assertTrue(entry["cache_miss"])
        self.assertEqual(entry["set_selected_ms"], 80)
        self.assertEqual(entry["post_nav_ipc_ms"], 40)

    def test_missing_timing_fields_default_to_zero(self):
        profiler.record_navigate(500.0, 400.0, {})
        entry = profiler._session["navigate"][0]
        self.assertEqual(entry["lock_wait_ms"], 0)
        self.assertEqual(entry["resolve_folder_ms"], 0)
        self.assertFalse(entry["cache_miss"])

    def test_none_timing_defaults_to_zero(self):
        profiler.record_navigate(500.0, 400.0, None)
        entry = profiler._session["navigate"][0]
        self.assertEqual(entry["lock_wait_ms"], 0)


class TestRecordFilmstrip(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_appends_entry(self):
        profiler.record_filmstrip(400.0, 60.0, 330.0, 3.0, 5)
        self.assertEqual(len(profiler._session["filmstrip"]), 1)
        entry = profiler._session["filmstrip"][0]
        self.assertEqual(entry["total_ms"], 400)
        self.assertEqual(entry["probe_ms"], 60)
        self.assertEqual(entry["extract_ms"], 330)
        self.assertEqual(entry["encode_ms"], 3)
        self.assertEqual(entry["frames"], 5)


class TestRecordFilmstripCacheHit(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_increments_counter(self):
        profiler.record_filmstrip_cache_hit()
        profiler.record_filmstrip_cache_hit()
        self.assertEqual(profiler._session["filmstrip_cache_hits"], 2)


class TestRecordSuggestBg(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_appends_value(self):
        profiler.record_suggest_bg(850.0)
        self.assertEqual(profiler._session["suggest_bg"], [850])


class TestSummary(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_empty_session(self):
        s = profiler.summary()
        self.assertEqual(s["navigate"]["n"], 0)
        self.assertEqual(s["suggest_bg"]["n"], 0)
        self.assertEqual(s["filmstrip"]["n"], 0)
        self.assertEqual(s["filmstrip"]["cache_hits"], 0)
        self.assertEqual(s["filmstrip"]["cache_hit_rate"], "n/a")
        self.assertEqual(s["navigate"]["cache_miss_rate"], "n/a")

    def test_navigate_stats_populated(self):
        profiler.record_navigate(1000.0, 700.0, {"lock_wait_ms": 200.0, "resolve_folder_ms": 150.0,
                                                  "folder_cache_ms": 0.0, "cache_miss": False,
                                                  "set_selected_ms": 70.0, "post_nav_ipc_ms": 40.0})
        profiler.record_navigate(2000.0, 800.0, {"lock_wait_ms": 1000.0, "resolve_folder_ms": 200.0,
                                                  "folder_cache_ms": 500.0, "cache_miss": True,
                                                  "set_selected_ms": 80.0, "post_nav_ipc_ms": 50.0})
        s = profiler.summary()
        self.assertEqual(s["navigate"]["n"], 2)
        self.assertEqual(s["navigate"]["cache_misses"], 1)
        self.assertEqual(s["navigate"]["cache_miss_rate"], "50.0%")
        self.assertIn("lock_wait", s["navigate"])
        self.assertIn("resolve_folder", s["navigate"])
        self.assertIn("set_selected", s["navigate"])
        self.assertIn("post_nav_ipc", s["navigate"])
        self.assertIn("overhead", s["navigate"])

    def test_filmstrip_cache_hit_rate(self):
        profiler.record_filmstrip(300.0, 50.0, 240.0, 3.0, 5)
        profiler.record_filmstrip_cache_hit()
        s = profiler.summary()
        self.assertEqual(s["filmstrip"]["cache_hit_rate"], "50.0%")

    def test_raw_section_present(self):
        profiler.record_navigate(500.0, 400.0)
        s = profiler.summary()
        self.assertIn("navigate", s["raw"])
        self.assertIn("suggest_bg", s["raw"])
        self.assertIn("filmstrip", s["raw"])

    def test_generated_at_present(self):
        s = profiler.summary()
        self.assertIn("generated_at", s)
        self.assertIn("session_started_at", s)


class TestDump(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_writes_valid_json(self):
        profiler.record_navigate(1000.0, 700.0)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "report.json")
            returned_path = profiler.dump(path)
            self.assertEqual(returned_path, path)
            data = json.loads(Path(path).read_text())
        self.assertIn("navigate", data)
        self.assertIn("raw", data)

    def test_auto_path_contains_profile_prefix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("profiler.os.path.dirname", return_value=tmpdir):
                path = profiler.dump()
        self.assertIn("profile_", Path(path).name)
        Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
