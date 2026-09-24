from __future__ import annotations

import unittest

from qt_tool.media import platform_of


class PlatformTests(unittest.TestCase):
    def test_flat_youtube_entries_get_youtube_platform(self):
        self.assertEqual(platform_of({"url": "https://www.youtube.com/watch?v=abc"}), "youtube")
        self.assertEqual(platform_of({"ie_key": "Youtube", "url": "abc"}), "youtube")
        self.assertEqual(platform_of({"url": "https://youtu.be/abc"}), "youtube")
        self.assertEqual(platform_of({"extractor_key": "Vimeo"}), "vimeo")
        self.assertEqual(platform_of({"url": "https://example.com/v"}), "unknown")
        self.assertEqual(platform_of({"url": "https://notyoutube.com.evil/v"}), "unknown")


if __name__ == "__main__":
    unittest.main()
