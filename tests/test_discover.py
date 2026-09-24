from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from qt_tool.db import Database
from qt_tool.media import (MediaPipeline, load_negative_terms, negative_hit, negative_terms_for,
                           platform_of, source_score)


def flat_entry(video_id: str, title: str, duration: float | None = 60) -> dict:
    """Shape of a yt-dlp --flat-playlist entry: no extractor_key, only ie_key and a watch URL."""
    return {"_type": "url", "ie_key": "Youtube", "id": video_id, "title": title, "duration": duration,
            "url": f"https://www.youtube.com/watch?v={video_id}"}


class NegativeTermTests(unittest.TestCase):
    def test_templates_negative_is_merged_with_builtin_avoid_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "search_templates.yaml"
            path.write_text(json.dumps({"defaults": {"negative": ["Lyrics", "montage"]}}), encoding="utf-8")
            terms = load_negative_terms(path)
        self.assertIn("lyrics", terms)
        self.assertIn("gopro", terms)
        self.assertEqual(terms.count("montage"), 1)
        self.assertIn("cinematic", load_negative_terms(Path("missing-file.yaml")))

    def test_title_match_is_case_insensitive_and_word_start(self):
        terms = load_negative_terms(None)
        self.assertEqual(negative_hit("Paris 4K Walking Tour 2024", terms), "walking tour")
        self.assertEqual(negative_hit("GoPro HERO skiing", terms), "gopro")
        self.assertEqual(negative_hit("Epic B-Roll pack", terms), "b roll")
        self.assertEqual(negative_hit("My Hyper-lapse", ["hyperlapse", "hyper lapse"]), "hyper lapse")
        self.assertIsNone(negative_hit("Chef chopping vegetables stock footage", terms))
        self.assertIsNone(negative_hit("Construction worker in helmet walking", terms))
        self.assertIsNone(negative_hit("Preview of the trail", ["review"]))

    def test_t84_may_keep_slow_motion(self):
        terms = load_negative_terms(None)
        self.assertEqual(negative_hit("Dancer slow motion", negative_terms_for(terms, "T8.3")), "slow motion")
        self.assertIsNone(negative_hit("Dancer slow motion", negative_terms_for(terms, "T8.4")))
        self.assertIsNotNone(negative_hit("Dancer slow motion vlog", negative_terms_for(terms, "T8.4")))

    def test_source_score_uses_the_same_terms(self):
        base = {"title": "man walking", "description": "", "duration": 60}
        clean = source_score(base, "T6.8", "man walking")
        penalised = source_score(dict(base, description="shot on gopro, cinematic"), "T6.8", "man walking")
        self.assertEqual(clean - penalised, 36.0)
        custom = source_score(dict(base, description="lyrics"), "T6.8", "man walking", negatives=["lyrics"])
        self.assertEqual(clean - custom, 18.0)


class PlatformTests(unittest.TestCase):
    def test_flat_youtube_entries_get_youtube_platform(self):
        self.assertEqual(platform_of({"url": "https://www.youtube.com/watch?v=abc"}), "youtube")
        self.assertEqual(platform_of({"ie_key": "Youtube", "url": "abc"}), "youtube")
        self.assertEqual(platform_of({"url": "https://youtu.be/abc"}), "youtube")
        self.assertEqual(platform_of({"extractor_key": "Vimeo"}), "vimeo")
        self.assertEqual(platform_of({"url": "https://example.com/v"}), "unknown")
        self.assertEqual(platform_of({"url": "https://notyoutube.com.evil/v"}), "unknown")


class PipelineDiscoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        templates = data_dir / "search_templates.yaml"
        templates.write_text(json.dumps({"defaults": {"negative": ["lyrics"]}}), encoding="utf-8")
        self.db = Database(data_dir / "test.sqlite3")
        settings = SimpleNamespace(data_dir=data_dir, ytdlp_bin="yt-dlp", ytdlp_js_runtime="",
                                   ffmpeg_bin="missing-ffmpeg", ytdlp_cookies_file="",
                                   ytdlp_cookies_from_browser="", ytdlp_po_token_url="", ytdlp_proxy="",
                                   ytdlp_sleep_interval=5, ytdlp_max_sleep_interval=10,
                                   source_max_duration_seconds=600, search_templates_path=templates)
        self.pipeline = MediaPipeline(settings, self.db, None)
        self.addCleanup(self.tmp.cleanup)

    def _run_with(self, entries):
        proc = subprocess.CompletedProcess([], 0, stdout=json.dumps({"entries": entries}), stderr="")
        return patch("qt_tool.media._run", return_value=proc), patch("qt_tool.media._command_exists", return_value=True)

    def test_search_counts_titles_filtered_by_negative_terms(self):
        entries = [flat_entry("a1", "Man walking up stairs"),
                   flat_entry("a2", "POV climbing stairs"),
                   flat_entry("a3", "Song LYRICS video"),
                   flat_entry("a4", "Long stair walk", duration=4000),
                   flat_entry("a5", "Stairs 24/7 live stream", duration=None)]
        run_patch, exists_patch = self._run_with(entries)
        with run_patch as run, exists_patch:
            stats = self.pipeline.discover('"following shot of" man walking up stairs', "T6.8", 8)
        command = run.call_args.args[0]
        self.assertIn("--flat-playlist", command)
        self.assertEqual(command[-1], 'ytsearch8:"following shot of" man walking up stairs')
        self.assertEqual((stats["found"], stats["created"], stats["filtered"], stats["excluded"],
                          stats["excluded_live"]), (5, 1, 2, 2, 1))
        source = self.db.list_sources(limit=10, view="all")[0]
        self.assertEqual((source["platform"], source["video_id"]), ("youtube", "a1"))


if __name__ == "__main__":
    unittest.main()
