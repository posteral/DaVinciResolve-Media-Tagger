"""Tests for Shot Finder routes — M1.3: /search/api/status and /search/api/build."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import app as flask_app
import search_index


_SAMPLE_CLIPS = [
    {
        "file_name": "clip_a.mp4",
        "clip_dir": "/vol/dir",
        "keywords": ["sunset", "beach"],
        "date": datetime(2025, 1, 1),
        "duration_tc": "00:00:10:00",
    },
    {
        "file_name": "clip_b.mp4",
        "clip_dir": "/vol/dir",
        "keywords": ["ocean"],
        "date": None,
        "duration_tc": "00:00:05:00",
    },
]


class TestSearchStatusRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_returns_empty_when_db_missing(self):
        with patch("app._SEARCH_DB_PATH", "/nonexistent/search.db"):
            res = self.client.get("/search/api/status")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["state"], "empty")
        self.assertEqual(data["clip_count"], 0)
        self.assertIsNone(data["built_at"])

    def test_returns_ready_after_build(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            search_index.build_index(db_path, _SAMPLE_CLIPS)
            with patch("app._SEARCH_DB_PATH", db_path):
                res = self.client.get("/search/api/status")
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data["state"], "ready")
            self.assertEqual(data["clip_count"], 2)
            self.assertIsNotNone(data["built_at"])
        finally:
            Path(db_path).unlink(missing_ok=True)


class TestSearchBuildRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_returns_503_when_resolve_unavailable(self):
        with patch("resolve_api.get_resolve", return_value=None):
            res = self.client.post("/search/api/build")
        self.assertEqual(res.status_code, 503)
        self.assertIn("error", res.get_json())

    def test_returns_500_when_export_fails(self):
        import resolve_api as ra
        mock_resolve = object()
        with patch("resolve_api.get_resolve", return_value=mock_resolve), \
             patch("resolve_api.export_metadata", return_value=False):
            res = self.client.post("/search/api/build")
        self.assertEqual(res.status_code, 500)
        self.assertIn("error", res.get_json())

    def test_returns_status_on_success(self):
        import csv, io

        csv_text = (
            "File Name,Clip Directory,Keywords,Date Modified,Duration TC\n"
            "clip_a.mp4,/vol/dir,sunset,Wed Jan  1 10:00:00 2025,00:00:10:00\n"
        )

        mock_resolve = object()

        def fake_export(resolve, csv_path):
            # Write a minimal UTF-16 CSV to the temp path.
            with open(csv_path, "w", encoding="utf-16", newline="") as f:
                f.write(csv_text)
            return True

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            with patch("resolve_api.get_resolve", return_value=mock_resolve), \
                 patch("resolve_api.export_metadata", side_effect=fake_export), \
                 patch("app._SEARCH_DB_PATH", db_path):
                res = self.client.post("/search/api/build")
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data["state"], "ready")
            self.assertEqual(data["clip_count"], 1)
        finally:
            Path(db_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
