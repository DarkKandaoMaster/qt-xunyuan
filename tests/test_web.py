from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from dataclasses import replace
from threading import Event
from qt_tool.config import load_settings

from qt_tool.web import Handler, App
from unittest.mock import Mock


class WebPaginationTests(unittest.TestCase):
    def test_preflight_runs_while_download_pool_is_occupied(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = replace(load_settings(), db_path=Path(tmp) / 'test.sqlite3', max_download_concurrency=1)
            app = App(settings)
            release = Event()
            downloading = Event()
            checked = Event()
            def download(*args, **kwargs):
                downloading.set()
                release.wait(5)
                return Path(tmp) / 'proxy.mp4'
            def preflight(*args, **kwargs):
                checked.set()
                return {'reason': 'test'}
            app.pipeline.validate_proxy_source = Mock()
            app.pipeline.download_proxy = download
            app.pipeline.preflight_source = preflight
            try:
                ids = [app.db.add_source({'platform': 'test', 'video_id': str(i), 'url': f'https://example.test/{i}'})[0] for i in range(3)]
                app.queue_source_job('proxy', ids[0])
                self.assertTrue(downloading.wait(2))
                queued, _ = app.queue_source_job('proxy', ids[1])
                check, _ = app.queue_source_job('preflight', ids[2])
                self.assertTrue(checked.wait(2), '规格检查不应等待下载队列')
                self.assertEqual(app.db.get_job(queued)['status'], 'QUEUED')
                self.assertEqual(app.db.get_job(queued)['queue_ahead'], 1)
                self.assertEqual(app.queue_source_job('preflight', ids[0])[1], False)
            finally:
                release.set()
                for executor in app.executors.values():
                    executor.shutdown(wait=True)

    def test_analysis_summary_distinguishes_rejected_from_waiting(self):
        app = App.__new__(App)
        app.db = Mock()
        app.db.get_candidate.side_effect = lambda cid: {'status': 'REJECTED' if cid == 1 else 'WAITING_REVIEW'}
        app.db.get_rule_results.return_value = [{'rule_id': 'SPEC_FPS', 'status': 'FAIL', 'deterministic': True, 'reason': '23.976 fps 低于 24 fps'}]
        result = app.analysis_summary([1, 2])
        self.assertEqual((result['waiting_count'], result['rejected_count']), (1, 1))
        self.assertIn('待人工审核 1 个，自动拒绝 1 个', result['message'])
        self.assertIn('23.976', result['message'])
        result = app.analysis_summary([1])
        self.assertEqual(result['waiting_count'], 0)
        self.assertIn('拒绝列表', result['message'])
        self.assertIn('本次未新增候选', app.analysis_summary([])['message'])

    def test_pagination_is_twenty_by_default_and_clamps_page(self):
        self.assertEqual(Handler._pagination({}, 45), (1, 20, 0))
        self.assertEqual(Handler._pagination({"page": ["2"]}, 45), (2, 20, 20))
        self.assertEqual(Handler._pagination({"page": ["99"]}, 45), (3, 20, 40))

    def test_explicit_page_size_keeps_review_queue_compatibility(self):
        self.assertEqual(Handler._pagination({"limit": ["500"]}, 420, 100), (1, 500, 0))


if __name__ == "__main__":
    unittest.main()
