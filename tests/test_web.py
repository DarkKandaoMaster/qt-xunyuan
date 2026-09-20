from __future__ import annotations

import unittest

from qt_tool.web import Handler


class WebPaginationTests(unittest.TestCase):
    def test_pagination_is_twenty_by_default_and_clamps_page(self):
        self.assertEqual(Handler._pagination({}, 45), (1, 20, 0))
        self.assertEqual(Handler._pagination({"page": ["2"]}, 45), (2, 20, 20))
        self.assertEqual(Handler._pagination({"page": ["99"]}, 45), (3, 20, 40))

    def test_explicit_page_size_keeps_review_queue_compatibility(self):
        self.assertEqual(Handler._pagination({"limit": ["500"]}, 420, 100), (1, 500, 0))


if __name__ == "__main__":
    unittest.main()
