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
        item.SetClipProperty.assert_called_once_with("Keywords", "a, b")

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
        item.SetClipProperty.assert_called_once_with("Keywords", "")


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
        # Each keyword is scored by its NEAREST carrying clip (max 1/d, not sum).
        # Layout (sorted by date, cur at index 2):
        #   n1(d=2): alpha, beta   best=0.5
        #   n2(d=1): alpha, beta   best=1.0  ← raises alpha and beta to 1.0
        #   cur(d=0): []
        #   n3(d=1): alpha, beta, gamma  best=1.0  ← gamma=1.0
        #   n4(d=2): alpha, delta  best=0.5  ← delta=0.5 (no closer clip carries it)
        #   n5(d=3): alpha         best=0.333 (alpha already 1.0, no change)
        # Best scores: alpha=1.0, beta=1.0, gamma=1.0, delta=0.5
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
        # alpha, beta, gamma all tie at 1.0; delta at 0.5 — all 4 must appear
        self.assertIn("alpha", suggestions)
        self.assertIn("beta", suggestions)
        self.assertIn("gamma", suggestions)
        self.assertIn("delta", suggestions)
        self.assertEqual(len(suggestions), 4)  # only 4 unique candidates
        # delta must rank below the distance-1 keywords
        self.assertEqual(suggestions[-1], "delta")

    def test_proximity_prefers_close_neighbours(self):
        # "near" appears only on adjacent clips; "far" appears on many but distant ones.
        # near: nearest clip at distance 1 → best_score = 1.0
        # far:  nearest clip at distance 4 → best_score = 0.25
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
        # Clips with no parseable date get datetime.max — excluded from scoring
        # because their sort position is unreliable.
        clips = [
            self._make_clip("undated1", ["wrong"],     ""),
            self._make_clip("cur",      [],             "01/01/2024 12:00:00"),
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

    def test_includes_adjacent_clips_from_different_days(self):
        # The day-boundary filter has been removed: clips from any day are
        # suggested based purely on sequential distance. An immediately adjacent
        # clip (distance=1) should appear regardless of its calendar date.
        clips = [
            self._make_clip("yesterday", ["other_day"], "01/01/2024 23:00:00"),
            self._make_clip("cur",       [],             "01/02/2024 10:00:00"),
            self._make_clip("sameday",   ["same_day"],   "01/02/2024 14:00:00"),
        ]
        resolve = self._make_resolve(clips, "cur")
        suggestions = resolve_api.suggest_keywords(resolve)[0]
        self.assertIn("same_day", suggestions)
        self.assertIn("other_day", suggestions)

    def test_window_excludes_clips_beyond_50_positions(self):
        # Build a list where cur is at index 0, near at index 1 (distance=1),
        # and 60 "far" clips at indices 51..110 (distance 51-110, outside ±50 window).
        # 'far' should score 0; 'near' should score 1.0 and appear in suggestions.
        import datetime as _dt
        base = _dt.datetime(2024, 1, 1, 12, 0, 0)
        clips = []
        clips.append(self._make_clip("cur", [], base.strftime("%m/%d/%Y %H:%M:%S")))
        clips.append(self._make_clip("near1", ["near"],
                                     (base + _dt.timedelta(minutes=1)).strftime("%m/%d/%Y %H:%M:%S")))
        # 49 empty padding clips at indices 2..50 (inside window, no keywords)
        for i in range(2, 51):
            t = (base + _dt.timedelta(minutes=i)).strftime("%m/%d/%Y %H:%M:%S")
            clips.append(self._make_clip(f"pad{i}", [], t))
        # 60 "far" clips at indices 51..110 (all outside window)
        for i in range(51, 111):
            t = (base + _dt.timedelta(minutes=i)).strftime("%m/%d/%Y %H:%M:%S")
            clips.append(self._make_clip(f"far{i}", ["far"], t))
        resolve = self._make_resolve(clips, "cur")
        suggestions = resolve_api.suggest_keywords(resolve)[0]
        self.assertIn("near", suggestions)
        self.assertNotIn("far", suggestions)


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
            # First call is ffprobe; subsequent calls are concurrent ffmpeg extractions.
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
        # Calls after the first (ffprobe) are the per-frame ffmpeg calls.
        seek_args = {call[0][0][2] for call in mock_sub.run.call_args_list[1:6]}
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
                MagicMock(returncode=0, stdout=b"10.0"),   # ffprobe
                MagicMock(returncode=0, stdout=b"F1"),
                MagicMock(returncode=1, stdout=b""),        # one frame fails
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

    def test_catalog_and_proximity_not_in_prompt(self):
        # Catalog and proximity suggestions are no longer sent to the VLM —
        # only existing keywords and the image are used.
        with patch("resolve_api.frames_from_file_path", return_value=[b"F1"]), \
             patch("resolve_api.urllib.request.urlopen", return_value=self._make_urlopen("waterfall, mist, rocks")) as mock_open:
            resolve_api.ai_suggest_keywords(
                "/fake/clip.mov",
                proximity_suggestions=["alpine meadow", "hiking"],
                catalog=["golden hour", "Eiffel Tower"],
            )
        called_payload = json.loads(mock_open.call_args[0][0].data)
        self.assertNotIn("alpine meadow", called_payload["prompt"])
        self.assertNotIn("golden hour", called_payload["prompt"])

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

    def test_rejects_sentence_fragments(self):
        # LLMs sometimes return verbose sentences instead of short keyword phrases.
        # Anything longer than 40 chars or more than 5 words should be dropped.
        long_sentence = "this is a very long sentence that should be rejected by the filter"
        short_kw = "brick building"
        response = f"{long_sentence}, {short_kw}"
        with patch("resolve_api.frames_from_file_path", return_value=[b"F1"]), \
             patch("resolve_api.urllib.request.urlopen", return_value=self._make_urlopen(response)):
            result = resolve_api.ai_suggest_keywords("/fake/clip.mov")
        self.assertNotIn(long_sentence, result)
        self.assertIn("brick building", result)


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


class TestAsSequence(unittest.TestCase):
    """Tests for resolve_api._as_sequence."""

    def test_none_returns_empty_list(self):
        self.assertEqual(resolve_api._as_sequence(None), [])

    def test_dict_returns_values(self):
        result = resolve_api._as_sequence({"a": 1, "b": 2})
        self.assertIn(1, result)
        self.assertIn(2, result)
        self.assertEqual(len(result), 2)

    def test_dict_filters_none_values(self):
        result = resolve_api._as_sequence({"a": 1, "b": None, "c": 3})
        self.assertNotIn(None, result)
        self.assertEqual(len(result), 2)

    def test_list_returned_as_list(self):
        self.assertEqual(resolve_api._as_sequence([1, 2, 3]), [1, 2, 3])

    def test_tuple_returned_as_list(self):
        result = resolve_api._as_sequence((1, 2))
        self.assertIsInstance(result, list)
        self.assertEqual(result, [1, 2])

    def test_set_returned_as_list(self):
        result = resolve_api._as_sequence({10, 20})
        self.assertIsInstance(result, list)
        self.assertEqual(sorted(result), [10, 20])

    def test_scalar_wrapped_in_list(self):
        self.assertEqual(resolve_api._as_sequence("hello"), ["hello"])

    def test_int_scalar_wrapped_in_list(self):
        self.assertEqual(resolve_api._as_sequence(42), [42])


class TestDedupKeywords(unittest.TestCase):
    """Tests for resolve_api._dedup_keywords."""

    def test_empty_list(self):
        self.assertEqual(resolve_api._dedup_keywords([]), [])

    def test_basic_dedup(self):
        self.assertEqual(resolve_api._dedup_keywords(["a", "b", "a"]), ["a", "b"])

    def test_case_insensitive_dedup(self):
        result = resolve_api._dedup_keywords(["Ohio", "ohio"])
        self.assertEqual(result, ["Ohio"])

    def test_preserves_first_occurrence(self):
        # "Alpha" comes first; "alpha" is the duplicate → "Alpha" kept
        result = resolve_api._dedup_keywords(["Alpha", "beta", "ALPHA"])
        self.assertEqual(result[0], "Alpha")
        self.assertNotIn("ALPHA", result)

    def test_no_duplicates_unchanged(self):
        kws = ["alpha", "beta", "gamma"]
        self.assertEqual(resolve_api._dedup_keywords(kws), kws)

    def test_single_item(self):
        self.assertEqual(resolve_api._dedup_keywords(["x"]), ["x"])


class TestClipDateKey(unittest.TestCase):
    """Tests for resolve_api._clip_date_key."""

    def _make_clip(self, date_str, name="clip"):
        clip = MagicMock()
        clip.GetClipProperty.return_value = date_str
        clip.GetName.return_value = name
        return clip

    def test_mm_dd_yyyy_format(self):
        clip = self._make_clip("01/15/2024 08:30:00")
        dt, name = resolve_api._clip_date_key(clip)
        from datetime import datetime
        self.assertEqual(dt, datetime(2024, 1, 15, 8, 30, 0))

    def test_yyyy_mm_dd_format(self):
        clip = self._make_clip("2024-06-20 14:00:00")
        dt, name = resolve_api._clip_date_key(clip)
        from datetime import datetime
        self.assertEqual(dt, datetime(2024, 6, 20, 14, 0, 0))

    def test_dd_mm_yyyy_format(self):
        clip = self._make_clip("28/09/2024 19:35:21")
        dt, name = resolve_api._clip_date_key(clip)
        from datetime import datetime
        self.assertEqual(dt, datetime(2024, 9, 28, 19, 35, 21))

    def test_weekday_month_day_year_format(self):
        clip = self._make_clip("Sat Sep 28 2024 19:35:21")
        dt, name = resolve_api._clip_date_key(clip)
        from datetime import datetime
        self.assertEqual(dt, datetime(2024, 9, 28, 19, 35, 21))

    def test_unparseable_date_returns_max(self):
        from datetime import datetime
        clip = self._make_clip("not a date at all", name="test")
        dt, name = resolve_api._clip_date_key(clip)
        self.assertEqual(dt, datetime.max)
        self.assertEqual(name, "test")

    def test_empty_string_returns_max(self):
        from datetime import datetime
        clip = self._make_clip("", name="myclip")
        dt, name = resolve_api._clip_date_key(clip)
        self.assertEqual(dt, datetime.max)
        self.assertEqual(name, "myclip")

    def test_getclipproperty_raises_returns_max(self):
        from datetime import datetime
        clip = MagicMock()
        clip.GetClipProperty.side_effect = Exception("IPC error")
        clip.GetName.return_value = "badclip"
        dt, name = resolve_api._clip_date_key(clip)
        self.assertEqual(dt, datetime.max)
        self.assertEqual(name, "badclip")

    def test_name_returned_correctly(self):
        clip = self._make_clip("01/01/2024 10:00:00", name="myclip")
        _, name = resolve_api._clip_date_key(clip)
        self.assertEqual(name, "myclip")


class TestGetCachedSuggestions(unittest.TestCase):
    """Tests for resolve_api.get_cached_suggestions and invalidate_folder_cache."""

    def setUp(self):
        resolve_api._last_suggestions = None
        resolve_api._folder_cache = None

    def tearDown(self):
        resolve_api._last_suggestions = None
        resolve_api._folder_cache = None

    def test_cache_hit_returns_suggestions(self):
        resolve_api._last_suggestions = ("media-123", ["alpha", "beta"])
        result = resolve_api.get_cached_suggestions("media-123")
        self.assertEqual(result, ["alpha", "beta"])

    def test_cache_miss_wrong_id_returns_none(self):
        resolve_api._last_suggestions = ("media-999", ["alpha"])
        result = resolve_api.get_cached_suggestions("media-123")
        self.assertIsNone(result)

    def test_cold_cache_returns_none(self):
        resolve_api._last_suggestions = None
        result = resolve_api.get_cached_suggestions("any-id")
        self.assertIsNone(result)

    def test_invalidate_clears_both_caches(self):
        resolve_api._folder_cache = ("folder", [], {}, {}, {})
        resolve_api._last_suggestions = ("media-1", ["kw"])
        resolve_api.invalidate_folder_cache()
        self.assertIsNone(resolve_api._folder_cache)
        self.assertIsNone(resolve_api._last_suggestions)


class TestGetFolderCache(unittest.TestCase):
    """Tests for resolve_api._get_folder_cache."""

    def _make_clip(self, media_id, date="01/01/2024 12:00:00", keywords="alpha, beta", proxy=""):
        clip = MagicMock()
        clip.GetMediaId.return_value = media_id
        clip.GetName.return_value = media_id
        clip.GetClipProperty.side_effect = lambda k: (
            date if k == "Date Created"
            else (keywords if k == "Keywords" else (proxy if k == "Proxy Media Path" else ""))
        )
        clip.GetMetadata.side_effect = lambda k=None: (
            {"Keywords": keywords} if k is None else (keywords if k == "Keywords" else None)
        )
        return clip

    def setUp(self):
        resolve_api._folder_cache = None
        resolve_api._last_suggestions = None

    def tearDown(self):
        resolve_api._folder_cache = None
        resolve_api._last_suggestions = None

    def test_cache_hit_same_folder_name(self):
        clip = self._make_clip("c1")
        folder = MagicMock()
        folder.GetName.return_value = "Master"
        resolve_api._folder_cache = ("Master", [clip], {}, {}, {})
        # Should return from cache without calling GetClipList
        sorted_clips, _, _, _ = resolve_api._get_folder_cache(folder)
        folder.GetClipList.assert_not_called()

    def test_cache_miss_different_name_rebuilds(self):
        clip = self._make_clip("c1")
        folder = MagicMock()
        folder.GetName.return_value = "NewFolder"
        folder.GetClipList.return_value = [clip]
        resolve_api._folder_cache = ("OldFolder", [], {}, {}, {})
        sorted_clips, _, _, _ = resolve_api._get_folder_cache(folder)
        folder.GetClipList.assert_called_once()
        self.assertEqual(len(sorted_clips), 1)

    def test_raw_none_triggers_getcliplist(self):
        clip = self._make_clip("c2")
        folder = MagicMock()
        folder.GetName.return_value = "FolderA"
        folder.GetClipList.return_value = [clip]
        sorted_clips, _, _, _ = resolve_api._get_folder_cache(folder, raw=None)
        folder.GetClipList.assert_called_once()
        self.assertEqual(len(sorted_clips), 1)

    def test_raw_provided_skips_getcliplist(self):
        clip = self._make_clip("c3")
        folder = MagicMock()
        folder.GetName.return_value = "FolderB"
        sorted_clips, _, _, _ = resolve_api._get_folder_cache(folder, raw=[clip])
        folder.GetClipList.assert_not_called()
        self.assertEqual(len(sorted_clips), 1)


class TestGetNeighbours(unittest.TestCase):
    """Tests for resolve_api.get_neighbours."""

    def _make_clip(self, media_id, proxy=""):
        clip = MagicMock()
        clip.GetMediaId.return_value = media_id
        clip.GetName.return_value = media_id
        return clip

    def setUp(self):
        resolve_api._folder_cache = None
        resolve_api._last_suggestions = None

    def tearDown(self):
        resolve_api._folder_cache = None
        resolve_api._last_suggestions = None

    def _set_cache(self, clips, proxy_by_id=None):
        if proxy_by_id is None:
            proxy_by_id = {c.GetMediaId(): f"/proxy/{c.GetMediaId()}.mxf" for c in clips}
        resolve_api._folder_cache = ("folder", clips, {}, {}, proxy_by_id)

    def test_cold_cache_returns_empty_strings(self):
        resolve_api._folder_cache = None
        prev, next_ = resolve_api.get_neighbours("any-id")
        self.assertEqual(prev, "")
        self.assertEqual(next_, "")

    def test_clip_not_found_returns_empty(self):
        clips = [self._make_clip("a"), self._make_clip("b")]
        self._set_cache(clips)
        prev, next_ = resolve_api.get_neighbours("nonexistent")
        self.assertEqual(prev, "")
        self.assertEqual(next_, "")

    def test_first_clip_no_prev(self):
        clips = [self._make_clip("first"), self._make_clip("second"), self._make_clip("third")]
        proxy_by_id = {"first": "/p/first.mxf", "second": "/p/second.mxf", "third": "/p/third.mxf"}
        self._set_cache(clips, proxy_by_id)
        prev, next_ = resolve_api.get_neighbours("first")
        self.assertEqual(prev, "")
        self.assertEqual(next_, "/p/second.mxf")

    def test_last_clip_no_next(self):
        clips = [self._make_clip("first"), self._make_clip("second"), self._make_clip("last")]
        proxy_by_id = {"first": "/p/first.mxf", "second": "/p/second.mxf", "last": "/p/last.mxf"}
        self._set_cache(clips, proxy_by_id)
        prev, next_ = resolve_api.get_neighbours("last")
        self.assertEqual(prev, "/p/second.mxf")
        self.assertEqual(next_, "")

    def test_middle_clip_has_both(self):
        clips = [self._make_clip("a"), self._make_clip("b"), self._make_clip("c")]
        proxy_by_id = {"a": "/p/a.mxf", "b": "/p/b.mxf", "c": "/p/c.mxf"}
        self._set_cache(clips, proxy_by_id)
        prev, next_ = resolve_api.get_neighbours("b")
        self.assertEqual(prev, "/p/a.mxf")
        self.assertEqual(next_, "/p/c.mxf")


class TestSuggestKeywordsFromCache(unittest.TestCase):
    """Tests for resolve_api.suggest_keywords_from_cache."""

    def _make_clip(self, media_id):
        clip = MagicMock()
        clip.GetMediaId.return_value = media_id
        clip.GetName.return_value = media_id
        return clip

    def setUp(self):
        resolve_api._folder_cache = None
        resolve_api._last_suggestions = None

    def tearDown(self):
        resolve_api._folder_cache = None
        resolve_api._last_suggestions = None

    def test_cold_cache_returns_none(self):
        result = resolve_api.suggest_keywords_from_cache("any-id", [])
        self.assertIsNone(result)

    def test_clip_not_in_cache_returns_none(self):
        clip = self._make_clip("some-other-id")
        from datetime import datetime
        resolve_api._folder_cache = (
            "folder", [clip],
            {"some-other-id": datetime(2024, 1, 1)},
            {"some-other-id": ["kw"]},
            {},
        )
        result = resolve_api.suggest_keywords_from_cache("nonexistent", [])
        self.assertIsNone(result)

    def test_excludes_current_clip_keywords(self):
        from datetime import datetime
        c1 = self._make_clip("neighbor")
        cur = self._make_clip("cur")
        resolve_api._folder_cache = (
            "folder",
            [c1, cur],
            {"neighbor": datetime(2024, 1, 1), "cur": datetime(2024, 1, 2)},
            {"neighbor": ["shared", "unique"], "cur": ["shared"]},
            {},
        )
        result = resolve_api.suggest_keywords_from_cache("cur", ["shared"])
        self.assertIsNotNone(result)
        self.assertNotIn("shared", [k.lower() for k in result])
        self.assertIn("unique", result)

    def test_updates_last_suggestions(self):
        from datetime import datetime
        c1 = self._make_clip("n1")
        cur = self._make_clip("cur")
        resolve_api._folder_cache = (
            "folder",
            [c1, cur],
            {"n1": datetime(2024, 1, 1), "cur": datetime(2024, 1, 2)},
            {"n1": ["alpha"], "cur": []},
            {},
        )
        result = resolve_api.suggest_keywords_from_cache("cur", [])
        self.assertIsNotNone(result)
        self.assertIsNotNone(resolve_api._last_suggestions)
        self.assertEqual(resolve_api._last_suggestions[0], "cur")

    def test_correct_scoring_closer_neighbor_ranks_higher(self):
        from datetime import datetime
        # n_far at index 0, n_close at index 1, cur at index 2
        n_far = self._make_clip("n_far")
        n_close = self._make_clip("n_close")
        cur = self._make_clip("cur")
        resolve_api._folder_cache = (
            "folder",
            [n_far, n_close, cur],
            {
                "n_far": datetime(2024, 1, 1),
                "n_close": datetime(2024, 1, 2),
                "cur": datetime(2024, 1, 3),
            },
            {
                "n_far": ["far_kw"],
                "n_close": ["close_kw"],
                "cur": [],
            },
            {},
        )
        result = resolve_api.suggest_keywords_from_cache("cur", [])
        # close_kw has distance 1 (score 1.0), far_kw has distance 2 (score 0.5)
        self.assertIsNotNone(result)
        self.assertIn("close_kw", result)
        self.assertIn("far_kw", result)
        self.assertEqual(result[0], "close_kw")
        self.assertEqual(result[1], "far_kw")


class TestProbeDuration(unittest.TestCase):
    """Tests for resolve_api._probe_duration."""

    def test_success_returns_float(self):
        with patch("resolve_api.subprocess") as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=0, stdout=b"12.345\n")
            result = resolve_api._probe_duration("/fake/file.mov", "/usr/bin/ffprobe")
        self.assertAlmostEqual(result, 12.345)

    def test_nonzero_returncode_returns_zero(self):
        with patch("resolve_api.subprocess") as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=1, stdout=b"")
            result = resolve_api._probe_duration("/fake/file.mov", "/usr/bin/ffprobe")
        self.assertEqual(result, 0.0)

    def test_float_parse_error_returns_zero(self):
        with patch("resolve_api.subprocess") as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=0, stdout=b"N/A\n")
            result = resolve_api._probe_duration("/fake/file.mov", "/usr/bin/ffprobe")
        self.assertEqual(result, 0.0)

    def test_exception_returns_zero(self):
        with patch("resolve_api.subprocess") as mock_sub:
            mock_sub.run.side_effect = Exception("timeout")
            result = resolve_api._probe_duration("/fake/file.mov", "/usr/bin/ffprobe")
        self.assertEqual(result, 0.0)


class TestExtractFrame(unittest.TestCase):
    """Tests for resolve_api._extract_frame."""

    def test_success_returns_bytes(self):
        with patch("resolve_api.subprocess") as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=0, stdout=b"PNGDATA")
            result = resolve_api._extract_frame("/fake/file.mov", "/usr/bin/ffmpeg", 5.0)
        self.assertEqual(result, b"PNGDATA")

    def test_nonzero_returncode_returns_none(self):
        with patch("resolve_api.subprocess") as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=1, stdout=b"")
            result = resolve_api._extract_frame("/fake/file.mov", "/usr/bin/ffmpeg", 5.0)
        self.assertIsNone(result)

    def test_empty_stdout_returns_none(self):
        with patch("resolve_api.subprocess") as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=0, stdout=b"")
            result = resolve_api._extract_frame("/fake/file.mov", "/usr/bin/ffmpeg", 5.0)
        self.assertIsNone(result)

    def test_exception_returns_none(self):
        with patch("resolve_api.subprocess") as mock_sub:
            mock_sub.run.side_effect = Exception("process crashed")
            result = resolve_api._extract_frame("/fake/file.mov", "/usr/bin/ffmpeg", 5.0)
        self.assertIsNone(result)


class TestExtractFramesSinglePass(unittest.TestCase):
    """Tests for resolve_api._extract_frames_single_pass."""

    def test_empty_seeks_returns_empty_list(self):
        result = resolve_api._extract_frames_single_pass("/fake/file.mov", "/usr/bin/ffmpeg", [])
        self.assertEqual(result, [])

    def test_none_results_filtered_out(self):
        with patch("resolve_api._extract_frame") as mock_ef:
            mock_ef.side_effect = [b"F1", None, b"F3"]
            result = resolve_api._extract_frames_single_pass(
                "/fake/file.mov", "/usr/bin/ffmpeg", [1.0, 2.0, 3.0]
            )
        # None filtered
        self.assertIn(b"F1", result)
        self.assertIn(b"F3", result)
        self.assertNotIn(None, result)

    def test_all_succeed(self):
        with patch("resolve_api._extract_frame", return_value=b"OK"):
            result = resolve_api._extract_frames_single_pass(
                "/fake/file.mov", "/usr/bin/ffmpeg", [1.0, 2.0]
            )
        self.assertEqual(len(result), 2)


class TestFramesFromFilePathTimed(unittest.TestCase):
    """Tests for resolve_api.frames_from_file_path_timed."""

    def test_returns_tuple_of_frames_probe_ms_extract_ms(self):
        with patch("resolve_api._ffmpeg_path", return_value="/usr/bin/ffmpeg"), \
             patch("resolve_api._ffprobe_path", return_value="/usr/bin/ffprobe"), \
             patch("resolve_api.subprocess") as mock_sub:
            mock_sub.run.side_effect = [
                MagicMock(returncode=0, stdout=b"10.0"),
            ] + [MagicMock(returncode=0, stdout=b"PNG")] * 10
            frames, probe_ms, extract_ms = resolve_api.frames_from_file_path_timed("/fake/clip.mov")
        self.assertIsInstance(frames, list)
        self.assertIsInstance(probe_ms, float)
        self.assertIsInstance(extract_ms, float)
        self.assertEqual(len(frames), 5)

    def test_ffmpeg_not_found_returns_empty_and_zeros(self):
        with patch("resolve_api._ffmpeg_path", side_effect=FileNotFoundError):
            frames, probe_ms, extract_ms = resolve_api.frames_from_file_path_timed("/fake/clip.mov")
        self.assertEqual(frames, [])
        self.assertEqual(probe_ms, 0.0)
        self.assertEqual(extract_ms, 0.0)


class TestGetSelectedMediaPoolItem(unittest.TestCase):
    """Tests for resolve_api.get_selected_media_pool_item."""

    def test_returns_none_when_project_manager_is_none(self):
        resolve = MagicMock()
        resolve.GetProjectManager.return_value = None
        result = resolve_api.get_selected_media_pool_item(resolve)
        self.assertIsNone(result)

    def test_timeline_path_returns_media_pool_item(self):
        resolve = MagicMock()
        pm = resolve.GetProjectManager.return_value
        project = pm.GetCurrentProject.return_value
        timeline = project.GetCurrentTimeline.return_value
        tl_item = timeline.GetCurrentVideoItem.return_value
        mpi = MagicMock()
        tl_item.GetMediaPoolItem.return_value = mpi
        result = resolve_api.get_selected_media_pool_item(resolve)
        self.assertEqual(result, mpi)

    def test_media_pool_fallback_when_no_timeline(self):
        resolve = MagicMock()
        pm = resolve.GetProjectManager.return_value
        project = pm.GetCurrentProject.return_value
        project.GetCurrentTimeline.return_value = None
        media_pool = project.GetMediaPool.return_value
        selected_clip = MagicMock()
        media_pool.GetSelectedClips.return_value = {"1": selected_clip}
        result = resolve_api.get_selected_media_pool_item(resolve)
        self.assertEqual(result, selected_clip)

    def test_media_pool_fallback_when_timeline_item_has_no_media_pool_item(self):
        resolve = MagicMock()
        pm = resolve.GetProjectManager.return_value
        project = pm.GetCurrentProject.return_value
        timeline = project.GetCurrentTimeline.return_value
        tl_item = timeline.GetCurrentVideoItem.return_value
        tl_item.GetMediaPoolItem.return_value = None
        media_pool = project.GetMediaPool.return_value
        selected_clip = MagicMock()
        media_pool.GetSelectedClips.return_value = {"1": selected_clip}
        result = resolve_api.get_selected_media_pool_item(resolve)
        self.assertEqual(result, selected_clip)

    def test_nothing_selected_returns_none(self):
        resolve = MagicMock()
        pm = resolve.GetProjectManager.return_value
        project = pm.GetCurrentProject.return_value
        project.GetCurrentTimeline.return_value = None
        media_pool = project.GetMediaPool.return_value
        media_pool.GetSelectedClips.return_value = {}
        result = resolve_api.get_selected_media_pool_item(resolve)
        self.assertIsNone(result)

    def test_returns_none_when_media_pool_is_none(self):
        resolve = MagicMock()
        pm = resolve.GetProjectManager.return_value
        project = pm.GetCurrentProject.return_value
        project.GetCurrentTimeline.return_value = None
        project.GetMediaPool.return_value = None
        result = resolve_api.get_selected_media_pool_item(resolve)
        self.assertIsNone(result)


class TestFindFolderForClip(unittest.TestCase):
    """Tests for resolve_api._find_folder_for_clip."""

    def _make_clip(self, media_id):
        clip = MagicMock()
        clip.GetMediaId.return_value = media_id
        return clip

    def _make_folder(self, clips, subfolders=None):
        folder = MagicMock()
        folder.GetClipList.return_value = clips
        folder.GetSubFolderList.return_value = subfolders or []
        return folder

    def test_found_in_root(self):
        clip = self._make_clip("target")
        folder = self._make_folder([clip])
        result = resolve_api._find_folder_for_clip(folder, "target")
        self.assertEqual(result, folder)

    def test_found_in_subfolder(self):
        clip = self._make_clip("target")
        subfolder = self._make_folder([clip])
        root = self._make_folder([], subfolders=[subfolder])
        result = resolve_api._find_folder_for_clip(root, "target")
        self.assertEqual(result, subfolder)

    def test_not_found_returns_none(self):
        clip = self._make_clip("other")
        folder = self._make_folder([clip])
        result = resolve_api._find_folder_for_clip(folder, "nonexistent")
        self.assertIsNone(result)

    def test_found_in_nested_subfolder(self):
        clip = self._make_clip("deep")
        deep_sub = self._make_folder([clip])
        mid_sub = self._make_folder([], subfolders=[deep_sub])
        root = self._make_folder([], subfolders=[mid_sub])
        result = resolve_api._find_folder_for_clip(root, "deep")
        self.assertEqual(result, deep_sub)


class TestResolveFolder(unittest.TestCase):
    """Tests for resolve_api._resolve_folder."""

    def _make_clip(self, media_id):
        clip = MagicMock()
        clip.GetMediaId.return_value = media_id
        return clip

    def test_getcurrentfolder_success_clip_present(self):
        clip = self._make_clip("cur")
        folder = MagicMock()
        folder.GetClipList.return_value = [clip]
        media_pool = MagicMock()
        media_pool.GetCurrentFolder.return_value = folder
        current_item = self._make_clip("cur")
        result_folder, result_clips = resolve_api._resolve_folder(media_pool, current_item)
        self.assertEqual(result_folder, folder)
        self.assertIsNotNone(result_clips)

    def test_getcurrentfolder_stale_falls_back_to_walk(self):
        # GetCurrentFolder returns folder, but clip not in that folder
        other_clip = self._make_clip("other")
        stale_folder = MagicMock()
        stale_folder.GetClipList.return_value = [other_clip]

        target_clip = self._make_clip("target")
        correct_folder = MagicMock()
        correct_folder.GetClipList.return_value = [target_clip]
        correct_folder.GetSubFolderList.return_value = []

        root = MagicMock()
        root.GetClipList.return_value = []
        root.GetSubFolderList.return_value = [correct_folder]

        media_pool = MagicMock()
        media_pool.GetCurrentFolder.return_value = stale_folder
        media_pool.GetRootFolder.return_value = root

        current_item = self._make_clip("target")
        result_folder, result_clips = resolve_api._resolve_folder(media_pool, current_item)
        self.assertEqual(result_folder, correct_folder)
        # raw is None in the fallback path
        self.assertIsNone(result_clips)

    def test_getcurrentfolder_none_falls_back_to_walk(self):
        target_clip = self._make_clip("target")
        folder = MagicMock()
        folder.GetClipList.return_value = [target_clip]
        folder.GetSubFolderList.return_value = []

        root = MagicMock()
        root.GetClipList.return_value = []
        root.GetSubFolderList.return_value = [folder]

        media_pool = MagicMock()
        media_pool.GetCurrentFolder.return_value = None
        media_pool.GetRootFolder.return_value = root

        current_item = self._make_clip("target")
        result_folder, result_clips = resolve_api._resolve_folder(media_pool, current_item)
        self.assertEqual(result_folder, folder)

    def test_root_none_returns_none_none(self):
        media_pool = MagicMock()
        media_pool.GetCurrentFolder.return_value = None
        media_pool.GetRootFolder.return_value = None
        current_item = self._make_clip("target")
        result_folder, result_clips = resolve_api._resolve_folder(media_pool, current_item)
        self.assertIsNone(result_folder)
        self.assertIsNone(result_clips)


if __name__ == "__main__":
    unittest.main()
