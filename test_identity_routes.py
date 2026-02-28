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


if __name__ == "__main__":
    unittest.main()
