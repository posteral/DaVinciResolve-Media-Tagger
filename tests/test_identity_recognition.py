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
        embedding, crop, frame_idx, location = results[0]
        self.assertEqual(len(embedding), 128)
        self.assertIsInstance(crop, bytes)
        self.assertEqual(frame_idx, 0)
        self.assertEqual(len(location), 4)  # (top, right, bottom, left)

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
    def _detected(self, embedding, frame_idx=0, location=(0, 10, 10, 0)):
        return (embedding, b"crop", frame_idx, location)

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
        fr.face_distance.return_value = np.array([0.8])  # above both thresholds
        # Non-overlapping boxes so spatial path doesn't merge them either
        detected = [
            self._detected(emb_a.tolist(), 0, location=(0, 10, 10, 0)),
            self._detected(emb_b.tolist(), 1, location=(50, 60, 60, 50)),
        ]
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            clusters = identity_recognition.cluster_faces(detected)
        self.assertEqual(len(clusters), 2)

    def test_returns_empty_when_face_recognition_missing(self):
        with patch.object(identity_recognition, "_import_face_recognition", return_value=None):
            clusters = identity_recognition.cluster_faces([(np.array([0.1] * 128).tolist(), b"", 0, (0, 10, 10, 0))])
        self.assertEqual(clusters, [])

    def test_mean_embedding_shape(self):
        emb = np.array([0.5] * 128)
        fr = MagicMock()
        fr.face_distance.return_value = np.array([0.1])
        detected = [(emb.tolist(), b"crop", 0, (0, 10, 10, 0)), (emb.tolist(), b"crop", 1, (0, 10, 10, 0))]
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
        reg = self._registry_with("abc", [[0.1] * 128] * 5)  # MIN_EMBEDDINGS_FOR_KNOWN = 5
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            iid, status, dist, candidates = identity_recognition.match_cluster([0.1] * 128, reg)
        self.assertEqual(iid, "abc")
        self.assertEqual(status, "known")
        self.assertAlmostEqual(dist, 0.4)

    def test_low_confidence_match(self):
        fr = MagicMock()
        fr.face_distance.return_value = np.array([0.55])  # KNOWN_THRESHOLD < 0.55 ≤ LOW_CONF_THRESHOLD
        reg = self._registry_with("abc", [[0.1] * 128])
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            iid, status, dist, candidates = identity_recognition.match_cluster([0.1] * 128, reg)
        self.assertEqual(iid, "abc")
        self.assertEqual(status, "low_confidence")

    def test_unknown(self):
        fr = MagicMock()
        fr.face_distance.return_value = np.array([0.85])  # > LOW_CONF_THRESHOLD
        reg = self._registry_with("abc", [[0.1] * 128])
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            iid, status, dist, candidates = identity_recognition.match_cluster([0.1] * 128, reg)
        self.assertIsNone(iid)
        self.assertEqual(status, "unknown")
        self.assertIsNone(dist)

    def test_unknown_when_registry_empty(self):
        fr = MagicMock()
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            iid, status, dist, candidates = identity_recognition.match_cluster([0.1] * 128, {"identities": []})
        self.assertIsNone(iid)
        self.assertEqual(status, "unknown")

    def test_returns_unknown_when_face_recognition_missing(self):
        with patch.object(identity_recognition, "_import_face_recognition", return_value=None):
            iid, status, dist, candidates = identity_recognition.match_cluster([0.1] * 128, {"identities": []})
        self.assertEqual(status, "unknown")

    def test_candidates_returned_sorted_by_distance(self):
        fr = MagicMock()
        registry = {"identities": [
            {"identity_id": "a1", "display_name": "Alice", "keyword_string": "Alice",
             "embeddings": [[0.1] * 128] * 5},
            {"identity_id": "b2", "display_name": "Bob", "keyword_string": "Bob",
             "embeddings": [[0.2] * 128] * 5},
        ]}
        # Alice closer (0.4), Bob further (0.5)
        fr.face_distance.side_effect = [np.array([0.4]), np.array([0.5])]
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            iid, status, dist, candidates = identity_recognition.match_cluster([0.1] * 128, registry)
        self.assertIsInstance(candidates, list)
        self.assertGreaterEqual(len(candidates), 1)
        names = [c["display_name"] for c in candidates]
        self.assertEqual(names[0], "Alice")  # closest first
        if len(names) > 1:
            self.assertEqual(names[1], "Bob")
        # Each candidate has required fields
        for c in candidates:
            self.assertIn("identity_id", c)
            self.assertIn("display_name", c)
            self.assertIn("distance", c)

    def test_candidates_empty_when_no_face_recognition(self):
        with patch.object(identity_recognition, "_import_face_recognition", return_value=None):
            _, _, _, candidates = identity_recognition.match_cluster([0.1] * 128, {"identities": []})
        self.assertEqual(candidates, [])

    def test_candidates_empty_when_registry_empty(self):
        fr = MagicMock()
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            _, _, _, candidates = identity_recognition.match_cluster([0.1] * 128, {"identities": []})
        self.assertEqual(candidates, [])

    def test_run_detection_pipeline_includes_candidates(self):
        """run_detection_pipeline result dicts include a 'candidates' list."""
        fr = MagicMock()
        fr.face_locations.return_value = [(0, 10, 10, 0)]
        fr.face_encodings.return_value = [np.array([0.1] * 128)]
        fr.face_distance.return_value = np.array([0.85])  # unknown
        registry = {"identities": [
            {"identity_id": "a1", "display_name": "Alice", "keyword_string": "Alice",
             "embeddings": [[0.1] * 128] * 5},
        ]}
        import io as _io
        from PIL import Image
        img = Image.new("RGB", (100, 100))
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            results = identity_recognition.run_detection_pipeline([png_bytes], registry)
        self.assertEqual(len(results), 1)
        self.assertIn("candidates", results[0])
        self.assertIsInstance(results[0]["candidates"], list)


class TestRunDetectionPipeline(unittest.TestCase):
    def test_end_to_end_known_identity(self):
        fr = MagicMock()
        fr.face_locations.return_value = [(0, 10, 10, 0)]
        fr.face_encodings.return_value = [np.array([0.1] * 128)]
        fr.face_distance.return_value = np.array([0.3])  # known

        reg = {"identities": [
            {"identity_id": "abc", "display_name": "Alice",
             "keyword_string": "Alice", "embeddings": [[0.1] * 128] * 5}  # MIN_EMBEDDINGS_FOR_KNOWN = 5
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


class TestBoxesOverlap(unittest.TestCase):
    """Tests for identity_recognition._boxes_overlap."""

    def test_overlapping_boxes_returns_true(self):
        # Box a = (10, 40, 40, 10), box b = (20, 50, 50, 20) — overlap
        a = (10, 40, 40, 10)
        b = (20, 50, 50, 20)
        self.assertTrue(identity_recognition._boxes_overlap(a, b))

    def test_non_overlapping_b_is_left_of_a(self):
        # a is at (10, 40, 40, 20), b ends at right=15 (left of a)
        a = (10, 40, 40, 20)
        b = (10, 15, 40, 0)  # b_right=15 < a_left=20
        self.assertFalse(identity_recognition._boxes_overlap(a, b))

    def test_non_overlapping_b_is_above_a(self):
        # a starts at top=50, b ends at bottom=20 — b is entirely above a
        a = (50, 100, 100, 50)
        b = (0, 100, 20, 50)  # b_bottom=20 < a_top=50
        self.assertFalse(identity_recognition._boxes_overlap(a, b))

    def test_touching_edges_not_overlapping(self):
        # Boxes share an edge exactly (a_right == b_left) — touching but NOT overlapping
        # a_right=40, b_left=40 → a_right > b_left is False
        a = (0, 40, 40, 0)
        b = (0, 80, 40, 40)
        self.assertFalse(identity_recognition._boxes_overlap(a, b))

    def test_contained_box_overlaps(self):
        # b completely inside a
        a = (0, 100, 100, 0)
        b = (20, 80, 80, 20)
        self.assertTrue(identity_recognition._boxes_overlap(a, b))


class TestCropFace(unittest.TestCase):
    """Tests for identity_recognition._crop_face boundary clamping."""

    def _make_rgb(self, h=100, w=100):
        return np.zeros((h, w, 3), dtype=np.uint8)

    def test_crop_clamped_at_top_left_boundary(self):
        # Location with tiny top/left so padding would go negative
        rgb = self._make_rgb(100, 100)
        location = (2, 20, 20, 2)  # top=2, right=20, bottom=20, left=2 — pad would go below 0
        result = identity_recognition._crop_face(rgb, location, pad_fraction=0.5)
        self.assertIsInstance(result, bytes)
        self.assertGreater(len(result), 0)

    def test_crop_clamped_at_bottom_right_boundary(self):
        # Location at the very bottom-right corner
        rgb = self._make_rgb(100, 100)
        location = (80, 99, 99, 80)  # padding would exceed 100×100
        result = identity_recognition._crop_face(rgb, location, pad_fraction=0.5)
        self.assertIsInstance(result, bytes)
        self.assertGreater(len(result), 0)

    def test_normal_crop_returns_jpeg_bytes(self):
        rgb = self._make_rgb(200, 200)
        location = (50, 150, 150, 50)
        result = identity_recognition._crop_face(rgb, location, pad_fraction=0.1)
        self.assertIsInstance(result, bytes)
        # JPEG magic bytes
        self.assertEqual(result[:2], b"\xff\xd8")


class TestClusterFacesSpatialMerge(unittest.TestCase):
    """Tests for the spatial-overlap merge path in cluster_faces."""

    def _detected(self, embedding, frame_idx=0, location=(0, 10, 10, 0)):
        return (embedding, b"crop", frame_idx, location)

    def test_spatial_overlap_with_loose_distance_merges(self):
        """Two detections in overlapping boxes with dist < CLUSTER_DISTANCE_SPATIAL
        but dist >= CLUSTER_DISTANCE should merge into one cluster."""
        emb_a = np.array([0.0] * 128)
        emb_b = np.array([0.1] * 128)

        fr = MagicMock()
        # Return dist between CLUSTER_DISTANCE (0.50) and CLUSTER_DISTANCE_SPATIAL (0.70)
        fr.face_distance.return_value = np.array([0.60])

        # Same overlapping location box
        location = (10, 50, 50, 10)
        detected = [
            self._detected(emb_a.tolist(), 0, location),
            self._detected(emb_b.tolist(), 1, location),
        ]
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            clusters = identity_recognition.cluster_faces(detected)

        # Should merge into one cluster because boxes overlap AND dist < CLUSTER_DISTANCE_SPATIAL
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["occurrence_count"], 2)

    def test_non_overlapping_boxes_no_spatial_merge(self):
        """Two detections with dist between thresholds but NON-overlapping boxes
        should produce two separate clusters."""
        emb_a = np.array([0.0] * 128)
        emb_b = np.array([0.1] * 128)

        fr = MagicMock()
        fr.face_distance.return_value = np.array([0.60])

        detected = [
            self._detected(emb_a.tolist(), 0, location=(0, 10, 10, 0)),
            self._detected(emb_b.tolist(), 1, location=(80, 90, 90, 80)),  # far apart, no overlap
        ]
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            clusters = identity_recognition.cluster_faces(detected)

        self.assertEqual(len(clusters), 2)


class TestMatchClusterBoundaries(unittest.TestCase):
    """Tests for match_cluster threshold boundary conditions."""

    def _registry_with(self, identity_id, n_embeddings):
        return {"identities": [
            {
                "identity_id": identity_id,
                "display_name": "Alice",
                "keyword_string": "Alice",
                "embeddings": [[0.1] * 128] * n_embeddings,
            }
        ]}

    def test_dist_exactly_known_threshold_with_enough_embeddings_is_known(self):
        fr = MagicMock()
        fr.face_distance.return_value = np.array([identity_recognition.KNOWN_THRESHOLD])
        reg = self._registry_with("abc", identity_recognition.MIN_EMBEDDINGS_FOR_KNOWN)
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            iid, status, dist, candidates = identity_recognition.match_cluster([0.1] * 128, reg)
        self.assertEqual(status, "known")
        self.assertEqual(iid, "abc")

    def test_dist_exactly_low_conf_threshold_is_low_confidence(self):
        fr = MagicMock()
        fr.face_distance.return_value = np.array([identity_recognition.LOW_CONF_THRESHOLD])
        reg = self._registry_with("abc", 1)
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            iid, status, dist, candidates = identity_recognition.match_cluster([0.1] * 128, reg)
        self.assertEqual(status, "low_confidence")

    def test_dist_within_known_threshold_but_too_few_embeddings_is_low_confidence(self):
        fr = MagicMock()
        fr.face_distance.return_value = np.array([identity_recognition.KNOWN_THRESHOLD - 0.01])
        # Fewer than MIN_EMBEDDINGS_FOR_KNOWN
        reg = self._registry_with("abc", identity_recognition.MIN_EMBEDDINGS_FOR_KNOWN - 1)
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            iid, status, dist, candidates = identity_recognition.match_cluster([0.1] * 128, reg)
        self.assertEqual(status, "low_confidence")
        self.assertEqual(iid, "abc")


class TestRunDetectionPipelineExtra(unittest.TestCase):
    """Additional tests for run_detection_pipeline edge cases."""

    def test_distance_is_none_when_unknown(self):
        fr = MagicMock()
        fr.face_locations.return_value = [(0, 10, 10, 0)]
        fr.face_encodings.return_value = [np.array([0.1] * 128)]
        fr.face_distance.return_value = np.array([0.99])  # > LOW_CONF_THRESHOLD → unknown

        reg = {"identities": [
            {"identity_id": "abc", "display_name": "Alice",
             "keyword_string": "Alice", "embeddings": [[0.1] * 128]}
        ]}
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            results = identity_recognition.run_detection_pipeline([_make_png()], reg)
        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0]["distance"])
        self.assertIsNone(results[0]["identity_id"])

    def test_low_confidence_case_sets_status_correctly(self):
        fr = MagicMock()
        fr.face_locations.return_value = [(0, 10, 10, 0)]
        fr.face_encodings.return_value = [np.array([0.1] * 128)]
        fr.face_distance.return_value = np.array([0.55])  # low_confidence range

        reg = {"identities": [
            {"identity_id": "abc", "display_name": "Bob",
             "keyword_string": "Bob", "embeddings": [[0.1] * 128]}
        ]}
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            results = identity_recognition.run_detection_pipeline([_make_png()], reg)
        self.assertEqual(results[0]["status"], "low_confidence")
        self.assertEqual(results[0]["identity_id"], "abc")

    def test_occurrence_count_passed_through(self):
        """occurrence_count in the cluster dict must appear in the result."""
        fr = MagicMock()
        fr.face_locations.return_value = [(0, 10, 10, 0)]
        fr.face_encodings.return_value = [np.array([0.1] * 128)]
        fr.face_distance.return_value = np.array([0.9])  # unknown

        reg = {"identities": []}
        with patch.object(identity_recognition, "_import_face_recognition", return_value=fr):
            results = identity_recognition.run_detection_pipeline([_make_png()], reg)
        self.assertEqual(results[0]["occurrence_count"], 1)


if __name__ == "__main__":
    unittest.main()
