from __future__ import annotations

import json
import sys
import unittest
from unittest.mock import MagicMock, patch

import main
import resolve_api


class TestNormalizeKeywords(unittest.TestCase):
    def test_comma_separated_string(self):
        self.assertEqual(resolve_api._normalize_keywords("a, b, c"), ["a", "b", "c"])

    def test_semicolon_separated_string(self):
        self.assertEqual(resolve_api._normalize_keywords("a; b; c"), ["a", "b", "c"])

    def test_single_value(self):
        self.assertEqual(resolve_api._normalize_keywords("tag"), ["tag"])

    def test_list_input(self):
        self.assertEqual(resolve_api._normalize_keywords(["a", "b"]), ["a", "b"])

    def test_none_returns_empty(self):
        self.assertEqual(resolve_api._normalize_keywords(None), [])

    def test_empty_string_returns_empty(self):
        self.assertEqual(resolve_api._normalize_keywords(""), [])

    def test_strips_whitespace(self):
        self.assertEqual(resolve_api._normalize_keywords("  tag  "), ["tag"])


class TestDedupePreserveOrder(unittest.TestCase):
    def test_removes_case_insensitive_duplicates(self):
        self.assertEqual(resolve_api._dedupe_preserve_order(["tag", "Tag", "TAG"]), ["tag"])

    def test_preserves_first_occurrence(self):
        self.assertEqual(resolve_api._dedupe_preserve_order(["Tag", "tag"]), ["Tag"])

    def test_preserves_order(self):
        self.assertEqual(resolve_api._dedupe_preserve_order(["b", "a", "c"]), ["b", "a", "c"])

    def test_empty_list(self):
        self.assertEqual(resolve_api._dedupe_preserve_order([]), [])


class TestMergeKeywords(unittest.TestCase):
    def test_set_replaces_existing(self):
        self.assertEqual(resolve_api.merge_keywords(["old"], ["new"], "set"), ["new"])

    def test_set_dedupes_incoming(self):
        self.assertEqual(resolve_api.merge_keywords([], ["a", "A"], "set"), ["a"])

    def test_replace_is_alias_for_set(self):
        self.assertEqual(resolve_api.merge_keywords(["old"], ["new"], "replace"), ["new"])

    def test_append_combines(self):
        self.assertEqual(resolve_api.merge_keywords(["a"], ["b"], "append"), ["a", "b"])

    def test_append_dedupes(self):
        self.assertEqual(resolve_api.merge_keywords(["a"], ["a", "b"], "append"), ["a", "b"])

    def test_append_case_insensitive_dedupe(self):
        self.assertEqual(resolve_api.merge_keywords(["Tag"], ["tag", "new"], "append"), ["Tag", "new"])

    def test_set_empty_clears(self):
        self.assertEqual(resolve_api.merge_keywords(["old"], [], "set"), [])

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            resolve_api.merge_keywords([], [], "unknown")


class TestGetKeywords(unittest.TestCase):
    def _make_item(self, metadata: dict, clip_property: str = "") -> MagicMock:
        item = MagicMock()
        item.GetMetadata.side_effect = lambda key=None: metadata if key is None else metadata.get(key)
        item.GetClipProperty.return_value = clip_property
        return item

    def test_reads_from_metadata_dict(self):
        item = self._make_item({"Keywords": "a, b"})
        self.assertEqual(resolve_api.get_keywords(item), ["a", "b"])

    def test_falls_back_to_explicit_key(self):
        item = MagicMock()
        item.GetMetadata.side_effect = lambda key=None: {} if key is None else ("a" if key == "Keywords" else None)
        item.GetClipProperty.return_value = ""
        self.assertEqual(resolve_api.get_keywords(item), ["a"])

    def test_falls_back_to_clip_property(self):
        item = MagicMock()
        item.GetMetadata.side_effect = lambda key=None: {} if key is None else None
        item.GetClipProperty.return_value = "x; y"
        self.assertEqual(resolve_api.get_keywords(item), ["x", "y"])

    def test_returns_empty_when_nothing(self):
        item = MagicMock()
        item.GetMetadata.side_effect = lambda key=None: {} if key is None else None
        item.GetClipProperty.return_value = ""
        self.assertEqual(resolve_api.get_keywords(item), [])


class TestSetKeywords(unittest.TestCase):
    def test_returns_true_on_success(self):
        item = MagicMock()
        item.SetMetadata.return_value = True
        self.assertTrue(resolve_api.set_keywords(item, ["a", "b"]))
        item.SetMetadata.assert_called_once_with("Keywords", "a, b")

    def test_returns_false_on_failure(self):
        item = MagicMock()
        item.SetMetadata.return_value = False
        self.assertFalse(resolve_api.set_keywords(item, ["a"]))

    def test_returns_false_on_none(self):
        item = MagicMock()
        item.SetMetadata.return_value = None
        self.assertFalse(resolve_api.set_keywords(item, ["a"]))

    def test_empty_keywords_writes_empty_string(self):
        item = MagicMock()
        item.SetMetadata.return_value = True
        resolve_api.set_keywords(item, [])
        item.SetMetadata.assert_called_once_with("Keywords", "")


class TestFormatOutput(unittest.TestCase):
    def test_human_with_keywords(self):
        out = resolve_api.format_output("clip.mp4", ["a", "b"], as_json=False)
        self.assertIn("Clip: clip.mp4", out)
        self.assertIn("- a", out)
        self.assertIn("- b", out)

    def test_human_no_keywords(self):
        out = resolve_api.format_output("clip.mp4", [], as_json=False)
        self.assertIn("Keywords: (none)", out)

    def test_json_output(self):
        out = resolve_api.format_output("clip.mp4", ["a"], as_json=True)
        data = json.loads(out)
        self.assertEqual(data["clip"], "clip.mp4")
        self.assertEqual(data["keywords"], ["a"])


class TestMain(unittest.TestCase):
    def _make_item(self, name: str = "clip.mp4", keywords: list[str] | None = None) -> MagicMock:
        item = MagicMock()
        item.GetName.return_value = name
        kw_value = ", ".join(keywords or [])
        item.GetMetadata.side_effect = lambda key=None: (
            {"Keywords": kw_value} if key is None else (kw_value if key == "Keywords" else None)
        )
        item.GetClipProperty.return_value = ""
        item.SetMetadata.return_value = True
        return item

    def _run(self, args: list[str]) -> int:
        with patch.object(sys, "argv", ["main.py"] + args):
            return main.main()

    def test_read_returns_0(self):
        item = self._make_item(keywords=["a", "b"])
        with patch("main.get_resolve"), patch("main.get_selected_media_pool_item", return_value=item):
            self.assertEqual(self._run([]), 0)

    def test_read_no_clip_returns_1(self):
        with patch("main.get_resolve"), patch("main.get_selected_media_pool_item", return_value=None):
            self.assertEqual(self._run([]), 1)

    def test_resolve_error_returns_1(self):
        with patch("main.get_resolve", side_effect=RuntimeError("no resolve")):
            self.assertEqual(self._run([]), 1)

    def test_set_writes_keywords(self):
        item = self._make_item(keywords=[])
        with patch("main.get_resolve"), patch("main.get_selected_media_pool_item", return_value=item):
            result = self._run(["--set", "a,b"])
        self.assertEqual(result, 0)
        item.SetMetadata.assert_called_once_with("Keywords", "a, b")

    def test_append_merges_keywords(self):
        item = self._make_item(keywords=["existing"])
        with patch("main.get_resolve"), patch("main.get_selected_media_pool_item", return_value=item):
            result = self._run(["--append", "new"])
        self.assertEqual(result, 0)
        item.SetMetadata.assert_called_once_with("Keywords", "existing, new")

    def test_replace_is_alias_for_set(self):
        item = self._make_item(keywords=["old"])
        with patch("main.get_resolve"), patch("main.get_selected_media_pool_item", return_value=item):
            result = self._run(["--replace", "new"])
        self.assertEqual(result, 0)
        item.SetMetadata.assert_called_once_with("Keywords", "new")

    def test_dry_run_does_not_write(self):
        item = self._make_item(keywords=["old"])
        with patch("main.get_resolve"), patch("main.get_selected_media_pool_item", return_value=item):
            result = self._run(["--set", "new", "--dry-run"])
        self.assertEqual(result, 0)
        item.SetMetadata.assert_not_called()

    def test_set_write_failure_returns_1(self):
        item = self._make_item(keywords=[])
        item.SetMetadata.return_value = False
        with patch("main.get_resolve"), patch("main.get_selected_media_pool_item", return_value=item):
            result = self._run(["--set", "a"])
        self.assertEqual(result, 1)

    def test_json_read_output(self):
        item = self._make_item(keywords=["a"])
        with patch("main.get_resolve"), patch("main.get_selected_media_pool_item", return_value=item):
            with patch("builtins.print") as mock_print:
                result = self._run(["--json"])
        self.assertEqual(result, 0)
        printed = mock_print.call_args[0][0]
        data = json.loads(printed)
        self.assertIn("clip", data)
        self.assertIn("keywords", data)


if __name__ == "__main__":
    unittest.main()
