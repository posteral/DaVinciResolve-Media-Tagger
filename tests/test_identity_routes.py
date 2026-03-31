from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

import app as flask_app


SAMPLE_DETECTION = {
    "mean_embedding": [0.1] * 128,
    "best_crop": b"fakejpeg",
    "occurrence_count": 2,
    "status": "known",
    "identity_id": "id-abc",
    "display_name": "Alice",
    "keyword_string": "Alice",
    "distance": 0.42,
}

SAMPLE_REGISTRY = {
    "version": 1,
    "identities": [
        {
            "identity_id": "id-abc",
            "display_name": "Alice",
            "keyword_string": "Alice",
            "embeddings": [[0.1] * 128],
            "thumbnail_path": "",
        }
    ],
}


class TestDetectIdentitiesRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_returns_detections(self):
        with (
            patch("resolve_api.frames_from_file_path", return_value=[b"frame1", b"frame2"]),
            patch("identity_registry.load_registry", return_value=SAMPLE_REGISTRY),
            patch("identity_recognition.run_detection_pipeline", return_value=[SAMPLE_DETECTION]),
        ):
            resp = self.client.post(
                "/api/clip/detect-identities",
                json={"path": "/fake/clip.mov"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data["detections"]), 1)
        det = data["detections"][0]
        self.assertIn("face_token", det)
        self.assertEqual(det["status"], "known")
        self.assertEqual(det["display_name"], "Alice")
        self.assertEqual(det["occurrence_count"], 2)
        # face_token should be stored in caches
        token = det["face_token"]
        self.assertIn(token, flask_app._face_crop_cache)
        self.assertIn(token, flask_app._detection_cache)

    def test_returns_empty_when_no_frames(self):
        with patch("resolve_api.frames_from_file_path", return_value=[]):
            resp = self.client.post(
                "/api/clip/detect-identities",
                json={"path": "/fake/clip.mov"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["detections"], [])

    def test_returns_400_when_path_missing(self):
        resp = self.client.post("/api/clip/detect-identities", json={})
        self.assertEqual(resp.status_code, 400)

    def test_returns_empty_when_no_faces_detected(self):
        with (
            patch("resolve_api.frames_from_file_path", return_value=[b"frame"]),
            patch("identity_registry.load_registry", return_value={"version": 1, "identities": []}),
            patch("identity_recognition.run_detection_pipeline", return_value=[]),
        ):
            resp = self.client.post(
                "/api/clip/detect-identities",
                json={"path": "/fake/clip.mov"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["detections"], [])


class TestFaceCropRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_returns_crop_for_valid_token(self):
        flask_app._face_crop_cache["test-token-123"] = b"fakejpegbytes"
        resp = self.client.get("/api/clip/face-crop?token=test-token-123")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, b"fakejpegbytes")
        self.assertEqual(resp.content_type, "image/jpeg")

    def test_returns_404_for_unknown_token(self):
        resp = self.client.get("/api/clip/face-crop?token=no-such-token")
        self.assertEqual(resp.status_code, 404)

    def test_returns_404_when_token_missing(self):
        resp = self.client.get("/api/clip/face-crop")
        self.assertEqual(resp.status_code, 404)


class TestListIdentitiesRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_returns_identity_list(self):
        with patch("identity_registry.load_registry", return_value=SAMPLE_REGISTRY):
            resp = self.client.get("/api/identities")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data["identities"]), 1)
        self.assertEqual(data["identities"][0]["display_name"], "Alice")
        self.assertNotIn("embeddings", data["identities"][0])

    def test_returns_empty_list_when_no_identities(self):
        with patch("identity_registry.load_registry", return_value={"version": 1, "identities": []}):
            resp = self.client.get("/api/identities")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["identities"], [])


class TestConfirmIdentitiesRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()
        # Seed caches with a known token
        flask_app._face_crop_cache["tok-1"] = b"crop"
        flask_app._detection_cache["tok-1"] = [0.1] * 128

    def _post(self, assignments):
        return self.client.post(
            "/api/identities/confirm",
            json={"assignments": assignments},
        )

    def test_new_identity_created_and_keyword_returned(self):
        with (
            patch("identity_registry.load_registry", return_value={"version": 1, "identities": []}),
            patch("identity_registry.find_identity_by_name", return_value=None),
            patch("identity_registry.add_identity", return_value=({"version": 1, "identities": []}, "new-id")) as mock_add,
            patch("identity_registry.save_registry"),
        ):
            resp = self._post([{
                "face_token": "tok-1",
                "display_name": "Bob",
                "keyword_string": "Bob",
                "identity_id": None,
                "is_new_identity": True,
                "add_as_keyword": True,
            }])
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Bob", resp.get_json()["keywords_added"])
        mock_add.assert_called_once()

    def test_existing_identity_embedding_updated(self):
        with (
            patch("identity_registry.load_registry", return_value=SAMPLE_REGISTRY),
            patch("identity_registry.update_identity_embedding", return_value=SAMPLE_REGISTRY) as mock_update,
            patch("identity_registry.save_registry"),
        ):
            resp = self._post([{
                "face_token": "tok-1",
                "display_name": "Alice",
                "keyword_string": "Alice",
                "identity_id": "id-abc",
                "is_new_identity": False,
                "add_as_keyword": True,
            }])
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Alice", resp.get_json()["keywords_added"])
        mock_update.assert_called_once()

    def test_add_as_keyword_false_excludes_from_result(self):
        with (
            patch("identity_registry.load_registry", return_value={"version": 1, "identities": []}),
            patch("identity_registry.find_identity_by_name", return_value=None),
            patch("identity_registry.add_identity", return_value=({"version": 1, "identities": []}, "new-id")),
            patch("identity_registry.save_registry"),
        ):
            resp = self._post([{
                "face_token": "tok-1",
                "display_name": "Carol",
                "keyword_string": "Carol",
                "identity_id": None,
                "is_new_identity": True,
                "add_as_keyword": False,
            }])
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("Carol", resp.get_json()["keywords_added"])

    def test_skips_assignment_with_empty_display_name(self):
        with (
            patch("identity_registry.load_registry", return_value={"version": 1, "identities": []}),
            patch("identity_registry.save_registry"),
        ):
            resp = self._post([{
                "face_token": "tok-1",
                "display_name": "",
                "keyword_string": "",
                "identity_id": None,
                "is_new_identity": True,
                "add_as_keyword": True,
            }])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["keywords_added"], [])

    def test_typed_existing_name_reuses_identity(self):
        """Typing an existing name rather than selecting from datalist should
        update the existing identity, not create a new one."""
        existing = {"identity_id": "id-abc", "display_name": "Alice",
                    "keyword_string": "Alice", "embeddings": [[0.1] * 128]}
        with (
            patch("identity_registry.load_registry", return_value=SAMPLE_REGISTRY),
            patch("identity_registry.find_identity_by_name", return_value=existing),
            patch("identity_registry.update_identity_embedding", return_value=SAMPLE_REGISTRY) as mock_update,
            patch("identity_registry.add_identity") as mock_add,
            patch("identity_registry.save_registry"),
        ):
            resp = self._post([{
                "face_token": "tok-1",
                "display_name": "Alice",
                "keyword_string": "Alice",
                "identity_id": None,
                "is_new_identity": True,  # browser thought it was new
                "add_as_keyword": True,
            }])
        self.assertEqual(resp.status_code, 200)
        mock_add.assert_not_called()
        mock_update.assert_called_once()

    def test_returns_400_for_invalid_assignments(self):
        resp = self.client.post("/api/identities/confirm", json={"assignments": "bad"})
        self.assertEqual(resp.status_code, 400)


class TestIndexRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_returns_200_with_html(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"html", resp.data.lower())


class TestClipRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def _make_item(self, name="clip.mov", keywords=None, proxy_path="/proxy/clip.mxf"):
        item = MagicMock()
        item.GetName.return_value = name
        item.GetMediaId.return_value = "media-001"
        kws = keywords or ["alpha", "beta"]
        item.GetMetadata.side_effect = lambda k=None: (
            {"Keywords": ", ".join(kws)} if k is None else (", ".join(kws) if k == "Keywords" else None)
        )
        item.GetClipProperty.side_effect = lambda k: (
            ", ".join(kws) if k == "Keywords"
            else (proxy_path if k == "Proxy Media Path" else "")
        )
        return item

    def test_returns_clip_data(self):
        item = self._make_item()
        with patch("resolve_api.get_selected_media_pool_item", return_value=item), \
             patch("app._get_resolve", return_value=MagicMock()):
            resp = self.client.get("/api/clip")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("clip", data)
        self.assertIn("keywords", data)
        self.assertIn("file_path", data)
        self.assertIn("no_proxy", data)

    def test_returns_404_when_no_item(self):
        with patch("resolve_api.get_selected_media_pool_item", return_value=None), \
             patch("app._get_resolve", return_value=MagicMock()):
            resp = self.client.get("/api/clip")
        self.assertEqual(resp.status_code, 404)

    def test_returns_500_on_exception(self):
        with patch("app._get_resolve", side_effect=RuntimeError("Resolve busy")):
            resp = self.client.get("/api/clip")
        self.assertEqual(resp.status_code, 500)


class TestClipThumbnailRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_returns_204_when_no_path(self):
        with patch("resolve_api.get_selected_media_pool_item", return_value=None), \
             patch("app._get_resolve", return_value=MagicMock()):
            resp = self.client.get("/api/clip/thumbnail")
        self.assertEqual(resp.status_code, 204)

    def test_returns_200_png_with_path_param(self):
        with patch("resolve_api.thumbnail_from_file_path", return_value=b"PNGDATA"):
            resp = self.client.get("/api/clip/thumbnail?path=/proxy/clip.mxf")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content_type, "image/png")
        self.assertEqual(resp.data, b"PNGDATA")

    def test_returns_204_when_ffmpeg_returns_none(self):
        with patch("resolve_api.thumbnail_from_file_path", return_value=None):
            resp = self.client.get("/api/clip/thumbnail?path=/proxy/no_proxy_clip.mxf")
        self.assertEqual(resp.status_code, 204)


class TestClipFilmstripRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_returns_204_when_no_path(self):
        resp = self.client.get("/api/clip/filmstrip")
        self.assertEqual(resp.status_code, 204)

    def test_returns_frames_json(self):
        fake_frames = [b"PNG1", b"PNG2", b"PNG3"]
        with patch("resolve_api.frames_from_file_path_timed", return_value=(fake_frames, 10.0, 50.0)):
            resp = self.client.get("/api/clip/filmstrip?path=/proxy/clip.mxf")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("frames", data)
        self.assertEqual(len(data["frames"]), 3)

    def test_empty_frames_returns_empty_list(self):
        with patch("resolve_api.frames_from_file_path_timed", return_value=([], 0.0, 0.0)):
            resp = self.client.get("/api/clip/filmstrip?path=/proxy/clip.mxf")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["frames"], [])


class TestClipSuggestionsRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_uses_cached_suggestions_when_media_id_matches(self):
        # Pre-seed the module-level last_suggestions
        import resolve_api as ra
        ra._last_suggestions = ("media-abc", ["close_kw", "nearby"])
        resp = self.client.get("/api/clip/suggestions?media_id=media-abc")
        ra._last_suggestions = None
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("close_kw", data["suggestions"])

    def test_calls_suggest_keywords_when_not_cached(self):
        import resolve_api as ra
        ra._last_suggestions = None
        with patch("resolve_api.suggest_keywords", return_value=(["kw1", "kw2"], {})), \
             patch("app._get_resolve", return_value=MagicMock()):
            resp = self.client.get("/api/clip/suggestions")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("suggestions", resp.get_json())


class TestAiSuggestionRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_post_with_path_returns_suggestions(self):
        with patch("resolve_api.ai_suggest_keywords", return_value=["mountain", "sunset"]):
            resp = self.client.post(
                "/api/clip/ai-suggestion",
                json={"path": "/proxy/clip.mxf", "keywords": ["beach"], "suggestions": [], "catalog": []},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("suggestions", data)
        self.assertIn("mountain", data["suggestions"])

    def test_get_with_path_returns_suggestions(self):
        with patch("resolve_api.ai_suggest_keywords", return_value=["waterfall"]):
            resp = self.client.get("/api/clip/ai-suggestion?path=/proxy/clip.mxf")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("waterfall", resp.get_json()["suggestions"])

    def test_returns_empty_suggestions_when_no_path_and_no_item(self):
        with patch("resolve_api.get_selected_media_pool_item", return_value=None), \
             patch("app._get_resolve", return_value=MagicMock()):
            resp = self.client.post("/api/clip/ai-suggestion", json={})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["suggestions"], [])


class TestKeywordsCatalogRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_returns_catalog_json(self):
        import app as flask_app_mod
        # Set the catalog directly
        with flask_app_mod._catalog_lock:
            flask_app_mod._keyword_catalog = ["alpha", "beta", "gamma"]
            flask_app_mod._catalog_loaded = True
            flask_app_mod._catalog_refresh_pending = False
        resp = self.client.get("/api/keywords/catalog")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("keywords", data)
        self.assertIn("alpha", data["keywords"])


class TestNavigateClipRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def _make_item(self, name="clip.mov"):
        item = MagicMock()
        item.GetName.return_value = name
        item.GetMediaId.return_value = "media-nav-001"
        item.GetMetadata.side_effect = lambda k=None: (
            {"Keywords": "alpha"} if k is None else ("alpha" if k == "Keywords" else None)
        )
        item.GetClipProperty.side_effect = lambda k: (
            "alpha" if k == "Keywords" else ("/proxy/clip.mxf" if k == "Proxy Media Path" else "")
        )
        return item

    def test_next_returns_200(self):
        item = self._make_item("next_clip.mov")
        with patch("resolve_api.navigate_clip", return_value=(item, {})), \
             patch("resolve_api.get_keywords", return_value=["alpha"]), \
             patch("resolve_api.suggest_keywords_from_cache", return_value=[]), \
             patch("app._get_resolve", return_value=MagicMock()):
            resp = self.client.post("/api/clip/navigate", json={"direction": "next"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("clip", data)
        self.assertIn("keywords", data)

    def test_prev_returns_200(self):
        item = self._make_item("prev_clip.mov")
        with patch("resolve_api.navigate_clip", return_value=(item, {})), \
             patch("resolve_api.get_keywords", return_value=["alpha"]), \
             patch("resolve_api.suggest_keywords_from_cache", return_value=[]), \
             patch("app._get_resolve", return_value=MagicMock()):
            resp = self.client.post("/api/clip/navigate", json={"direction": "prev"})
        self.assertEqual(resp.status_code, 200)

    def test_bad_direction_returns_400(self):
        resp = self.client.post("/api/clip/navigate", json={"direction": "sideways"})
        self.assertEqual(resp.status_code, 400)

    def test_at_boundary_returns_404(self):
        with patch("resolve_api.navigate_clip", return_value=(None, {})), \
             patch("app._get_resolve", return_value=MagicMock()):
            resp = self.client.post("/api/clip/navigate", json={"direction": "next"})
        self.assertEqual(resp.status_code, 404)


class TestProfilerRoutes(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_profiler_report_returns_200_json(self):
        resp = self.client.get("/api/profiler/report")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("navigate", data)

    def test_profiler_dump_returns_200_with_path(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            import profiler
            with patch("profiler.dump", return_value=f"{tmpdir}/report.json"):
                resp = self.client.post("/api/profiler/dump")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("path", resp.get_json())

    def test_profiler_filmstrip_cache_hit_returns_204(self):
        resp = self.client.post("/api/profiler/filmstrip-cache-hit")
        self.assertEqual(resp.status_code, 204)


class TestClipNeighboursRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_with_media_id_returns_paths(self):
        with patch("resolve_api.get_neighbours", return_value=("/p/prev.mxf", "/p/next.mxf")):
            resp = self.client.get("/api/clip/neighbours?media_id=media-123")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["prev_path"], "/p/prev.mxf")
        self.assertEqual(data["next_path"], "/p/next.mxf")

    def test_without_media_id_returns_empty_paths(self):
        resp = self.client.get("/api/clip/neighbours")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["prev_path"], "")
        self.assertEqual(data["next_path"], "")


class TestSetKeywordsRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()
        # Ensure the resolve lock is not held from previous tests' background threads
        acquired = flask_app._resolve_lock.acquire(timeout=6.0)
        if acquired:
            flask_app._resolve_lock.release()

    def _make_item(self, name="clip.mov"):
        item = MagicMock()
        item.GetName.return_value = name
        return item

    def test_returns_200_on_success(self):
        item = self._make_item()
        with patch("resolve_api.get_selected_media_pool_item", return_value=item), \
             patch("resolve_api.set_keywords", return_value=True), \
             patch("resolve_api.invalidate_folder_cache"), \
             patch("threading.Thread"), \
             patch("app._get_resolve", return_value=MagicMock()):
            resp = self.client.post(
                "/api/clip/keywords",
                json={"keywords": ["alpha", "beta"]},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("keywords", data)

    def test_returns_400_when_keywords_not_list(self):
        resp = self.client.post("/api/clip/keywords", json={"keywords": "not-a-list"})
        self.assertEqual(resp.status_code, 400)

    def test_returns_404_when_no_clip(self):
        mock_resolve = MagicMock()
        with patch("app._get_resolve", return_value=mock_resolve), \
             patch("resolve_api.get_selected_media_pool_item", return_value=None):
            resp = self.client.post("/api/clip/keywords", json={"keywords": ["alpha"]})
        self.assertEqual(resp.status_code, 404)

    def test_returns_500_when_resolve_rejects_write(self):
        item = self._make_item()
        with patch("resolve_api.get_selected_media_pool_item", return_value=item), \
             patch("resolve_api.set_keywords", return_value=False), \
             patch("threading.Thread"), \
             patch("app._get_resolve", return_value=MagicMock()):
            resp = self.client.post("/api/clip/keywords", json={"keywords": ["alpha"]})
        self.assertEqual(resp.status_code, 500)


class TestPinnedKeywordsRoute(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_returns_200_with_list(self):
        resp = self.client.get("/api/config/pinned-keywords")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("pinned_keywords", data)
        self.assertIsInstance(data["pinned_keywords"], list)


if __name__ == "__main__":
    unittest.main()
