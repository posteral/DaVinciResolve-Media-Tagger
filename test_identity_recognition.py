from __future__ import annotations

import io
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

import identity_recognition


def _make_png(color=(128, 64, 32)) -> bytes:
    """Return minimal valid PNG bytes (1x1 pixel)."""
    from PIL import Image
    img = Image.new("RGB", (10, 10), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_fr_mock(locations=None, encodings=None, distances=None):
    """Build a face_recognition mock."""
    fr = MagicMock()
    fr.face_locations.return_value = locations if locations is not None else [(0, 10, 10, 0)]
    enc = encodings if encodings is not None else [np.array([0.1] * 128)]
    fr.face_encodings.return_value = enc
    if distances is not None:
        fr.face_distance.return_value = np.array(distances)
    return fr


class TestDetectFacesInFrames(unittest.TestCase):
    def test_returns_embedding_crop_and_frame_idx(self):
        fr = _make_fr_mock()
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            results = identity_recognition.detect_faces_in_frames([_make_png()])
        self.assertEqual(len(results), 1)
        embedding, crop, frame_idx = results[0]
        self.assertEqual(len(embedding), 128)
        self.assertIsInstance(crop, bytes)
        self.assertEqual(frame_idx, 0)

    def test_returns_empty_when_face_recognition_missing(self):
        with patch.object(identity_recognition, "_import_face_recognition", return_value=None):
            results = identity_recognition.detect_faces_in_frames([_make_png()])
        self.assertEqual(results, [])

    def test_returns_empty_when_no_faces_detected(self):
        fr = _make_fr_mock(locations=[], encodings=[])
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            results = identity_recognition.detect_faces_in_frames([_make_png()])
        self.assertEqual(results, [])

    def test_handles_multiple_frames(self):
        fr = _make_fr_mock()
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            results = identity_recognition.detect_faces_in_frames([_make_png(), _make_png(), _make_png()])
        self.assertEqual(len(results), 3)
        frame_indices = [r[2] for r in results]
        self.assertEqual(frame_indices, [0, 1, 2])

    def test_skips_failed_frame_without_raising(self):
        fr = _make_fr_mock()
        fr.face_locations.side_effect = [Exception("boom"), [(0, 10, 10, 0)]]
        fr.face_encodings.return_value = [np.array([0.1] * 128)]
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            results = identity_recognition.detect_faces_in_frames([_make_png(), _make_png()])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][2], 1)


class TestClusterFaces(unittest.TestCase):
    def _detected(self, embedding, frame_idx=0):
        return (embedding, b"crop", frame_idx)

    def test_same_person_multiple_frames_gives_one_cluster(self):
        emb = np.array([0.1] * 128)
        fr = MagicMock()
        fr.face_distance.return_value = np.array([0.1])  # well below CLUSTER_DISTANCE
        detected = [self._detected(emb.tolist(), 0), self._detected(emb.tolist(), 1)]
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            clusters = identity_recognition.cluster_faces(detected)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["occurrence_count"], 2)

    def test_different_people_give_separate_clusters(self):
        emb_a = np.array([0.1] * 128)
        emb_b = np.array([0.9] * 128)
        fr = MagicMock()
        fr.face_distance.return_value = np.array([0.8])  # above CLUSTER_DISTANCE
        detected = [self._detected(emb_a.tolist(), 0), self._detected(emb_b.tolist(), 1)]
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            clusters = identity_recognition.cluster_faces(detected)
        self.assertEqual(len(clusters), 2)

    def test_returns_empty_when_face_recognition_missing(self):
        with patch.object(identity_recognition, "_import_face_recognition", return_value=None):
            clusters = identity_recognition.cluster_faces([(np.array([0.1] * 128).tolist(), b"", 0)])
        self.assertEqual(clusters, [])

    def test_mean_embedding_shape(self):
        emb = np.array([0.5] * 128)
        fr = MagicMock()
        fr.face_distance.return_value = np.array([0.1])
        detected = [(emb.tolist(), b"crop", 0), (emb.tolist(), b"crop", 1)]
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            clusters = identity_recognition.cluster_faces(detected)
        self.assertEqual(len(clusters[0]["mean_embedding"]), 128)


class TestMatchCluster(unittest.TestCase):
    def _registry_with(self, identity_id, embeddings):
        return {"identities": [
            {"identity_id": identity_id, "display_name": "Alice",
             "keyword_string": "Alice", "embeddings": embeddings}
        ]}

    def test_known_match(self):
        fr = MagicMock()
        fr.face_distance.return_value = np.array([0.4])  # ≤ KNOWN_THRESHOLD
        reg = self._registry_with("abc", [[0.1] * 128])
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            iid, status, dist = identity_recognition.match_cluster([0.1] * 128, reg)
        self.assertEqual(iid, "abc")
        self.assertEqual(status, "known")
        self.assertAlmostEqual(dist, 0.4)

    def test_low_confidence_match(self):
        fr = MagicMock()
        fr.face_distance.return_value = np.array([0.62])  # KNOWN < 0.62 ≤ LOW_CONF
        reg = self._registry_with("abc", [[0.1] * 128])
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            iid, status, dist = identity_recognition.match_cluster([0.1] * 128, reg)
        self.assertEqual(iid, "abc")
        self.assertEqual(status, "low_confidence")

    def test_unknown(self):
        fr = MagicMock()
        fr.face_distance.return_value = np.array([0.85])  # > LOW_CONF_THRESHOLD
        reg = self._registry_with("abc", [[0.1] * 128])
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            iid, status, dist = identity_recognition.match_cluster([0.1] * 128, reg)
        self.assertIsNone(iid)
        self.assertEqual(status, "unknown")
        self.assertIsNone(dist)

    def test_unknown_when_registry_empty(self):
        fr = MagicMock()
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            iid, status, dist = identity_recognition.match_cluster([0.1] * 128, {"identities": []})
        self.assertIsNone(iid)
        self.assertEqual(status, "unknown")

    def test_returns_unknown_when_face_recognition_missing(self):
        with patch.object(identity_recognition, "_import_face_recognition", return_value=None):
            iid, status, dist = identity_recognition.match_cluster([0.1] * 128, {"identities": []})
        self.assertEqual(status, "unknown")


class TestRunDetectionPipeline(unittest.TestCase):
    def test_end_to_end_known_identity(self):
        fr = MagicMock()
        fr.face_locations.return_value = [(0, 10, 10, 0)]
        fr.face_encodings.return_value = [np.array([0.1] * 128)]
        fr.face_distance.return_value = np.array([0.3])  # known

        reg = {"identities": [
            {"identity_id": "abc", "display_name": "Alice",
             "keyword_string": "Alice", "embeddings": [[0.1] * 128]}
        ]}
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            results = identity_recognition.run_detection_pipeline([_make_png()], reg)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "known")
        self.assertEqual(results[0]["identity_id"], "abc")
        self.assertEqual(results[0]["display_name"], "Alice")
        self.assertIn("best_crop", results[0])
        self.assertIn("mean_embedding", results[0])

    def test_end_to_end_unknown_identity(self):
        fr = MagicMock()
        fr.face_locations.return_value = [(0, 10, 10, 0)]
        fr.face_encodings.return_value = [np.array([0.1] * 128)]
        fr.face_distance.return_value = np.array([0.9])  # unknown

        reg = {"identities": [
            {"identity_id": "abc", "display_name": "Alice",
             "keyword_string": "Alice", "embeddings": [[0.1] * 128]}
        ]}
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            results = identity_recognition.run_detection_pipeline([_make_png()], reg)
        self.assertEqual(results[0]["status"], "unknown")
        self.assertIsNone(results[0]["identity_id"])

    def test_returns_empty_when_no_faces(self):
        fr = MagicMock()
        fr.face_locations.return_value = []
        fr.face_encodings.return_value = []
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            results = identity_recognition.run_detection_pipeline([_make_png()], {"identities": []})
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
