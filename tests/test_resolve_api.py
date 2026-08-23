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
        item = self._make_item({"Keywords": "a,b"})
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
        item.SetMetadata.assert_called_once_with("Keywords", "a,b")
        item.SetClipProperty.assert_called_once_with("Keywords", "a,b")

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

    def test_strips_leading_and_trailing_spaces(self):
        item = MagicMock()
        item.SetMetadata.return_value = True
        resolve_api.set_keywords(item, [" Italy", "Rome ", "  Florence  "])
        item.SetMetadata.assert_called_once_with("Keywords", "Florence,Italy,Rome")

    def test_deduplicates_after_stripping(self):
        item = MagicMock()
        item.SetMetadata.return_value = True
        resolve_api.set_keywords(item, ["Italy", " Italy", "Rome", "Italy"])
        item.SetMetadata.assert_called_once_with("Keywords", "Italy,Rome")

    def test_drops_whitespace_only_tokens(self):
        item = MagicMock()
        item.SetMetadata.return_value = True
        resolve_api.set_keywords(item, ["Italy", "   ", "Rome"])
        item.SetMetadata.assert_called_once_with("Keywords", "Italy,Rome")


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


class TestNormaliseAiKeywordExtended(unittest.TestCase):
    """Additional edge-case tests for resolve_api._normalise_ai_keyword."""

    def test_none_existing_keywords_treated_as_empty(self):
        # Should not raise; without any known proper nouns, result is lowercase.
        self.assertEqual(
            resolve_api._normalise_ai_keyword("Rolling Hills", None),
            "rolling hills",
        )

    def test_single_word_proper_noun_restored(self):
        # "Portugal" is a single-word proper noun in existing_keywords.
        self.assertEqual(
            resolve_api._normalise_ai_keyword("portugal coastline", ["Portugal"]),
            "Portugal coastline",
        )

    def test_generic_word_stays_lowercase_even_if_capitalized_in_keyword(self):
        # "New" is in _GENERIC — should NOT be restored to capital from multi-word kw.
        # "New York City" contributes as a phrase, not individual words.
        result = resolve_api._normalise_ai_keyword("new market square", ["New York City"])
        # "new" is not restored individually; phrase "new york city" not present.
        self.assertEqual(result, "new market square")

    def test_phrase_match_restores_whole_phrase(self):
        kws = ["Parc Monceau"]
        result = resolve_api._normalise_ai_keyword("parc monceau in paris", kws)
        self.assertEqual(result, "Parc Monceau in paris")

    def test_phrase_takes_priority_over_word(self):
        # "New" would not be in known_words (it's generic), but "New York" is a phrase.
        kws = ["New York"]
        result = resolve_api._normalise_ai_keyword("new york street", kws)
        self.assertEqual(result, "New York street")

    def test_lowercase_existing_keyword_not_used_for_restoration(self):
        # "sunset" starts with lowercase — not a proper noun, should not be registered.
        result = resolve_api._normalise_ai_keyword("Sunset Beach", ["sunset"])
        self.assertEqual(result, "sunset beach")

    def test_empty_existing_keywords_list(self):
        result = resolve_api._normalise_ai_keyword("Eiffel Tower", [])
        self.assertEqual(result, "eiffel tower")

    def test_whitespace_only_input(self):
        result = resolve_api._normalise_ai_keyword("   ")
        self.assertEqual(result, "   ")

    def test_multiple_known_words_all_restored(self):
        kws = ["Alice", "Bob", "Rome"]
        result = resolve_api._normalise_ai_keyword("alice and bob in rome", kws)
        self.assertEqual(result, "Alice and Bob in Rome")

    def test_longer_phrase_matches_before_shorter(self):
        # "New York City" (3 words) should beat "New York" (2 words) if both present.
        kws = ["New York City", "New York"]
        result = resolve_api._normalise_ai_keyword("new york city skyline", kws)
        self.assertEqual(result, "New York City skyline")


class TestNormalizeKeywordsMixedSeparators(unittest.TestCase):
    """Edge cases for resolve_api._normalize_keywords not covered in existing tests."""

    def test_list_with_whitespace_items_stripped(self):
        result = resolve_api._normalize_keywords(["  sunset  ", " beach ", ""])
        self.assertEqual(result, ["sunset", "beach"])

    def test_string_with_only_commas_returns_empty(self):
        result = resolve_api._normalize_keywords(",,,")
        self.assertEqual(result, [])

    def test_semicolon_separated_with_spaces(self):
        result = resolve_api._normalize_keywords("alpha; beta; gamma")
        self.assertEqual(result, ["alpha", "beta", "gamma"])

    def test_single_item_list_returned_as_single(self):
        result = resolve_api._normalize_keywords(["only"])
        self.assertEqual(result, ["only"])

    def test_list_of_empty_strings_returns_empty(self):
        result = resolve_api._normalize_keywords(["", "  ", ""])
        self.assertEqual(result, [])


class TestFindClipByNameAndDir(unittest.TestCase):
    """Tests for resolve_api._find_clip_by_name_and_dir."""

    def _make_clip(self, name, file_path):
        clip = MagicMock()
        clip.GetName.return_value = name
        clip.GetClipProperty.side_effect = lambda k: file_path if k == "File Path" else ""
        return clip

    def _make_folder(self, clips, subfolders=None):
        folder = MagicMock()
        folder.GetClipList.return_value = clips
        folder.GetSubFolderList.return_value = subfolders or []
        return folder

    def test_found_in_root_exact_match(self):
        clip = self._make_clip("C0040.MP4", "/Volumes/Drive/Video/C0040.MP4")
        folder = self._make_folder([clip])
        result_folder, result_clip = resolve_api._find_clip_by_name_and_dir(
            folder, "C0040.MP4", "/Volumes/Drive/Video"
        )
        self.assertIs(result_clip, clip)
        self.assertIs(result_folder, folder)

    def test_name_matches_but_dir_differs_not_found(self):
        clip = self._make_clip("C0040.MP4", "/Volumes/DriveA/Video/C0040.MP4")
        folder = self._make_folder([clip])
        result_folder, result_clip = resolve_api._find_clip_by_name_and_dir(
            folder, "C0040.MP4", "/Volumes/DriveB/Video"
        )
        self.assertIsNone(result_clip)
        self.assertIsNone(result_folder)

    def test_found_in_subfolder(self):
        clip = self._make_clip("clip.mp4", "/vol/sub/clip.mp4")
        subfolder = self._make_folder([clip])
        root = self._make_folder([], subfolders=[subfolder])
        result_folder, result_clip = resolve_api._find_clip_by_name_and_dir(
            root, "clip.mp4", "/vol/sub"
        )
        self.assertIs(result_clip, clip)
        self.assertIs(result_folder, subfolder)

    def test_trailing_slash_normalised(self):
        clip = self._make_clip("clip.mp4", "/vol/dir/clip.mp4")
        folder = self._make_folder([clip])
        # clip_dir with trailing slash should still match
        result_folder, result_clip = resolve_api._find_clip_by_name_and_dir(
            folder, "clip.mp4", "/vol/dir/"
        )
        self.assertIs(result_clip, clip)

    def test_not_found_returns_none_none(self):
        clip = self._make_clip("other.mp4", "/vol/dir/other.mp4")
        folder = self._make_folder([clip])
        result_folder, result_clip = resolve_api._find_clip_by_name_and_dir(
            folder, "missing.mp4", "/vol/dir"
        )
        self.assertIsNone(result_clip)
        self.assertIsNone(result_folder)

    def test_duplicate_filename_different_dirs_returns_correct_one(self):
        clip_a = self._make_clip("C0040.MP4", "/Volumes/DriveA/Video/C0040.MP4")
        clip_b = self._make_clip("C0040.MP4", "/Volumes/DriveB/Video/C0040.MP4")
        sub_a = self._make_folder([clip_a])
        sub_b = self._make_folder([clip_b])
        root = self._make_folder([], subfolders=[sub_a, sub_b])
        _, result_clip = resolve_api._find_clip_by_name_and_dir(
            root, "C0040.MP4", "/Volumes/DriveB/Video"
        )
        self.assertIs(result_clip, clip_b)


class TestSelectClipInResolve(unittest.TestCase):
    """Tests for resolve_api.select_clip_in_resolve."""

    def _make_clip(self, name, file_path):
        clip = MagicMock()
        clip.GetName.return_value = name
        clip.GetClipProperty.side_effect = lambda k: file_path if k == "File Path" else ""
        return clip

    def _make_resolve(self, root_folder):
        resolve = MagicMock()
        project = resolve.GetProjectManager.return_value.GetCurrentProject.return_value
        media_pool = project.GetMediaPool.return_value
        media_pool.GetRootFolder.return_value = root_folder
        media_pool.SetCurrentFolder.return_value = True
        media_pool.SetSelectedClip.return_value = True
        return resolve, media_pool

    def test_success_returns_ok_true(self):
        clip = self._make_clip("clip.mp4", "/vol/dir/clip.mp4")
        folder = MagicMock()
        folder.GetClipList.return_value = [clip]
        folder.GetSubFolderList.return_value = []
        folder.GetName.return_value = "dir"
        resolve, media_pool = self._make_resolve(folder)
        result = resolve_api.select_clip_in_resolve(resolve, "clip.mp4", "/vol/dir")
        self.assertTrue(result["ok"])

    def test_clip_not_found_returns_ok_false(self):
        folder = MagicMock()
        folder.GetClipList.return_value = []
        folder.GetSubFolderList.return_value = []
        resolve, _ = self._make_resolve(folder)
        result = resolve_api.select_clip_in_resolve(resolve, "missing.mp4", "/vol/dir")
        self.assertFalse(result["ok"])
        self.assertIn("clip not found", result["error"])

    def test_no_project_manager_returns_ok_false(self):
        resolve = MagicMock()
        resolve.GetProjectManager.return_value = None
        result = resolve_api.select_clip_in_resolve(resolve, "clip.mp4", "/vol/dir")
        self.assertFalse(result["ok"])

    def test_no_current_project_returns_ok_false(self):
        resolve = MagicMock()
        resolve.GetProjectManager.return_value.GetCurrentProject.return_value = None
        result = resolve_api.select_clip_in_resolve(resolve, "clip.mp4", "/vol/dir")
        self.assertFalse(result["ok"])

    def test_no_media_pool_returns_ok_false(self):
        resolve = MagicMock()
        project = resolve.GetProjectManager.return_value.GetCurrentProject.return_value
        project.GetMediaPool.return_value = None
        result = resolve_api.select_clip_in_resolve(resolve, "clip.mp4", "/vol/dir")
        self.assertFalse(result["ok"])

    def test_no_root_folder_returns_ok_false(self):
        resolve = MagicMock()
        project = resolve.GetProjectManager.return_value.GetCurrentProject.return_value
        media_pool = project.GetMediaPool.return_value
        media_pool.GetRootFolder.return_value = None
        result = resolve_api.select_clip_in_resolve(resolve, "clip.mp4", "/vol/dir")
        self.assertFalse(result["ok"])

    def test_exception_returns_ok_false_with_error(self):
        resolve = MagicMock()
        resolve.GetProjectManager.side_effect = Exception("IPC crash")
        result = resolve_api.select_clip_in_resolve(resolve, "clip.mp4", "/vol/dir")
        self.assertFalse(result["ok"])
        self.assertIn("IPC crash", result["error"])

    def test_sets_current_folder_and_selected_clip(self):
        clip = self._make_clip("clip.mp4", "/vol/dir/clip.mp4")
        folder = MagicMock()
        folder.GetClipList.return_value = [clip]
        folder.GetSubFolderList.return_value = []
        folder.GetName.return_value = "dir"
        resolve, media_pool = self._make_resolve(folder)
        resolve_api.select_clip_in_resolve(resolve, "clip.mp4", "/vol/dir")
        media_pool.SetCurrentFolder.assert_called_once_with(folder)
        media_pool.SetSelectedClip.assert_called_once_with(clip)


class TestCollectProxyPaths(unittest.TestCase):
    """Tests for resolve_api._collect_proxy_paths_recursive and collect_proxy_paths."""

    def _make_clip(self, name, file_path, proxy_path=""):
        clip = MagicMock()
        clip.GetName.return_value = name
        clip.GetClipProperty.side_effect = lambda k: (
            file_path if k == "File Path" else (proxy_path if k == "Proxy Media Path" else "")
        )
        return clip

    def _make_folder(self, clips, subfolders=None):
        folder = MagicMock()
        folder.GetClipList.return_value = clips
        folder.GetSubFolderList.return_value = subfolders or []
        return folder

    def test_clip_with_proxy_included(self):
        clip = self._make_clip("clip.mp4", "/vol/dir/clip.mp4", "/proxy/clip.mp4")
        folder = self._make_folder([clip])
        result = {}
        resolve_api._collect_proxy_paths_recursive(folder, result)
        self.assertIn(("clip.mp4", "/vol/dir"), result)
        self.assertEqual(result[("clip.mp4", "/vol/dir")], "/proxy/clip.mp4")

    def test_clip_without_proxy_excluded(self):
        clip = self._make_clip("clip.mp4", "/vol/dir/clip.mp4", "")
        folder = self._make_folder([clip])
        result = {}
        resolve_api._collect_proxy_paths_recursive(folder, result)
        self.assertEqual(result, {})

    def test_duplicate_filenames_different_dirs_keyed_separately(self):
        clip_a = self._make_clip("C0040.MP4", "/DriveA/Video/C0040.MP4", "/proxy/A/C0040.MP4")
        clip_b = self._make_clip("C0040.MP4", "/DriveB/Video/C0040.MP4", "/proxy/B/C0040.MP4")
        folder = self._make_folder([clip_a, clip_b])
        result = {}
        resolve_api._collect_proxy_paths_recursive(folder, result)
        self.assertEqual(result[("C0040.MP4", "/DriveA/Video")], "/proxy/A/C0040.MP4")
        self.assertEqual(result[("C0040.MP4", "/DriveB/Video")], "/proxy/B/C0040.MP4")

    def test_collects_from_subfolders_recursively(self):
        clip = self._make_clip("sub.mp4", "/vol/sub/sub.mp4", "/proxy/sub.mp4")
        subfolder = self._make_folder([clip])
        root = self._make_folder([], subfolders=[subfolder])
        result = {}
        resolve_api._collect_proxy_paths_recursive(root, result)
        self.assertIn(("sub.mp4", "/vol/sub"), result)

    def test_collect_proxy_paths_returns_empty_on_no_project(self):
        resolve = MagicMock()
        resolve.GetProjectManager.return_value.GetCurrentProject.return_value = None
        result = resolve_api.collect_proxy_paths(resolve)
        self.assertEqual(result, {})

    def test_collect_proxy_paths_returns_empty_on_exception(self):
        resolve = MagicMock()
        resolve.GetProjectManager.side_effect = Exception("crash")
        result = resolve_api.collect_proxy_paths(resolve)
        self.assertEqual(result, {})

    def test_collect_proxy_paths_returns_dict_with_tuple_keys(self):
        clip = self._make_clip("a.mp4", "/vol/dir/a.mp4", "/proxy/a.mp4")
        folder = self._make_folder([clip])
        resolve = MagicMock()
        project = resolve.GetProjectManager.return_value.GetCurrentProject.return_value
        media_pool = project.GetMediaPool.return_value
        media_pool.GetRootFolder.return_value = folder
        result = resolve_api.collect_proxy_paths(resolve)
        self.assertIsInstance(result, dict)
        key = list(result.keys())[0]
        self.assertIsInstance(key, tuple)
        self.assertEqual(len(key), 2)


class TestGetProjectName(unittest.TestCase):
    """Tests for resolve_api.get_project_name."""

    def test_returns_project_name(self):
        resolve = MagicMock()
        resolve.GetProjectManager.return_value.GetCurrentProject.return_value.GetName.return_value = "My Project"
        self.assertEqual(resolve_api.get_project_name(resolve), "My Project")

    def test_returns_empty_string_when_no_project_manager(self):
        resolve = MagicMock()
        resolve.GetProjectManager.return_value = None
        self.assertEqual(resolve_api.get_project_name(resolve), "")

    def test_returns_empty_string_when_no_project(self):
        resolve = MagicMock()
        resolve.GetProjectManager.return_value.GetCurrentProject.return_value = None
        self.assertEqual(resolve_api.get_project_name(resolve), "")

    def test_returns_empty_string_on_exception(self):
        resolve = MagicMock()
        resolve.GetProjectManager.side_effect = Exception("crash")
        self.assertEqual(resolve_api.get_project_name(resolve), "")

    def test_returns_empty_string_when_getname_returns_none(self):
        resolve = MagicMock()
        resolve.GetProjectManager.return_value.GetCurrentProject.return_value.GetName.return_value = None
        self.assertEqual(resolve_api.get_project_name(resolve), "")


class TestNavigateClip(unittest.TestCase):
    """Tests for resolve_api.navigate_clip."""

    def _make_clip(self, media_id):
        clip = MagicMock()
        clip.GetMediaId.return_value = media_id
        clip.GetName.return_value = media_id
        clip.GetClipProperty.return_value = "01/01/2024 12:00:00"
        clip.GetMetadata.return_value = {}
        return clip

    def setUp(self):
        resolve_api._folder_cache = None
        resolve_api._last_suggestions = None

    def tearDown(self):
        resolve_api._folder_cache = None
        resolve_api._last_suggestions = None

    def _make_resolve_with_clips(self, clips, current_id):
        resolve = MagicMock()
        current_clip = next(c for c in clips if c.GetMediaId() == current_id)

        pm = resolve.GetProjectManager.return_value
        project = pm.GetCurrentProject.return_value
        media_pool = project.GetMediaPool.return_value
        media_pool.SetSelectedClip.return_value = True

        # Pre-warm the folder cache with these clips so navigate uses fast path.
        resolve_api._folder_cache = ("folder", clips, {}, {}, {})

        # get_selected_media_pool_item path: no timeline, selected clip via media pool
        project.GetCurrentTimeline.return_value = None
        media_pool.GetSelectedClips.return_value = {"1": current_clip}

        return resolve, media_pool

    def test_next_returns_following_clip(self):
        clips = [self._make_clip("a"), self._make_clip("b"), self._make_clip("c")]
        resolve, media_pool = self._make_resolve_with_clips(clips, "a")
        item, _ = resolve_api.navigate_clip(resolve, +1)
        self.assertEqual(item.GetMediaId(), "b")

    def test_prev_returns_preceding_clip(self):
        clips = [self._make_clip("a"), self._make_clip("b"), self._make_clip("c")]
        resolve, media_pool = self._make_resolve_with_clips(clips, "c")
        item, _ = resolve_api.navigate_clip(resolve, -1)
        self.assertEqual(item.GetMediaId(), "b")

    def test_at_first_clip_prev_returns_none(self):
        clips = [self._make_clip("a"), self._make_clip("b")]
        resolve, _ = self._make_resolve_with_clips(clips, "a")
        item, _ = resolve_api.navigate_clip(resolve, -1)
        self.assertIsNone(item)

    def test_at_last_clip_next_returns_none(self):
        clips = [self._make_clip("a"), self._make_clip("b")]
        resolve, _ = self._make_resolve_with_clips(clips, "b")
        item, _ = resolve_api.navigate_clip(resolve, +1)
        self.assertIsNone(item)

    def test_returns_none_when_no_project_manager(self):
        resolve = MagicMock()
        resolve.GetProjectManager.return_value = None
        item, _ = resolve_api.navigate_clip(resolve, +1)
        self.assertIsNone(item)

    def test_returns_none_when_no_current_item(self):
        resolve = MagicMock()
        pm = resolve.GetProjectManager.return_value
        project = pm.GetCurrentProject.return_value
        media_pool = project.GetMediaPool.return_value
        project.GetCurrentTimeline.return_value = None
        media_pool.GetSelectedClips.return_value = {}
        resolve_api._folder_cache = None
        item, _ = resolve_api.navigate_clip(resolve, +1)
        self.assertIsNone(item)

    def test_calls_set_selected_clip_on_navigation(self):
        clips = [self._make_clip("a"), self._make_clip("b")]
        resolve, media_pool = self._make_resolve_with_clips(clips, "a")
        resolve_api.navigate_clip(resolve, +1)
        media_pool.SetSelectedClip.assert_called_once_with(clips[1])

    def test_timing_dict_returned(self):
        clips = [self._make_clip("a"), self._make_clip("b")]
        resolve, _ = self._make_resolve_with_clips(clips, "a")
        _, timing = resolve_api.navigate_clip(resolve, +1)
        self.assertIsInstance(timing, dict)


class TestCollectTimelineMedia(unittest.TestCase):
    """Tests for resolve_api._collect_timeline_media."""

    def _make_item(self, media_pool_item):
        item = MagicMock()
        item.GetMediaPoolItem.return_value = media_pool_item
        return item

    def _make_timeline(self, video_tracks, audio_tracks):
        """video_tracks/audio_tracks: list of lists of items, one list per track."""
        timeline = MagicMock()

        def track_count(track_type):
            return len(video_tracks) if track_type == "video" else len(audio_tracks)

        def item_list(track_type, index):
            tracks = video_tracks if track_type == "video" else audio_tracks
            return tracks[index - 1]

        timeline.GetTrackCount.side_effect = track_count
        timeline.GetItemListInTrack.side_effect = item_list
        return timeline

    def test_collects_clip_from_video_track(self):
        clip = MagicMock()
        clip.GetMediaId.return_value = "id1"
        timeline = self._make_timeline([[self._make_item(clip)]], [])
        result = resolve_api._collect_timeline_media(timeline)
        self.assertEqual(result, {"id1": clip})

    def test_dedups_same_clip_across_video_and_audio_tracks(self):
        clip = MagicMock()
        clip.GetMediaId.return_value = "id1"
        timeline = self._make_timeline([[self._make_item(clip)]], [[self._make_item(clip)]])
        result = resolve_api._collect_timeline_media(timeline)
        self.assertEqual(len(result), 1)

    def test_skips_items_with_no_media_pool_item(self):
        timeline = self._make_timeline([[self._make_item(None)]], [])
        result = resolve_api._collect_timeline_media(timeline)
        self.assertEqual(result, {})

    def test_empty_timeline_returns_empty_dict(self):
        timeline = self._make_timeline([], [])
        self.assertEqual(resolve_api._collect_timeline_media(timeline), {})

    def test_walks_multiple_tracks(self):
        clip_a, clip_b = MagicMock(), MagicMock()
        clip_a.GetMediaId.return_value = "a"
        clip_b.GetMediaId.return_value = "b"
        timeline = self._make_timeline(
            [[self._make_item(clip_a)], [self._make_item(clip_b)]], []
        )
        result = resolve_api._collect_timeline_media(timeline)
        self.assertEqual(set(result.keys()), {"a", "b"})


class _UsedTagTestMixin:
    """Shared clip-mock helper for tests around Used:... keyword tags."""

    def _make_clip(self, name, keywords, media_id=None):
        clip = MagicMock()
        clip.GetName.return_value = name
        clip.GetMediaId.return_value = media_id or name
        joined = ",".join(keywords)
        clip.GetMetadata.side_effect = lambda key=None: (
            {"Keywords": joined} if key is None else (joined if key == "Keywords" else None)
        )
        clip.GetClipProperty.return_value = ""
        return clip


class TestMajorityUsedTag(_UsedTagTestMixin, unittest.TestCase):
    """Tests for resolve_api._majority_used_tag (strict majority required)."""

    def test_returns_none_for_empty_list(self):
        self.assertIsNone(resolve_api._majority_used_tag([]))

    def test_returns_none_when_no_used_tag(self):
        clips = [self._make_clip("a", ["France"]), self._make_clip("b", ["Alex"])]
        self.assertIsNone(resolve_api._majority_used_tag(clips))

    def test_returns_tag_with_strict_majority(self):
        clips = [
            self._make_clip("a", ["Used:FruitBat"]),
            self._make_clip("b", ["Used:FruitBat"]),
            self._make_clip("c", ["Used:Other"]),
        ]
        self.assertEqual(resolve_api._majority_used_tag(clips), "Used:FruitBat")

    def test_returns_none_at_exactly_half(self):
        clips = [self._make_clip("a", ["Used:A"]), self._make_clip("b", ["Used:B"])]
        self.assertIsNone(resolve_api._majority_used_tag(clips))

    def test_case_insensitive_count_preserves_first_casing(self):
        clips = [
            self._make_clip("a", ["Used:FruitBat"]),
            self._make_clip("b", ["used:fruitbat"]),
            self._make_clip("c", ["USED:FRUITBAT"]),
        ]
        self.assertEqual(resolve_api._majority_used_tag(clips), "Used:FruitBat")


class TestSyncTimelineUsedTag(_UsedTagTestMixin, unittest.TestCase):
    """Tests for resolve_api.sync_timeline_used_tag. _collect_timeline_media is
    patched directly in each test (it has its own dedicated tests above) so
    these focus purely on the tag-diff/write logic."""

    def _make_resolve(self, all_clips, project_name="MyProject"):
        resolve = MagicMock()
        project = resolve.GetProjectManager.return_value.GetCurrentProject.return_value
        project.GetName.return_value = project_name
        root = project.GetMediaPool.return_value.GetRootFolder.return_value
        root.GetClipList.return_value = all_clips
        root.GetSubFolderList.return_value = []
        return resolve

    def test_no_current_project_raises(self):
        resolve = MagicMock()
        resolve.GetProjectManager.return_value.GetCurrentProject.return_value = None
        with self.assertRaises(RuntimeError):
            resolve_api.sync_timeline_used_tag(resolve)

    def test_no_active_timeline_raises(self):
        resolve = self._make_resolve([])
        resolve.GetProjectManager.return_value.GetCurrentProject.return_value.GetCurrentTimeline.return_value = None
        with self.assertRaises(RuntimeError):
            resolve_api.sync_timeline_used_tag(resolve)

    def test_no_media_pool_raises(self):
        resolve = MagicMock()
        resolve.GetProjectManager.return_value.GetCurrentProject.return_value.GetMediaPool.return_value = None
        with self.assertRaises(RuntimeError):
            resolve_api.sync_timeline_used_tag(resolve)

    def test_tag_with_comma_raises(self):
        resolve = self._make_resolve([])
        with patch("resolve_api._collect_timeline_media", return_value={}):
            with self.assertRaises(RuntimeError):
                resolve_api.sync_timeline_used_tag(resolve, tag="Bad,Tag")

    def test_default_tag_falls_back_to_project_name(self):
        clip = self._make_clip("a.mp4", [])
        resolve = self._make_resolve([clip], project_name="Riverside Film")
        with patch("resolve_api._collect_timeline_media", return_value={"a.mp4": clip}):
            result = resolve_api.sync_timeline_used_tag(resolve, dry_run=True)
        self.assertEqual(result["tag"], "Used:Riverside Film")

    def test_default_tag_uses_existing_majority_tag(self):
        clip1 = self._make_clip("a.mp4", ["Used:Custom"])
        clip2 = self._make_clip("b.mp4", ["Used:Custom"])
        resolve = self._make_resolve([clip1, clip2])
        with patch("resolve_api._collect_timeline_media",
                   return_value={"a.mp4": clip1, "b.mp4": clip2}):
            result = resolve_api.sync_timeline_used_tag(resolve, dry_run=True)
        self.assertEqual(result["tag"], "Used:Custom")

    def test_adds_tag_to_newly_used_clip(self):
        clip = self._make_clip("a.mp4", ["France"], media_id="id1")
        resolve = self._make_resolve([clip])
        with patch("resolve_api._collect_timeline_media", return_value={"id1": clip}):
            result = resolve_api.sync_timeline_used_tag(resolve, tag="Used:X", dry_run=False)
        self.assertEqual(result["added"], ["a.mp4"])
        written = clip.SetMetadata.call_args[0][1]
        self.assertIn("Used:X", written)
        self.assertIn("France", written)

    def test_removes_tag_from_no_longer_used_clip(self):
        clip = self._make_clip("a.mp4", ["France", "Used:X"], media_id="id1")
        resolve = self._make_resolve([clip])
        with patch("resolve_api._collect_timeline_media", return_value={}):
            result = resolve_api.sync_timeline_used_tag(resolve, tag="Used:X", dry_run=False)
        self.assertEqual(result["removed"], ["a.mp4"])
        written = clip.SetMetadata.call_args[0][1]
        self.assertNotIn("Used:X", written)
        self.assertIn("France", written)

    def test_dry_run_does_not_write(self):
        clip = self._make_clip("a.mp4", ["France"], media_id="id1")
        resolve = self._make_resolve([clip])
        with patch("resolve_api._collect_timeline_media", return_value={"id1": clip}):
            resolve_api.sync_timeline_used_tag(resolve, tag="Used:X", dry_run=True)
        clip.SetMetadata.assert_not_called()

    def test_already_tagged_and_untouched_counts(self):
        used_and_tagged = self._make_clip("a.mp4", ["Used:X"], media_id="id1")
        unused_and_untagged = self._make_clip("b.mp4", ["France"], media_id="id2")
        resolve = self._make_resolve([used_and_tagged, unused_and_untagged])
        with patch("resolve_api._collect_timeline_media", return_value={"id1": used_and_tagged}):
            result = resolve_api.sync_timeline_used_tag(resolve, tag="Used:X", dry_run=True)
        self.assertEqual(result["already_tagged"], 1)
        self.assertEqual(result["untouched"], 1)
        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [])


class TestDominantUsedTag(unittest.TestCase):
    """Tests for resolve_api._dominant_used_tag (mode, no majority threshold —
    contrast with TestMajorityUsedTag above)."""

    def test_returns_none_for_empty(self):
        self.assertIsNone(resolve_api._dominant_used_tag([]))

    def test_returns_none_when_no_used_tag(self):
        self.assertIsNone(resolve_api._dominant_used_tag([["France"], ["Alex"]]))

    def test_returns_mode_without_majority_requirement(self):
        # 2 vs 1 vs 1 — no strict majority, unlike _majority_used_tag.
        result = resolve_api._dominant_used_tag(
            [["Used:A"], ["Used:A"], ["Used:B"], ["Used:C"]]
        )
        self.assertEqual(result, "Used:A")

    def test_case_insensitive_count_preserves_first_casing(self):
        result = resolve_api._dominant_used_tag([["Used:FruitBat"], ["used:fruitbat"]])
        self.assertEqual(result, "Used:FruitBat")


class TestCollectClipLookupRecursive(unittest.TestCase):
    """Tests for resolve_api._collect_clip_lookup_recursive."""

    def _make_clip(self, name, frames):
        clip = MagicMock()
        clip.GetName.return_value = name
        clip.GetClipProperty.side_effect = lambda k: (
            str(frames) if k == "Frames" and frames is not None else ""
        )
        return clip

    def _make_folder(self, clips, subfolders=None):
        folder = MagicMock()
        folder.GetClipList.return_value = clips
        folder.GetSubFolderList.return_value = subfolders or []
        return folder

    def test_keys_by_name_and_frames(self):
        clip = self._make_clip("a.mp4", 480)
        result: dict = {}
        resolve_api._collect_clip_lookup_recursive(self._make_folder([clip]), result)
        self.assertEqual(result, {("a.mp4", 480): clip})

    def test_skips_clips_with_unparseable_frames(self):
        clip = self._make_clip("a.mp4", None)
        result: dict = {}
        resolve_api._collect_clip_lookup_recursive(self._make_folder([clip]), result)
        self.assertEqual(result, {})

    def test_recurses_into_subfolders(self):
        sub = self._make_folder([self._make_clip("b.mp4", 100)])
        root = self._make_folder([self._make_clip("a.mp4", 480)], subfolders=[sub])
        result: dict = {}
        resolve_api._collect_clip_lookup_recursive(root, result)
        self.assertEqual(set(result.keys()), {("a.mp4", 480), ("b.mp4", 100)})


class TestListProjects(unittest.TestCase):
    """Tests for resolve_api.list_projects."""

    def test_returns_current_projects_and_databases(self):
        resolve = MagicMock()
        pm = resolve.GetProjectManager.return_value
        pm.GetCurrentProject.return_value.GetName.return_value = "Northlight"
        pm.GetCurrentDatabase.return_value = {"DbType": "Disk", "DbName": "External"}
        pm.GetProjectListInCurrentFolder.return_value = ["Riverside Film", "Northlight"]
        pm.GetDatabaseList.return_value = [
            {"DbType": "Disk", "DbName": "External"},
            {"DbType": "Disk", "DbName": "Internal"},
        ]
        result = resolve_api.list_projects(resolve)
        self.assertEqual(result["current"], "Northlight")
        self.assertEqual(result["current_database"], "External")
        self.assertEqual(result["projects"], ["Northlight", "Riverside Film"])
        self.assertEqual(result["databases"], ["External", "Internal"])

    def test_returns_empty_structure_when_no_project_manager(self):
        resolve = MagicMock()
        resolve.GetProjectManager.return_value = None
        result = resolve_api.list_projects(resolve)
        self.assertEqual(
            result, {"current": "", "current_database": "", "projects": [], "databases": []}
        )

    def test_current_empty_string_when_no_current_project(self):
        resolve = MagicMock()
        pm = resolve.GetProjectManager.return_value
        pm.GetCurrentProject.return_value = None
        pm.GetCurrentDatabase.return_value = {}
        pm.GetProjectListInCurrentFolder.return_value = []
        pm.GetDatabaseList.return_value = []
        result = resolve_api.list_projects(resolve)
        self.assertEqual(result["current"], "")

    def test_calls_goto_root_folder(self):
        resolve = MagicMock()
        pm = resolve.GetProjectManager.return_value
        pm.GetCurrentProject.return_value.GetName.return_value = "X"
        pm.GetCurrentDatabase.return_value = {"DbName": "D"}
        pm.GetProjectListInCurrentFolder.return_value = []
        pm.GetDatabaseList.return_value = []
        resolve_api.list_projects(resolve)
        pm.GotoRootFolder.assert_called_once()


class TestReconcileProjectKeywords(unittest.TestCase):
    """Tests for resolve_api.reconcile_project_keywords.

    This is the riskiest function added this session — it switches Resolve's
    active project/database and writes real metadata — so coverage here
    intentionally includes the exact failure modes discovered live during
    development: SaveProject() must be called before switching away, the
    switch-back must be verified/retried (not just fired and forgotten), and
    matching must be resilient to a target catalog spanning a different
    database than the source project.
    """

    def _make_clip(self, name, keywords, frames=100):
        clip = MagicMock()
        clip.GetName.return_value = name
        joined = ",".join(keywords)
        clip.GetMetadata.side_effect = lambda key=None: (
            {"Keywords": joined} if key is None else (joined if key == "Keywords" else None)
        )
        clip.GetClipProperty.side_effect = lambda k: (
            str(frames) if k == "Frames" and frames is not None else ""
        )
        return clip

    def _make_folder(self, clips):
        folder = MagicMock()
        folder.GetClipList.return_value = clips
        folder.GetSubFolderList.return_value = []
        return folder

    def _write_target_csv(self, path, rows):
        """rows: list of (file_name, frames, keywords_str)."""
        lines = ["File Name,Clip Directory,Keywords,Date Modified,Frames,Tag"]
        for name, frames, kws in rows:
            lines.append(f"{name},/vol/dir,{kws},Wed Jan  1 10:00:00 2025,{frames},0")
        with open(path, "w", encoding="utf-16", newline="") as f:
            f.write("\n".join(lines) + "\n")

    def _make_resolve(
        self,
        source_clips,
        target_clips,
        target_csv_rows,
        source_name="SourceProj",
        target_name="TargetProj",
        databases=None,
        save_ok=True,
        load_target_ok=True,
        load_source_ok=True,
    ):
        """Stateful mock simulating LoadProject/SetCurrentDatabase switching
        between a source project (SourceDB) and a target project (TargetDB)."""
        source_db = {"DbType": "Disk", "DbName": "SourceDB"}
        target_db = {"DbType": "Disk", "DbName": "TargetDB"}
        databases = databases if databases is not None else [source_db, target_db]

        source_project = MagicMock()
        source_project.GetName.return_value = source_name
        source_project.GetMediaPool.return_value.GetRootFolder.return_value = (
            self._make_folder(source_clips)
        )

        target_project = MagicMock()
        target_project.GetName.return_value = target_name
        target_project.GetMediaPool.return_value.GetRootFolder.return_value = (
            self._make_folder(target_clips)
        )

        state = {"project": source_project, "database": source_db}

        def load_project(name):
            if name == target_name and load_target_ok:
                state["project"] = target_project
                return True
            if name == source_name and load_source_ok:
                state["project"] = source_project
                return True
            return False

        def set_current_database(db):
            state["database"] = db
            return True

        def project_list_in_current_folder():
            return [target_name] if state["database"] == target_db else [source_name]

        resolve = MagicMock()
        pm = resolve.GetProjectManager.return_value
        pm.GetCurrentProject.side_effect = lambda: state["project"]
        pm.GetCurrentDatabase.side_effect = lambda: state["database"]
        pm.LoadProject.side_effect = load_project
        pm.SetCurrentDatabase.side_effect = set_current_database
        pm.GetProjectListInCurrentFolder.side_effect = project_list_in_current_folder
        pm.GetDatabaseList.return_value = databases
        pm.SaveProject.return_value = save_ok
        pm.GotoRootFolder.return_value = True

        def fake_export_metadata(_resolve, csv_path):
            self._write_target_csv(csv_path, target_csv_rows)
            return True

        return resolve, source_project, target_project, fake_export_metadata

    def _run(self, resolve, fake_export_metadata, **kwargs):
        with patch("resolve_api.export_metadata", side_effect=fake_export_metadata), \
             patch("resolve_api.time.sleep"):
            return resolve_api.reconcile_project_keywords(resolve, **kwargs)

    # -- validation / guard clauses ------------------------------------

    def test_no_current_project_raises(self):
        resolve = MagicMock()
        resolve.GetProjectManager.return_value.GetCurrentProject.return_value = None
        with self.assertRaises(RuntimeError):
            resolve_api.reconcile_project_keywords(resolve, "Target")

    def test_no_media_pool_raises(self):
        resolve = MagicMock()
        resolve.GetProjectManager.return_value.GetCurrentProject.return_value.GetMediaPool.return_value = None
        with self.assertRaises(RuntimeError):
            resolve_api.reconcile_project_keywords(resolve, "Target")

    def test_empty_target_name_raises(self):
        resolve, _, _, fake_export = self._make_resolve([], [], [])
        with self.assertRaises(RuntimeError):
            self._run(resolve, fake_export, target_project_name="   ")

    def test_target_same_as_source_raises_case_insensitive(self):
        resolve, _, _, fake_export = self._make_resolve([], [], [], source_name="MyProj")
        with self.assertRaises(RuntimeError):
            self._run(resolve, fake_export, target_project_name="myproj")

    def test_no_used_clips_and_no_tag_given_raises(self):
        clip = self._make_clip("a.mp4", ["France"])  # no Used:... tag anywhere
        resolve, _, _, fake_export = self._make_resolve([clip], [], [])
        with self.assertRaises(RuntimeError):
            self._run(resolve, fake_export, target_project_name="TargetProj")

    def test_tag_with_comma_raises(self):
        clip = self._make_clip("a.mp4", ["Used:X"])
        resolve, _, _, fake_export = self._make_resolve([clip], [], [])
        with self.assertRaises(RuntimeError):
            self._run(resolve, fake_export, target_project_name="TargetProj", tag="Bad,Tag")

    # -- project/database switching safety -----------------------------

    def test_save_failure_aborts_before_switching(self):
        clip = self._make_clip("a.mp4", ["Used:X"])
        resolve, _, _, fake_export = self._make_resolve([clip], [], [], save_ok=False)
        pm = resolve.GetProjectManager.return_value
        with self.assertRaises(RuntimeError):
            self._run(resolve, fake_export, target_project_name="TargetProj")
        pm.LoadProject.assert_not_called()

    def test_target_not_found_in_any_database_raises_and_restores(self):
        clip = self._make_clip("a.mp4", ["Used:X"])
        resolve, source_project, _, fake_export = self._make_resolve(
            [clip], [], [], databases=[{"DbType": "Disk", "DbName": "SourceDB"}],
        )
        with self.assertRaises(RuntimeError) as ctx:
            self._run(resolve, fake_export, target_project_name="Nonexistent")
        self.assertIn("Nonexistent", str(ctx.exception))
        self.assertIs(resolve.GetProjectManager.return_value.GetCurrentProject(), source_project)

    def test_load_target_failure_raises_and_restores_source(self):
        clip = self._make_clip("a.mp4", ["Used:X"])
        resolve, source_project, _, fake_export = self._make_resolve(
            [clip], [], [], load_target_ok=False,
        )
        with self.assertRaises(RuntimeError):
            self._run(resolve, fake_export, target_project_name="TargetProj")
        self.assertIs(resolve.GetProjectManager.return_value.GetCurrentProject(), source_project)

    def test_target_found_in_current_database_skips_database_search(self):
        clip = self._make_clip("a.mp4", ["Used:X"], frames=100)
        resolve, _, _, fake_export = self._make_resolve([clip], [], [])
        pm = resolve.GetProjectManager.return_value
        pm.GetProjectListInCurrentFolder.side_effect = lambda: ["SourceProj", "TargetProj"]
        result = self._run(
            resolve, fake_export, target_project_name="TargetProj", tag="Used:X", dry_run=True
        )
        pm.GetDatabaseList.assert_not_called()
        self.assertEqual(result["target_database"], "SourceDB")

    def test_source_restored_after_run(self):
        clip = self._make_clip("a.mp4", ["Used:X"])
        resolve, source_project, _, fake_export = self._make_resolve([clip], [], [])
        result = self._run(
            resolve, fake_export, target_project_name="TargetProj", tag="Used:X", dry_run=True
        )
        self.assertTrue(result["source_restored"])
        self.assertIs(resolve.GetProjectManager.return_value.GetCurrentProject(), source_project)

    def test_source_restored_false_when_switch_back_fails(self):
        clip = self._make_clip("a.mp4", ["Used:X"])
        resolve, _, _, fake_export = self._make_resolve([clip], [], [], load_source_ok=False)
        result = self._run(
            resolve, fake_export, target_project_name="TargetProj", tag="Used:X", dry_run=True
        )
        self.assertFalse(result["source_restored"])

    # -- matching / merge logic -----------------------------------------

    def test_dry_run_computes_diff_without_writing(self):
        source_clip = self._make_clip("a.mp4", ["Used:X", "France"], frames=480)
        target_clip = self._make_clip("a.mp4", [], frames=480)
        resolve, _, _, fake_export = self._make_resolve(
            [source_clip], [target_clip], [("a.mp4", 480, "")],
        )
        result = self._run(
            resolve, fake_export, target_project_name="TargetProj", tag="Used:X", dry_run=True
        )
        self.assertEqual(len(result["updated"]), 1)
        self.assertEqual(result["updated"][0]["clip"], "a.mp4")
        self.assertCountEqual(result["updated"][0]["added"], ["Used:X", "France"])
        target_clip.SetMetadata.assert_not_called()
        self.assertFalse(result["applied"])

    def test_apply_writes_union_and_saves_target(self):
        source_clip = self._make_clip("a.mp4", ["Used:X", "France"], frames=480)
        target_clip = self._make_clip("a.mp4", ["Louvre"], frames=480)
        resolve, _, _, fake_export = self._make_resolve(
            [source_clip], [target_clip], [("a.mp4", 480, "Louvre")],
        )
        pm = resolve.GetProjectManager.return_value
        result = self._run(
            resolve, fake_export, target_project_name="TargetProj", tag="Used:X", dry_run=False
        )
        written = target_clip.SetMetadata.call_args[0][1]
        self.assertIn("Louvre", written)
        self.assertIn("Used:X", written)
        self.assertIn("France", written)
        pm.SaveProject.assert_called()
        self.assertTrue(result["applied"])
        self.assertEqual(result["target_database"], "TargetDB")

    def test_already_synced_when_target_already_has_all_keywords(self):
        source_clip = self._make_clip("a.mp4", ["Used:X"], frames=480)
        target_clip = self._make_clip("a.mp4", ["Used:X"], frames=480)
        resolve, _, _, fake_export = self._make_resolve(
            [source_clip], [target_clip], [("a.mp4", 480, "Used:X")],
        )
        result = self._run(
            resolve, fake_export, target_project_name="TargetProj", tag="Used:X", dry_run=True
        )
        self.assertEqual(result["already_synced"], 1)
        self.assertEqual(result["updated"], [])
        target_clip.SetMetadata.assert_not_called()

    def test_unmatched_when_no_frame_count_match_in_target(self):
        source_clip = self._make_clip("a.mp4", ["Used:X"], frames=480)
        resolve, _, _, fake_export = self._make_resolve([source_clip], [], [])
        result = self._run(
            resolve, fake_export, target_project_name="TargetProj", tag="Used:X", dry_run=True
        )
        self.assertEqual(result["unmatched"], ["a.mp4"])

    def test_unmatched_when_source_clip_missing_frame_count(self):
        source_clip = self._make_clip("a.mp4", ["Used:X"], frames=None)
        resolve, _, _, fake_export = self._make_resolve([source_clip], [], [])
        result = self._run(
            resolve, fake_export, target_project_name="TargetProj", tag="Used:X", dry_run=True
        )
        self.assertEqual(result["unmatched"], ["a.mp4"])

    def test_unmatched_when_matched_via_csv_but_missing_from_live_object_walk(self):
        # target_csv_rows says (a.mp4, 480) exists, but the live object walk
        # (target_clips) doesn't have it — simulates the tree changing
        # mid-operation; must surface as unmatched, not crash or silently drop.
        source_clip = self._make_clip("a.mp4", ["Used:X", "France"], frames=480)
        resolve, _, _, fake_export = self._make_resolve(
            [source_clip], [], [("a.mp4", 480, "")],
        )
        result = self._run(
            resolve, fake_export, target_project_name="TargetProj", tag="Used:X", dry_run=True
        )
        self.assertEqual(result["unmatched"], ["a.mp4"])

    def test_uses_dominant_tag_when_none_given(self):
        clip1 = self._make_clip("a.mp4", ["Used:MyTag"], frames=100)
        clip2 = self._make_clip("b.mp4", ["Used:MyTag"], frames=200)
        resolve, _, _, fake_export = self._make_resolve([clip1, clip2], [], [])
        result = self._run(resolve, fake_export, target_project_name="TargetProj", dry_run=True)
        self.assertEqual(result["tag"], "Used:MyTag")


if __name__ == "__main__":
    unittest.main()
