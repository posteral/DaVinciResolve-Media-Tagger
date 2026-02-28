from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

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


class TestThumbnailFromFilePath(unittest.TestCase):
    def _run(self, ffprobe_stdout=b"10.0", ffprobe_rc=0, ffmpeg_stdout=b"PNG", ffmpeg_rc=0):
        """Run thumbnail_from_file_path with mocked subprocesses."""
        with patch("resolve_api._ffmpeg_path", return_value="/usr/bin/ffmpeg"), \
             patch("resolve_api._ffprobe_path", return_value="/usr/bin/ffprobe"), \
             patch("resolve_api.subprocess") as mock_sub:

            probe_result = MagicMock()
            probe_result.returncode = ffprobe_rc
            probe_result.stdout = ffprobe_stdout

            ffmpeg_result = MagicMock()
            ffmpeg_result.returncode = ffmpeg_rc
            ffmpeg_result.stdout = ffmpeg_stdout

            mock_sub.run.side_effect = [probe_result, ffmpeg_result]

            return mock_sub, resolve_api.thumbnail_from_file_path("/fake/clip.mov")

    def test_returns_png_bytes_on_success(self):
        _, result = self._run()
        self.assertEqual(result, b"PNG")

    def test_returns_none_when_ffmpeg_fails(self):
        _, result = self._run(ffmpeg_rc=1, ffmpeg_stdout=b"")
        self.assertIsNone(result)

    def test_returns_none_when_ffmpeg_returns_empty_output(self):
        _, result = self._run(ffmpeg_stdout=b"")
        self.assertIsNone(result)

    def test_returns_none_when_file_path_is_empty(self):
        result = resolve_api.thumbnail_from_file_path("")
        self.assertIsNone(result)

    def test_returns_none_when_ffmpeg_not_found(self):
        with patch("resolve_api._ffmpeg_path", side_effect=FileNotFoundError):
            result = resolve_api.thumbnail_from_file_path("/fake/clip.mov")
        self.assertIsNone(result)

    def test_seeks_to_midpoint(self):
        mock_sub, _ = self._run(ffprobe_stdout=b"20.0")
        ffmpeg_call_args = mock_sub.run.call_args_list[1][0][0]
        self.assertIn("10.0", ffmpeg_call_args)

    def test_seeks_to_zero_when_probe_fails(self):
        mock_sub, _ = self._run(ffprobe_rc=1, ffprobe_stdout=b"")
        ffmpeg_call_args = mock_sub.run.call_args_list[1][0][0]
        self.assertIn("0.0", ffmpeg_call_args)

    def test_returns_none_when_subprocess_raises(self):
        with patch("resolve_api._ffmpeg_path", return_value="/usr/bin/ffmpeg"), \
             patch("resolve_api._ffprobe_path", return_value="/usr/bin/ffprobe"), \
             patch("resolve_api.subprocess") as mock_sub:
            mock_sub.run.side_effect = [MagicMock(returncode=0, stdout=b"5.0"),
                                        Exception("process error")]
            result = resolve_api.thumbnail_from_file_path("/fake/clip.mov")
        self.assertIsNone(result)


class TestSuggestKeywords(unittest.TestCase):
    def _make_clip(self, media_id, keywords, date="01/01/2024 12:00:00"):
        clip = MagicMock()
        clip.GetMediaId.return_value = media_id
        clip.GetName.return_value = media_id
        clip.GetClipProperty.side_effect = lambda k: date if k == "Date Created" else (", ".join(keywords) if k == "Keywords" else "")
        clip.GetMetadata.side_effect = lambda k=None: {"Keywords": ", ".join(keywords)} if k is None else (", ".join(keywords) if k == "Keywords" else None)
        return clip

    def _make_resolve(self, clips, current_id):
        resolve = MagicMock()
        folder = MagicMock()
        folder.GetClipList.return_value = {str(i): c for i, c in enumerate(clips)}
        media_pool = MagicMock()
        media_pool.GetCurrentFolder.return_value = folder
        media_pool.GetSelectedClips.return_value = {
            "1": next(c for c in clips if c.GetMediaId() == current_id)
        }
        project = MagicMock()
        project.GetMediaPool.return_value = media_pool
        project.GetCurrentTimeline.return_value = None
        project_manager = MagicMock()
        project_manager.GetCurrentProject.return_value = project
        resolve.GetProjectManager.return_value = project_manager
        return resolve

    def test_returns_top_3_by_frequency(self):
        # 5 neighbours each with "alpha", 3 with "beta", 1 with "gamma" and "delta"
        clips = [
            self._make_clip("n1", ["alpha", "beta"], "01/01/2024 10:00:00"),
            self._make_clip("n2", ["alpha", "beta"], "01/01/2024 11:00:00"),
            self._make_clip("cur", [], "01/01/2024 12:00:00"),
            self._make_clip("n3", ["alpha", "beta", "gamma"], "01/01/2024 13:00:00"),
            self._make_clip("n4", ["alpha", "delta"], "01/01/2024 14:00:00"),
            self._make_clip("n5", ["alpha"], "01/01/2024 15:00:00"),
        ]
        resolve = self._make_resolve(clips, "cur")
        suggestions = resolve_api.suggest_keywords(resolve)
        self.assertEqual(suggestions[0], "alpha")  # freq=5
        self.assertEqual(suggestions[1], "beta")   # freq=3
        self.assertIn(suggestions[2], ["gamma", "delta"])  # freq=1 tie
        self.assertEqual(len(suggestions), 3)

    def test_excludes_current_clip_keywords(self):
        clips = [
            self._make_clip("n1", ["alpha", "existing"], "01/01/2024 10:00:00"),
            self._make_clip("n2", ["alpha", "existing"], "01/01/2024 11:00:00"),
            self._make_clip("cur", ["existing"], "01/01/2024 12:00:00"),
            self._make_clip("n3", ["alpha", "existing"], "01/01/2024 13:00:00"),
        ]
        resolve = self._make_resolve(clips, "cur")
        suggestions = resolve_api.suggest_keywords(resolve)
        self.assertNotIn("existing", [s.lower() for s in suggestions])
        self.assertIn("alpha", suggestions)

    def test_returns_empty_when_no_neighbours_have_keywords(self):
        clips = [
            self._make_clip("n1", [], "01/01/2024 10:00:00"),
            self._make_clip("cur", [], "01/01/2024 12:00:00"),
            self._make_clip("n2", [], "01/01/2024 13:00:00"),
        ]
        resolve = self._make_resolve(clips, "cur")
        self.assertEqual(resolve_api.suggest_keywords(resolve), [])

    def test_returns_empty_when_no_current_item(self):
        resolve = MagicMock()
        project_manager = MagicMock()
        project = MagicMock()
        media_pool = MagicMock()
        media_pool.GetSelectedClips.return_value = {}
        project.GetMediaPool.return_value = media_pool
        project.GetCurrentTimeline.return_value = None
        project_manager.GetCurrentProject.return_value = project
        resolve.GetProjectManager.return_value = project_manager
        self.assertEqual(resolve_api.suggest_keywords(resolve), [])

    def test_fewer_than_3_candidates_returns_what_exists(self):
        clips = [
            self._make_clip("n1", ["alpha"], "01/01/2024 10:00:00"),
            self._make_clip("cur", [], "01/01/2024 12:00:00"),
            self._make_clip("n2", ["beta"], "01/01/2024 13:00:00"),
        ]
        resolve = self._make_resolve(clips, "cur")
        suggestions = resolve_api.suggest_keywords(resolve)
        self.assertEqual(len(suggestions), 2)
        self.assertIn("alpha", suggestions)
        self.assertIn("beta", suggestions)


class TestNormaliseAiKeyword(unittest.TestCase):
    def test_generic_phrase_lowercased(self):
        self.assertEqual(resolve_api._normalise_ai_keyword("Street scene"), "street scene")
        self.assertEqual(resolve_api._normalise_ai_keyword("Narrow alleyway"), "narrow alleyway")
        self.assertEqual(resolve_api._normalise_ai_keyword("Outdoor seating"), "outdoor seating")
        self.assertEqual(resolve_api._normalise_ai_keyword("Wedding photographer"), "wedding photographer")
        self.assertEqual(resolve_api._normalise_ai_keyword("Model Train Set"), "model train set")
        self.assertEqual(resolve_api._normalise_ai_keyword("Cemetery"), "cemetery")
        self.assertEqual(resolve_api._normalise_ai_keyword("Boat"), "boat")

    def test_proper_noun_restored_from_existing_keywords(self):
        kws = ["New York City", "Maria"]
        # Single-word keyword "Maria" → restored by word match
        self.assertEqual(
            resolve_api._normalise_ai_keyword("maria sharapova", kws),
            "Maria sharapova",
        )
        # Multi-word keyword "New York City" → restored only as full phrase
        self.assertEqual(
            resolve_api._normalise_ai_keyword("new york city skyline", kws),
            "New York City skyline",
        )
        # Partial match ("new york" without "city") → stays lowercase
        self.assertEqual(
            resolve_api._normalise_ai_keyword("new york street food vendors", kws),
            "new york street food vendors",
        )

    def test_already_lowercase_unchanged(self):
        self.assertEqual(resolve_api._normalise_ai_keyword("rolling hills"), "rolling hills")
        self.assertEqual(resolve_api._normalise_ai_keyword("concert crowd"), "concert crowd")

    def test_empty_string(self):
        self.assertEqual(resolve_api._normalise_ai_keyword(""), "")


class TestAiSuggestKeywords(unittest.TestCase):
    def _make_urlopen(self, response_text):
        body = json.dumps({"response": response_text}).encode()
        cm = MagicMock()
        cm.__enter__ = lambda s: MagicMock(read=MagicMock(return_value=body))
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    def test_returns_three_keywords(self):
        with patch("resolve_api.thumbnail_from_file_path", return_value=b"PNG"), \
             patch("resolve_api.urllib.request.urlopen", return_value=self._make_urlopen("mountain landscape, sunset, rolling hills")):
            result = resolve_api.ai_suggest_keywords("/fake/clip.mov")
        self.assertEqual(result, ["mountain landscape", "sunset", "rolling hills"])

    def test_existing_keywords_included_in_prompt(self):
        with patch("resolve_api.thumbnail_from_file_path", return_value=b"PNG"), \
             patch("resolve_api.urllib.request.urlopen", return_value=self._make_urlopen("waterfall, mist, rocks")) as mock_open:
            resolve_api.ai_suggest_keywords("/fake/clip.mov", existing_keywords=["sunset", "beach"])
        called_payload = json.loads(mock_open.call_args[0][0].data)
        self.assertIn("sunset", called_payload["prompt"])
        self.assertIn("beach", called_payload["prompt"])

    def test_deduplicates_against_existing_keywords(self):
        with patch("resolve_api.thumbnail_from_file_path", return_value=b"PNG"), \
             patch("resolve_api.urllib.request.urlopen", return_value=self._make_urlopen("Imagination Station, Toledo, waterfall")):
            result = resolve_api.ai_suggest_keywords(
                "/fake/clip.mov",
                existing_keywords=["Imagination Station", "Toledo", "Ohio"],
            )
        self.assertNotIn("Imagination Station", result)
        self.assertNotIn("Toledo", result)
        self.assertIn("waterfall", result)

    def test_deduplicates_within_suggestions(self):
        with patch("resolve_api.thumbnail_from_file_path", return_value=b"PNG"), \
             patch("resolve_api.urllib.request.urlopen", return_value=self._make_urlopen("sunset, sunset, rolling hills")):
            result = resolve_api.ai_suggest_keywords("/fake/clip.mov")
        self.assertEqual(result.count("sunset"), 1)

    def test_returns_empty_when_ollama_unreachable(self):
        with patch("resolve_api.thumbnail_from_file_path", return_value=b"PNG"), \
             patch("resolve_api.urllib.request.urlopen", side_effect=OSError("connection refused")):
            result = resolve_api.ai_suggest_keywords("/fake/clip.mov")
        self.assertEqual(result, [])

    def test_returns_empty_when_thumbnail_unavailable(self):
        with patch("resolve_api.thumbnail_from_file_path", return_value=None):
            result = resolve_api.ai_suggest_keywords("/fake/clip.mov")
        self.assertEqual(result, [])

    def test_returns_empty_when_response_is_empty(self):
        with patch("resolve_api.thumbnail_from_file_path", return_value=b"PNG"), \
             patch("resolve_api.urllib.request.urlopen", return_value=self._make_urlopen("")):
            result = resolve_api.ai_suggest_keywords("/fake/clip.mov")
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
