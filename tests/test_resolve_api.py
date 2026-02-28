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

    def test_deduplicates_keywords(self):
        item = self._make_item({"Keywords": "Ohio, Ohio, Toledo, Toledo"})
        self.assertEqual(resolve_api.get_keywords(item), ["Ohio", "Toledo"])

    def test_deduplicates_case_insensitively(self):
        item = self._make_item({"Keywords": "ohio, Ohio"})
        self.assertEqual(resolve_api.get_keywords(item), ["ohio"])


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

    def test_returns_top_5_by_proximity_score(self):
        # Layout (sorted by date, cur at index 2):
        #   n1(d=2): alpha, beta   weight=0.5
        #   n2(d=1): alpha, beta   weight=1.0
        #   cur(d=0): []
        #   n3(d=1): alpha, beta, gamma  weight=1.0
        #   n4(d=2): alpha, delta  weight=0.5
        #   n5(d=3): alpha         weight=0.333
        # Scores: alpha=3.333, beta=2.5, gamma=1.0, delta=0.5
        clips = [
            self._make_clip("n1", ["alpha", "beta"], "01/01/2024 10:00:00"),
            self._make_clip("n2", ["alpha", "beta"], "01/01/2024 11:00:00"),
            self._make_clip("cur", [], "01/01/2024 12:00:00"),
            self._make_clip("n3", ["alpha", "beta", "gamma"], "01/01/2024 13:00:00"),
            self._make_clip("n4", ["alpha", "delta"], "01/01/2024 14:00:00"),
            self._make_clip("n5", ["alpha"], "01/01/2024 15:00:00"),
        ]
        resolve = self._make_resolve(clips, "cur")
        suggestions = resolve_api.suggest_keywords(resolve)[0]
        self.assertEqual(suggestions[0], "alpha")  # score=3.333
        self.assertEqual(suggestions[1], "beta")   # score=2.5
        self.assertEqual(suggestions[2], "gamma")  # score=1.0
        self.assertEqual(suggestions[3], "delta")  # score=0.5
        self.assertEqual(len(suggestions), 4)       # only 4 unique candidates

    def test_proximity_prefers_close_neighbours(self):
        # "near" appears only on adjacent clips; "far" appears on many but distant ones.
        # near: clips at distance 1 each → score = 1.0 + 1.0 = 2.0
        # far:  clips at distance 4 each → score = 0.25 + 0.25 = 0.5
        clips = [
            self._make_clip("f1", ["far"], "01/01/2024 07:00:00"),
            self._make_clip("f2", ["far"], "01/01/2024 08:00:00"),
            self._make_clip("f3", ["far"], "01/01/2024 09:00:00"),
            self._make_clip("f4", ["far"], "01/01/2024 10:00:00"),
            self._make_clip("near1", ["near"], "01/01/2024 11:00:00"),
            self._make_clip("cur",   [],       "01/01/2024 12:00:00"),
            self._make_clip("near2", ["near"], "01/01/2024 13:00:00"),
        ]
        resolve = self._make_resolve(clips, "cur")
        suggestions = resolve_api.suggest_keywords(resolve)[0]
        self.assertEqual(suggestions[0], "near")

    def test_excludes_current_clip_keywords(self):
        clips = [
            self._make_clip("n1", ["alpha", "existing"], "01/01/2024 10:00:00"),
            self._make_clip("n2", ["alpha", "existing"], "01/01/2024 11:00:00"),
            self._make_clip("cur", ["existing"], "01/01/2024 12:00:00"),
            self._make_clip("n3", ["alpha", "existing"], "01/01/2024 13:00:00"),
        ]
        resolve = self._make_resolve(clips, "cur")
        suggestions = resolve_api.suggest_keywords(resolve)[0]
        self.assertNotIn("existing", [s.lower() for s in suggestions])
        self.assertIn("alpha", suggestions)

    def test_returns_empty_when_no_neighbours_have_keywords(self):
        clips = [
            self._make_clip("n1", [], "01/01/2024 10:00:00"),
            self._make_clip("cur", [], "01/01/2024 12:00:00"),
            self._make_clip("n2", [], "01/01/2024 13:00:00"),
        ]
        resolve = self._make_resolve(clips, "cur")
        self.assertEqual(resolve_api.suggest_keywords(resolve)[0], [])

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
        self.assertEqual(resolve_api.suggest_keywords(resolve)[0], [])

    def test_fewer_than_5_candidates_returns_what_exists(self):
        clips = [
            self._make_clip("n1", ["alpha"], "01/01/2024 10:00:00"),
            self._make_clip("cur", [], "01/01/2024 12:00:00"),
            self._make_clip("n2", ["beta"], "01/01/2024 13:00:00"),
        ]
        resolve = self._make_resolve(clips, "cur")
        suggestions = resolve_api.suggest_keywords(resolve)[0]
        self.assertEqual(len(suggestions), 2)
        self.assertIn("alpha", suggestions)
        self.assertIn("beta", suggestions)

    def test_excludes_undated_clips(self):
        # Clips with no parseable date get datetime.max — they must not be
        # treated as same-day neighbours of each other.
        clips = [
            self._make_clip("undated1", ["wrong"],    ""),
            self._make_clip("cur",      [],            ""),
            self._make_clip("undated2", ["also_wrong"], ""),
        ]
        resolve = self._make_resolve(clips, "cur")
        self.assertEqual(resolve_api.suggest_keywords(resolve)[0], [])

    def test_parses_weekday_month_day_year_format(self):
        # Format returned by some Resolve versions: "Sat Sep 28 2024 19:35:21"
        clips = [
            self._make_clip("cur",     [],           "Sat Sep 28 2024 10:00:00"),
            self._make_clip("sameday", ["edinburgh"], "Sat Sep 28 2024 19:35:21"),
        ]
        resolve = self._make_resolve(clips, "cur")
        suggestions = resolve_api.suggest_keywords(resolve)[0]
        self.assertIn("edinburgh", suggestions)

    def test_excludes_clips_from_different_days(self):
        clips = [
            self._make_clip("yesterday", ["other_day"], "01/01/2024 23:00:00"),
            self._make_clip("cur",       [],             "01/02/2024 10:00:00"),
            self._make_clip("sameday",   ["same_day"],   "01/02/2024 14:00:00"),
        ]
        resolve = self._make_resolve(clips, "cur")
        suggestions = resolve_api.suggest_keywords(resolve)[0]
        self.assertIn("same_day", suggestions)
        self.assertNotIn("other_day", suggestions)


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


class TestFramesFromFilePath(unittest.TestCase):
    def _run(self, duration_stdout=b"10.0", ffprobe_rc=0, frame_rc=0, frame_stdout=b"PNG"):
        with patch("resolve_api._ffmpeg_path", return_value="/usr/bin/ffmpeg"), \
             patch("resolve_api._ffprobe_path", return_value="/usr/bin/ffprobe"), \
             patch("resolve_api.subprocess") as mock_sub:
            mock_sub.run.side_effect = [
                MagicMock(returncode=ffprobe_rc, stdout=duration_stdout),
            ] + [MagicMock(returncode=frame_rc, stdout=frame_stdout)] * 10
            result = resolve_api.frames_from_file_path("/fake/clip.mov")
        return result, mock_sub

    def test_returns_five_frames_for_known_duration(self):
        frames, _ = self._run()
        self.assertEqual(len(frames), 5)

    def test_all_frames_are_png_bytes(self):
        frames, _ = self._run()
        self.assertTrue(all(f == b"PNG" for f in frames))

    def test_seeks_at_correct_percentages(self):
        _, mock_sub = self._run(duration_stdout=b"100.0")
        seek_args = {call[0][0][2] for call in mock_sub.run.call_args_list[1:]}
        self.assertEqual(seek_args, {"10.0", "30.0", "50.0", "70.0", "90.0"})

    def test_falls_back_to_single_frame_when_duration_unknown(self):
        frames, mock_sub = self._run(ffprobe_rc=1, duration_stdout=b"")
        self.assertEqual(len(frames), 1)
        seek_arg = mock_sub.run.call_args_list[1][0][0][2]
        self.assertEqual(seek_arg, "0.0")

    def test_skips_failed_frames(self):
        with patch("resolve_api._ffmpeg_path", return_value="/usr/bin/ffmpeg"), \
             patch("resolve_api._ffprobe_path", return_value="/usr/bin/ffprobe"), \
             patch("resolve_api.subprocess") as mock_sub:
            mock_sub.run.side_effect = [
                MagicMock(returncode=0, stdout=b"10.0"),
                MagicMock(returncode=0, stdout=b"F1"),
                MagicMock(returncode=1, stdout=b""),   # fail
                MagicMock(returncode=0, stdout=b"F3"),
                MagicMock(returncode=0, stdout=b"F4"),
                MagicMock(returncode=0, stdout=b"F5"),
            ]
            frames = resolve_api.frames_from_file_path("/fake/clip.mov")
        self.assertEqual(len(frames), 4)

    def test_returns_empty_when_ffmpeg_not_found(self):
        with patch("resolve_api._ffmpeg_path", side_effect=FileNotFoundError):
            self.assertEqual(resolve_api.frames_from_file_path("/fake/clip.mov"), [])


class TestAiSuggestKeywords(unittest.TestCase):
    def _make_urlopen(self, response_text):
        body = json.dumps({"response": response_text}).encode()
        cm = MagicMock()
        cm.__enter__ = lambda s: MagicMock(read=MagicMock(return_value=body))
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    def test_returns_three_keywords(self):
        with patch("resolve_api.frames_from_file_path", return_value=[b"F1", b"F2", b"F3"]), \
             patch("resolve_api.urllib.request.urlopen", return_value=self._make_urlopen("mountain landscape, sunset, rolling hills")):
            result = resolve_api.ai_suggest_keywords("/fake/clip.mov")
        self.assertEqual(result, ["mountain landscape", "sunset", "rolling hills"])

    def test_all_frames_sent_in_images_array(self):
        frames = [b"F1", b"F2", b"F3", b"F4", b"F5"]
        with patch("resolve_api.frames_from_file_path", return_value=frames), \
             patch("resolve_api.urllib.request.urlopen", return_value=self._make_urlopen("a, b, c")) as mock_open:
            resolve_api.ai_suggest_keywords("/fake/clip.mov")
        payload = json.loads(mock_open.call_args[0][0].data)
        self.assertEqual(len(payload["images"]), 5)

    def test_existing_keywords_included_in_prompt(self):
        with patch("resolve_api.frames_from_file_path", return_value=[b"F1"]), \
             patch("resolve_api.urllib.request.urlopen", return_value=self._make_urlopen("waterfall, mist, rocks")) as mock_open:
            resolve_api.ai_suggest_keywords("/fake/clip.mov", existing_keywords=["sunset", "beach"])
        called_payload = json.loads(mock_open.call_args[0][0].data)
        self.assertIn("sunset", called_payload["prompt"])
        self.assertIn("beach", called_payload["prompt"])

    def test_catalog_keywords_included_in_prompt(self):
        with patch("resolve_api.frames_from_file_path", return_value=[b"F1"]), \
             patch("resolve_api.urllib.request.urlopen", return_value=self._make_urlopen("waterfall, mist, rocks")) as mock_open:
            resolve_api.ai_suggest_keywords(
                "/fake/clip.mov",
                catalog=["golden hour", "street photography", "Eiffel Tower"],
            )
        called_payload = json.loads(mock_open.call_args[0][0].data)
        self.assertIn("golden hour", called_payload["prompt"])
        self.assertIn("Eiffel Tower", called_payload["prompt"])

    def test_proximity_suggestions_included_in_prompt(self):
        with patch("resolve_api.frames_from_file_path", return_value=[b"F1"]), \
             patch("resolve_api.urllib.request.urlopen", return_value=self._make_urlopen("waterfall, mist, rocks")) as mock_open:
            resolve_api.ai_suggest_keywords(
                "/fake/clip.mov",
                proximity_suggestions=["alpine meadow", "hiking"],
            )
        called_payload = json.loads(mock_open.call_args[0][0].data)
        self.assertIn("alpine meadow", called_payload["prompt"])
        self.assertIn("hiking", called_payload["prompt"])

    def test_deduplicates_against_existing_keywords(self):
        with patch("resolve_api.frames_from_file_path", return_value=[b"F1"]), \
             patch("resolve_api.urllib.request.urlopen", return_value=self._make_urlopen("Imagination Station, Toledo, waterfall")):
            result = resolve_api.ai_suggest_keywords(
                "/fake/clip.mov",
                existing_keywords=["Imagination Station", "Toledo", "Ohio"],
            )
        self.assertNotIn("Imagination Station", result)
        self.assertNotIn("Toledo", result)
        self.assertIn("waterfall", result)

    def test_deduplicates_within_suggestions(self):
        with patch("resolve_api.frames_from_file_path", return_value=[b"F1"]), \
             patch("resolve_api.urllib.request.urlopen", return_value=self._make_urlopen("sunset, sunset, rolling hills")):
            result = resolve_api.ai_suggest_keywords("/fake/clip.mov")
        self.assertEqual(result.count("sunset"), 1)

    def test_returns_empty_when_ollama_unreachable(self):
        with patch("resolve_api.frames_from_file_path", return_value=[b"F1"]), \
             patch("resolve_api.urllib.request.urlopen", side_effect=OSError("connection refused")):
            result = resolve_api.ai_suggest_keywords("/fake/clip.mov")
        self.assertEqual(result, [])

    def test_returns_empty_when_no_frames(self):
        with patch("resolve_api.frames_from_file_path", return_value=[]):
            result = resolve_api.ai_suggest_keywords("/fake/clip.mov")
        self.assertEqual(result, [])

    def test_returns_empty_when_response_is_empty(self):
        with patch("resolve_api.frames_from_file_path", return_value=[b"F1"]), \
             patch("resolve_api.urllib.request.urlopen", return_value=self._make_urlopen("")):
            result = resolve_api.ai_suggest_keywords("/fake/clip.mov")
        self.assertEqual(result, [])


class TestGetAllProjectKeywords(unittest.TestCase):
    def _make_clip(self, keywords_str: str) -> MagicMock:
        clip = MagicMock()
        clip.GetMetadata.side_effect = lambda key=None: (
            {"Keywords": keywords_str} if key is None else (keywords_str if key == "Keywords" else None)
        )
        clip.GetClipProperty.return_value = ""
        return clip

    def _make_folder(self, clips, subfolders=None) -> MagicMock:
        folder = MagicMock()
        folder.GetClipList.return_value = clips
        folder.GetSubFolderList.return_value = subfolders or []
        return folder

    def _make_resolve(self, root_folder) -> MagicMock:
        resolve = MagicMock()
        project = resolve.GetProjectManager.return_value.GetCurrentProject.return_value
        media_pool = project.GetMediaPool.return_value
        media_pool.GetRootFolder.return_value = root_folder
        return resolve

    def test_collects_keywords_from_root_folder(self):
        clips = [self._make_clip("city, night"), self._make_clip("interview")]
        root = self._make_folder(clips)
        resolve = self._make_resolve(root)
        result = resolve_api.get_all_project_keywords(resolve)
        self.assertEqual(result, ["city", "interview", "night"])

    def test_collects_keywords_from_subfolders_recursively(self):
        sub_clips = [self._make_clip("landscape, sunset")]
        sub = self._make_folder(sub_clips)
        root_clips = [self._make_clip("city")]
        root = self._make_folder(root_clips, subfolders=[sub])
        resolve = self._make_resolve(root)
        result = resolve_api.get_all_project_keywords(resolve)
        self.assertEqual(result, ["city", "landscape", "sunset"])

    def test_deduplicates_across_clips(self):
        clips = [self._make_clip("city, night"), self._make_clip("city, interview")]
        root = self._make_folder(clips)
        resolve = self._make_resolve(root)
        result = resolve_api.get_all_project_keywords(resolve)
        self.assertEqual(result.count("city"), 1)

    def test_returns_empty_when_no_project(self):
        resolve = MagicMock()
        resolve.GetProjectManager.return_value.GetCurrentProject.return_value = None
        result = resolve_api.get_all_project_keywords(resolve)
        self.assertEqual(result, [])

    def test_sorted_case_insensitive(self):
        clips = [self._make_clip("Zoo, apple, Banana")]
        root = self._make_folder(clips)
        resolve = self._make_resolve(root)
        result = resolve_api.get_all_project_keywords(resolve)
        self.assertEqual(result, ["apple", "Banana", "Zoo"])


if __name__ == "__main__":
    unittest.main()
