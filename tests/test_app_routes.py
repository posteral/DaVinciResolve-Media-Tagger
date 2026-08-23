"""Tests for the app.py routes added this session: Tag Timeline and
Reconcile Keywords.

These patch resolve_api's functions directly rather than building a full
Resolve mock chain — the underlying logic already has dedicated tests in
test_resolve_api.py. Here we're testing the Flask route's own
responsibilities: request-body parsing/defaulting, and error-to-status-code
mapping (RuntimeError -> 404, anything else -> 500).
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import app as flask_app


class TestPageRoutes(unittest.TestCase):
    """Smoke tests — every page added/moved this session actually renders."""

    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_hub_page_loads(self):
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_tagger_page_loads(self):
        self.assertEqual(self.client.get("/tagger").status_code, 200)

    def test_timeline_tag_page_loads(self):
        self.assertEqual(self.client.get("/timeline-tag").status_code, 200)

    def test_reconcile_page_loads(self):
        self.assertEqual(self.client.get("/reconcile").status_code, 200)


class TestTagTimelineRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_dry_run_defaults_to_true_when_omitted(self):
        with patch("app._get_resolve", return_value=object()), \
             patch("resolve_api.sync_timeline_used_tag") as mock_sync:
            mock_sync.return_value = {"added": [], "removed": [], "applied": False}
            self.client.post("/api/timeline/tag-used-media", json={})
        self.assertTrue(mock_sync.call_args.kwargs["dry_run"])

    def test_custom_tag_and_dry_run_passed_through(self):
        with patch("app._get_resolve", return_value=object()), \
             patch("resolve_api.sync_timeline_used_tag") as mock_sync:
            mock_sync.return_value = {"added": [], "removed": [], "applied": True}
            self.client.post(
                "/api/timeline/tag-used-media", json={"tag": "Used:X", "dry_run": False}
            )
        self.assertEqual(mock_sync.call_args.kwargs["tag"], "Used:X")
        self.assertFalse(mock_sync.call_args.kwargs["dry_run"])

    def test_runtime_error_returns_404(self):
        with patch("app._get_resolve", return_value=object()), \
             patch("resolve_api.sync_timeline_used_tag", side_effect=RuntimeError("no timeline")):
            res = self.client.post("/api/timeline/tag-used-media", json={})
        self.assertEqual(res.status_code, 404)
        self.assertIn("no timeline", res.get_json()["error"])

    def test_unexpected_exception_returns_500(self):
        with patch("app._get_resolve", return_value=object()), \
             patch("resolve_api.sync_timeline_used_tag", side_effect=Exception("boom")):
            res = self.client.post("/api/timeline/tag-used-media", json={})
        self.assertEqual(res.status_code, 500)

    def test_success_returns_result_json(self):
        with patch("app._get_resolve", return_value=object()), \
             patch("resolve_api.sync_timeline_used_tag") as mock_sync:
            mock_sync.return_value = {"added": ["a.mp4"], "removed": [], "applied": True}
            res = self.client.post("/api/timeline/tag-used-media", json={"dry_run": False})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["added"], ["a.mp4"])


class TestReconcileRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_dry_run_defaults_to_true_when_omitted(self):
        with patch("app._get_resolve", return_value=object()), \
             patch("resolve_api.reconcile_project_keywords") as mock_reconcile:
            mock_reconcile.return_value = {"updated": [], "unmatched": []}
            self.client.post("/api/reconcile", json={"target_project": "X"})
        self.assertTrue(mock_reconcile.call_args.kwargs["dry_run"])

    def test_target_project_and_tag_passed_through(self):
        with patch("app._get_resolve", return_value=object()), \
             patch("resolve_api.reconcile_project_keywords") as mock_reconcile:
            mock_reconcile.return_value = {"updated": [], "unmatched": []}
            self.client.post(
                "/api/reconcile",
                json={"target_project": "Master Catalog", "tag": "Used:X", "dry_run": False},
            )
        args, kwargs = mock_reconcile.call_args
        self.assertEqual(args[1], "Master Catalog")
        self.assertEqual(kwargs["tag"], "Used:X")
        self.assertFalse(kwargs["dry_run"])

    def test_runtime_error_returns_404(self):
        with patch("app._get_resolve", return_value=object()), \
             patch("resolve_api.reconcile_project_keywords", side_effect=RuntimeError("not found")):
            res = self.client.post("/api/reconcile", json={"target_project": "X"})
        self.assertEqual(res.status_code, 404)
        self.assertIn("not found", res.get_json()["error"])

    def test_unexpected_exception_returns_500(self):
        with patch("app._get_resolve", return_value=object()), \
             patch("resolve_api.reconcile_project_keywords", side_effect=Exception("boom")):
            res = self.client.post("/api/reconcile", json={"target_project": "X"})
        self.assertEqual(res.status_code, 500)

    def test_success_returns_result_json(self):
        with patch("app._get_resolve", return_value=object()), \
             patch("resolve_api.reconcile_project_keywords") as mock_reconcile:
            mock_reconcile.return_value = {
                "updated": [{"clip": "a.mp4", "added": ["Used:X"]}], "unmatched": [],
            }
            res = self.client.post(
                "/api/reconcile", json={"target_project": "X", "dry_run": False}
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["updated"][0]["clip"], "a.mp4")


class TestProjectsRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_returns_list_projects_result(self):
        with patch("app._get_resolve", return_value=object()), \
             patch("resolve_api.list_projects", return_value={"current": "X", "projects": []}):
            res = self.client.get("/api/projects")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["current"], "X")

    def test_exception_returns_500(self):
        with patch("app._get_resolve", side_effect=Exception("boom")):
            res = self.client.get("/api/projects")
        self.assertEqual(res.status_code, 500)


class TestReconcileConfigRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_returns_configured_default(self):
        with patch("app._DEFAULT_RECONCILE_TARGET", "Media Archive"):
            res = self.client.get("/api/config/reconcile")
        self.assertEqual(res.get_json()["default_target_project"], "Media Archive")

    def test_returns_empty_string_when_unconfigured(self):
        with patch("app._DEFAULT_RECONCILE_TARGET", ""):
            res = self.client.get("/api/config/reconcile")
        self.assertEqual(res.get_json()["default_target_project"], "")


if __name__ == "__main__":
    unittest.main()
