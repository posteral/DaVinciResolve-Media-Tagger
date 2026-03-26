from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import identity_registry


class TestLoadRegistry(unittest.TestCase):
    def test_returns_empty_when_file_missing(self):
        with patch.object(identity_registry, "_registry_path", return_value=Path("/nonexistent/path.json")):
            reg = identity_registry.load_registry()
        self.assertEqual(reg, {"version": 1, "identities": []})

    def test_loads_valid_file(self):
        data = {"version": 1, "identities": [{"identity_id": "abc", "display_name": "Alice"}]}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        try:
            with patch.object(identity_registry, "_registry_path", return_value=path):
                reg = identity_registry.load_registry()
            self.assertEqual(reg["identities"][0]["display_name"], "Alice")
        finally:
            path.unlink(missing_ok=True)

    def test_returns_empty_on_corrupt_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{not valid json")
            path = Path(f.name)
        try:
            with patch.object(identity_registry, "_registry_path", return_value=path):
                reg = identity_registry.load_registry()
            self.assertEqual(reg, {"version": 1, "identities": []})
        finally:
            path.unlink(missing_ok=True)

    def test_returns_empty_on_unexpected_shape(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(["not", "a", "dict"], f)
            path = Path(f.name)
        try:
            with patch.object(identity_registry, "_registry_path", return_value=path):
                reg = identity_registry.load_registry()
            self.assertEqual(reg, {"version": 1, "identities": []})
        finally:
            path.unlink(missing_ok=True)


class TestSaveRegistry(unittest.TestCase):
    def test_writes_and_reads_back(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "registry.json"
            with patch.object(identity_registry, "_registry_path", return_value=path):
                reg = {"version": 1, "identities": [{"identity_id": "x"}]}
                identity_registry.save_registry(reg)
                loaded = identity_registry.load_registry()
            self.assertEqual(loaded["identities"][0]["identity_id"], "x")

    def test_creates_bak_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "registry.json"
            bak = path.with_suffix(".json.bak")
            with patch.object(identity_registry, "_registry_path", return_value=path):
                identity_registry.save_registry({"version": 1, "identities": []})
                identity_registry.save_registry({"version": 1, "identities": [{"identity_id": "y"}]})
            self.assertTrue(bak.exists())


class TestAddIdentity(unittest.TestCase):
    def test_adds_new_identity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(identity_registry, "_faces_dir", return_value=Path(tmpdir) / "faces"):
                reg = {"version": 1, "identities": []}
                reg, iid = identity_registry.add_identity(reg, "Alice", "Alice", [0.1] * 128, None)
            self.assertEqual(len(reg["identities"]), 1)
            self.assertEqual(reg["identities"][0]["display_name"], "Alice")
            self.assertIsInstance(iid, str)
            self.assertEqual(len(reg["identities"][0]["embeddings"]), 1)

    def test_saves_face_crop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            faces_dir = Path(tmpdir) / "faces"
            with patch.object(identity_registry, "_faces_dir", return_value=faces_dir):
                reg = {"version": 1, "identities": []}
                reg, iid = identity_registry.add_identity(reg, "Bob", "Bob", [0.0] * 128, b"fakejpeg")
            self.assertTrue(reg["identities"][0]["thumbnail_path"].startswith("faces/"))
            self.assertTrue((faces_dir / f"{iid}_0.jpg").exists())


class TestUpdateIdentityEmbedding(unittest.TestCase):
    def test_appends_embedding(self):
        reg = {"version": 1, "identities": [
            {"identity_id": "abc", "embeddings": [[0.1] * 128], "thumbnail_path": ""}
        ]}
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(identity_registry, "_faces_dir", return_value=Path(tmpdir) / "faces"):
                reg = identity_registry.update_identity_embedding(reg, "abc", [0.2] * 128, None)
        self.assertEqual(len(reg["identities"][0]["embeddings"]), 2)

    @unittest.skipUnless(__import__("importlib").util.find_spec("numpy"), "numpy not installed")
    def test_diversity_cap(self):
        # Build MAX_EMBEDDINGS embeddings, then add one more to trigger diversity selection
        max_cap = identity_registry.MAX_EMBEDDINGS
        embeddings = [[float(i)] * 128 for i in range(max_cap)]
        reg = {"version": 1, "identities": [
            {"identity_id": "abc", "embeddings": embeddings, "thumbnail_path": ""}
        ]}
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(identity_registry, "_faces_dir", return_value=Path(tmpdir) / "faces"):
                reg = identity_registry.update_identity_embedding(reg, "abc", [99.0] * 128, None)
        # Cap is enforced after diversity selection
        self.assertEqual(len(reg["identities"][0]["embeddings"]), max_cap)


class TestListIdentities(unittest.TestCase):
    def test_returns_lightweight_list(self):
        reg = {"version": 1, "identities": [
            {"identity_id": "a", "display_name": "Alice", "keyword_string": "Alice", "embeddings": [[0.1] * 128]},
            {"identity_id": "b", "display_name": "Bob", "keyword_string": "Bob", "embeddings": [[0.2] * 128]},
        ]}
        result = identity_registry.list_identities(reg)
        self.assertEqual(len(result), 2)
        self.assertNotIn("embeddings", result[0])
        self.assertEqual(result[0]["display_name"], "Alice")


class TestFindIdentityByName(unittest.TestCase):
    def test_finds_by_name(self):
        reg = {"version": 1, "identities": [
            {"identity_id": "a", "display_name": "Alice", "keyword_string": "Alice", "embeddings": []}
        ]}
        result = identity_registry.find_identity_by_name(reg, "alice")
        self.assertIsNotNone(result)
        self.assertEqual(result["identity_id"], "a")

    def test_returns_none_when_not_found(self):
        reg = {"version": 1, "identities": []}
        self.assertIsNone(identity_registry.find_identity_by_name(reg, "Alice"))


class TestSaveFaceCrop(unittest.TestCase):
    def test_sequential_file_numbering(self):
        """Second crop for same identity_id gets _1.jpg."""
        with tempfile.TemporaryDirectory() as tmpdir:
            faces_dir = Path(tmpdir) / "faces"
            with patch.object(identity_registry, "_faces_dir", return_value=faces_dir):
                path0 = identity_registry.save_face_crop("id-xyz", b"jpeg0")
                path1 = identity_registry.save_face_crop("id-xyz", b"jpeg1")
            self.assertEqual(path0, "faces/id-xyz_0.jpg")
            self.assertEqual(path1, "faces/id-xyz_1.jpg")
            self.assertTrue((faces_dir / "id-xyz_0.jpg").exists())
            self.assertTrue((faces_dir / "id-xyz_1.jpg").exists())

    def test_different_identities_start_at_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            faces_dir = Path(tmpdir) / "faces"
            with patch.object(identity_registry, "_faces_dir", return_value=faces_dir):
                path_a = identity_registry.save_face_crop("id-A", b"jpeg")
                path_b = identity_registry.save_face_crop("id-B", b"jpeg")
            self.assertEqual(path_a, "faces/id-A_0.jpg")
            self.assertEqual(path_b, "faces/id-B_0.jpg")


class TestFindIdentityByNameExtra(unittest.TestCase):
    def test_case_insensitive_match(self):
        reg = {"version": 1, "identities": [
            {"identity_id": "a", "display_name": "Alice", "keyword_string": "Alice", "embeddings": []},
        ]}
        self.assertIsNotNone(identity_registry.find_identity_by_name(reg, "ALICE"))
        self.assertIsNotNone(identity_registry.find_identity_by_name(reg, "alice"))
        self.assertIsNotNone(identity_registry.find_identity_by_name(reg, "Alice"))

    def test_whitespace_trimmed(self):
        reg = {"version": 1, "identities": [
            {"identity_id": "b", "display_name": "Bob", "keyword_string": "Bob", "embeddings": []},
        ]}
        result = identity_registry.find_identity_by_name(reg, "  Bob  ")
        self.assertIsNotNone(result)
        self.assertEqual(result["identity_id"], "b")

    def test_multiple_identities_returns_first_match(self):
        reg = {"version": 1, "identities": [
            {"identity_id": "first", "display_name": "Alice", "keyword_string": "Alice", "embeddings": []},
            {"identity_id": "second", "display_name": "Alice", "keyword_string": "Alice", "embeddings": []},
        ]}
        result = identity_registry.find_identity_by_name(reg, "Alice")
        self.assertIsNotNone(result)
        self.assertEqual(result["identity_id"], "first")

    def test_returns_none_for_partial_match(self):
        reg = {"version": 1, "identities": [
            {"identity_id": "a", "display_name": "Alice Smith", "keyword_string": "Alice", "embeddings": []},
        ]}
        self.assertIsNone(identity_registry.find_identity_by_name(reg, "Alice"))


class TestSelectDiverseEmbeddings(unittest.TestCase):
    def test_keep_less_than_total_returns_correct_count(self):
        # 3 identical embeddings, keep=2 → should return exactly 2
        embeddings = [[0.5] * 128, [0.5] * 128, [0.5] * 128]
        result = identity_registry._select_diverse_embeddings(embeddings, 2)
        self.assertEqual(len(result), 2)

    def test_diverse_set_keeps_most_spread(self):
        # Two distinct embeddings — both should be kept when keep=2
        emb_a = [0.0] * 128
        emb_b = [1.0] * 128
        emb_c = [0.5] * 128  # middle, less unique
        result = identity_registry._select_diverse_embeddings([emb_a, emb_b, emb_c], 2)
        self.assertEqual(len(result), 2)
        # emb_a and emb_b are the most spread; emb_c is the least diverse
        result_tuples = [tuple(e) for e in result]
        self.assertIn(tuple(emb_a), result_tuples)
        self.assertIn(tuple(emb_b), result_tuples)


class TestUpdateIdentityEmbeddingExtra(unittest.TestCase):
    def test_crop_bytes_none_does_not_update_thumbnail_path(self):
        reg = {"version": 1, "identities": [
            {"identity_id": "abc", "embeddings": [[0.1] * 128], "thumbnail_path": "faces/abc_0.jpg"}
        ]}
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(identity_registry, "_faces_dir", return_value=Path(tmpdir) / "faces"):
                updated = identity_registry.update_identity_embedding(
                    reg, "abc", [0.2] * 128, crop_bytes=None
                )
        self.assertEqual(updated["identities"][0]["thumbnail_path"], "faces/abc_0.jpg")

    def test_unknown_identity_id_is_noop(self):
        reg = {"version": 1, "identities": [
            {"identity_id": "known-id", "embeddings": [[0.1] * 128], "thumbnail_path": ""}
        ]}
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(identity_registry, "_faces_dir", return_value=Path(tmpdir) / "faces"):
                updated = identity_registry.update_identity_embedding(
                    reg, "nonexistent-id", [0.99] * 128, crop_bytes=None
                )
        # Registry should be unchanged
        self.assertEqual(len(updated["identities"]), 1)
        self.assertEqual(len(updated["identities"][0]["embeddings"]), 1)


class TestListIdentitiesExtra(unittest.TestCase):
    def test_empty_registry_returns_empty_list(self):
        reg = {"version": 1, "identities": []}
        result = identity_registry.list_identities(reg)
        self.assertEqual(result, [])

    def test_no_identities_key_returns_empty_list(self):
        # Registry without identities key at all
        reg = {"version": 1}
        result = identity_registry.list_identities(reg)
        self.assertEqual(result, [])


class TestAddIdentityExtra(unittest.TestCase):
    def test_crop_bytes_none_leaves_thumbnail_path_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(identity_registry, "_faces_dir", return_value=Path(tmpdir) / "faces"):
                reg = {"version": 1, "identities": []}
                reg, iid = identity_registry.add_identity(reg, "Eve", "Eve", [0.1] * 128, None)
        self.assertEqual(reg["identities"][0]["thumbnail_path"], "")

    def test_crop_bytes_provided_sets_thumbnail_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            faces_dir = Path(tmpdir) / "faces"
            with patch.object(identity_registry, "_faces_dir", return_value=faces_dir):
                reg = {"version": 1, "identities": []}
                reg, iid = identity_registry.add_identity(reg, "Frank", "Frank", [0.1] * 128, b"jpeg")
        self.assertNotEqual(reg["identities"][0]["thumbnail_path"], "")
        self.assertTrue(reg["identities"][0]["thumbnail_path"].startswith("faces/"))


if __name__ == "__main__":
    unittest.main()
