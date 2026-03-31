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


class TestSearchBuildProxyAttachment(unittest.TestCase):
    """Verify that proxy_path is matched by (file_name, clip_dir), not just file_name."""

    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_duplicate_filenames_get_correct_proxy_paths(self):
        """Two clips named C0040.MP4 in different directories must each get
        the proxy path that matches their own clip_dir, not the other's."""
        import csv, io, tempfile, os
        from pathlib import Path
        from unittest.mock import patch

        # Two clips with the same filename but different directories.
        csv_text = (
            "File Name,Clip Directory,Keywords,Date Modified,Duration TC,Good Take\n"
            "C0040.MP4,/Volumes/DriveA/Video,,Wed Jan  1 10:00:00 2025,00:00:10:00,0\n"
            "C0040.MP4,/Volumes/DriveB/Video,,Wed Jan  1 10:00:00 2025,00:00:10:00,0\n"
        )

        mock_resolve = object()

        def fake_export(resolve, csv_path):
            with open(csv_path, "w", encoding="utf-16", newline="") as f:
                f.write(csv_text)
            return True

        # proxy_map keyed by (file_name, clip_dir)
        fake_proxy_map = {
            ("C0040.MP4", "/Volumes/DriveA/Video"): "/proxy/DriveA/C0040.MP4",
            ("C0040.MP4", "/Volumes/DriveB/Video"): "/proxy/DriveB/C0040.MP4",
        }

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            with patch("resolve_api.get_resolve", return_value=mock_resolve), \
                 patch("resolve_api.export_metadata", side_effect=fake_export), \
                 patch("resolve_api.collect_proxy_paths", return_value=fake_proxy_map), \
                 patch("resolve_api.get_project_name", return_value="TestProject"), \
                 patch("app._SEARCH_DB_PATH", db_path):
                res = self.client.post("/search/api/build")
            self.assertEqual(res.status_code, 200)

            # Query and verify each clip has its own proxy path.
            import sqlite3
            con = sqlite3.connect(db_path)
            rows = {
                row[0]: row[1]
                for row in con.execute(
                    "SELECT clip_dir, proxy_path FROM clips"
                ).fetchall()
            }
            con.close()
            self.assertEqual(rows["/Volumes/DriveA/Video"], "/proxy/DriveA/C0040.MP4")
            self.assertEqual(rows["/Volumes/DriveB/Video"], "/proxy/DriveB/C0040.MP4")
        finally:
            Path(db_path).unlink(missing_ok=True)

    def test_clip_without_proxy_gets_none(self):
        """A clip whose (file_name, clip_dir) is not in proxy_map gets proxy_path=None."""
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        csv_text = (
            "File Name,Clip Directory,Keywords,Date Modified,Duration TC,Good Take\n"
            "clip.mp4,/vol/dir,sunset,Wed Jan  1 10:00:00 2025,00:00:10:00,0\n"
        )

        mock_resolve = object()

        def fake_export(resolve, csv_path):
            with open(csv_path, "w", encoding="utf-16", newline="") as f:
                f.write(csv_text)
            return True

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            with patch("resolve_api.get_resolve", return_value=mock_resolve), \
                 patch("resolve_api.export_metadata", side_effect=fake_export), \
                 patch("resolve_api.collect_proxy_paths", return_value={}), \
                 patch("resolve_api.get_project_name", return_value=""), \
                 patch("app._SEARCH_DB_PATH", db_path):
                res = self.client.post("/search/api/build")
            self.assertEqual(res.status_code, 200)

            import sqlite3
            con = sqlite3.connect(db_path)
            proxy_path = con.execute("SELECT proxy_path FROM clips").fetchone()[0]
            con.close()
            self.assertIsNone(proxy_path)
        finally:
            Path(db_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
