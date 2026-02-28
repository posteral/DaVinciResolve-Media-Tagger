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

    def test_fifo_cap(self):
        embeddings = [[float(i)] * 128 for i in range(20)]
        reg = {"version": 1, "identities": [
            {"identity_id": "abc", "embeddings": embeddings, "thumbnail_path": ""}
        ]}
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(identity_registry, "_faces_dir", return_value=Path(tmpdir) / "faces"):
                reg = identity_registry.update_identity_embedding(reg, "abc", [99.0] * 128, None)
        self.assertEqual(len(reg["identities"][0]["embeddings"]), 20)
        # oldest dropped, newest kept
        self.assertEqual(reg["identities"][0]["embeddings"][-1], [99.0] * 128)
        self.assertEqual(reg["identities"][0]["embeddings"][0], [1.0] * 128)


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


if __name__ == "__main__":
    unittest.main()
