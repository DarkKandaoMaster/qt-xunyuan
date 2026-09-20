from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qt_tool.db import Database


class DatabaseTests(unittest.TestCase):
    def test_source_dedup_and_persistent_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.sqlite3")
            source = {"platform": "youtube", "video_id": "abc", "url": "https://example.test/v", "title": "A"}
            first, created = db.add_source(source)
            second, created_again = db.add_source(source)
            self.assertEqual(first, second)
            self.assertTrue(created)
            self.assertFalse(created_again)
            cid, was_created = db.add_candidate({"source_id": first, "start_time": 1.0, "end_time": 8.0,
                                                  "duration": 7.0, "facts": {"duration": 7.0}})
            self.assertTrue(was_created)
            self.assertEqual(db.get_candidate(cid)["duration"], 7.0)

    def test_background_job_lifecycle_and_deduplication(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.sqlite3")
            source_id, _ = db.add_source(
                {"platform": "manual", "video_id": "job-source", "url": "https://example.test/v", "title": "A"}
            )

            job_id, created = db.create_job("proxy", source_id)
            duplicate_id, duplicate_created = db.create_job("proxy", source_id)
            self.assertTrue(created)
            self.assertFalse(duplicate_created)
            self.assertEqual(job_id, duplicate_id)
            self.assertEqual(db.list_sources()[0]["active_job_id"], job_id)

            db.start_job(job_id)
            self.assertEqual(db.get_job(job_id)["status"], "RUNNING")
            self.assertEqual(db.get_job(job_id)["attempts"], 1)

            db.finish_job(job_id, "DONE", {"message": "完成"})
            finished = db.get_job(job_id)
            self.assertEqual(finished["status"], "DONE")
            self.assertIn("完成", finished["result_json"])
            self.assertIsNone(db.list_sources()[0]["active_job_id"])

    def test_candidate_part_number_follows_source_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.sqlite3")
            source_id, _ = db.add_source(
                {"platform": "youtube", "video_id": "parts", "url": "https://example.test/v", "title": "A"}
            )
            later, _ = db.add_candidate({"source_id": source_id, "start_time": 20.0, "end_time": 30.0,
                                         "duration": 10.0, "facts": {}})
            earlier, _ = db.add_candidate({"source_id": source_id, "start_time": 5.0, "end_time": 15.0,
                                           "duration": 10.0, "facts": {}})
            self.assertEqual(db.candidate_part_number(earlier), 1)
            self.assertEqual(db.candidate_part_number(later), 2)

    def test_reanalysis_removes_only_machine_owned_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.sqlite3")
            source_id, _ = db.add_source(
                {"platform": "youtube", "video_id": "reanalyze", "url": "https://example.test/v", "title": "A"}
            )
            machine_id, _ = db.add_candidate({"source_id": source_id, "start_time": 0.0, "end_time": 10.0,
                                               "duration": 10.0, "facts": {}})
            restored_id, _ = db.add_candidate({"source_id": source_id, "start_time": 20.0, "end_time": 30.0,
                                                "duration": 10.0, "facts": {}})
            db.review(restored_id, {"decision": "RESTORE", "notes": "从拒绝列表恢复"})
            reviewed_id, _ = db.add_candidate({"source_id": source_id, "start_time": 10.0, "end_time": 20.0,
                                                "duration": 10.0, "facts": {}})
            db.review(reviewed_id, {"decision": "REJECT", "notes": "人工拒绝"})

            self.assertEqual(db.clear_replaceable_candidates(source_id), 2)
            self.assertIsNone(db.get_candidate(machine_id))
            self.assertIsNone(db.get_candidate(restored_id))
            self.assertIsNotNone(db.get_candidate(reviewed_id))


if __name__ == "__main__":
    unittest.main()
