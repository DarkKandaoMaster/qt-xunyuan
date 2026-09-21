from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from qt_tool.db import Database
from qt_tool.media import MediaPipeline, delivery_description, merge_facts


class MediaTests(unittest.TestCase):
    def test_delivery_description_preserves_chinese_and_sanitizes_title(self):
        self.assertEqual(delivery_description('  城市跑步 / 跟拍: 4K  '), "城市跑步_跟拍_4K")

    def test_final_probe_values_override_candidate_facts_without_duplicate_keys(self):
        facts = merge_facts('{"duration": 12, "width": 854}',
                            {"duration": 60.074, "width": 3840, "height": 2160},
                            duration=60.074, shot_count=4)
        self.assertEqual(facts["duration"], 60.074)
        self.assertEqual(facts["width"], 3840)
        self.assertEqual(facts["height"], 2160)
        self.assertEqual(facts["shot_count"], 4)

    def test_delivery_csv_uses_required_chinese_columns(self):
        row = {
            "candidate_id": 42,
            "viewpoint": "third_person", "created_at": "2026-09-20T10:00:00+00:00",
            "exported_at": "2026-09-20T11:00:00+00:00",
            "unit": "T1.1", "width": 3840, "height": 2160, "duration": 12.3456,
        }

        class DeliveryDB:
            exported_ids = []

            @staticmethod
            def delivery_rows():
                return [row]

            @classmethod
            def mark_delivery_exported(cls, candidate_ids, exported_at=None):
                cls.exported_ids = candidate_ids

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "deliverable").mkdir()
            pipeline = MediaPipeline(SimpleNamespace(data_dir=data_dir), DeliveryDB(), None)
            output = pipeline.export_delivery_csv()
            with output.open(encoding="utf-8-sig", newline="") as stream:
                exported = list(csv.DictReader(stream))

        self.assertEqual(list(exported[0]), ["人称", "OSS路径", "交付时间", "统合单元", "分辨率", "时长"])
        self.assertEqual(exported[0]["人称"], "第三人称")
        self.assertEqual(exported[0]["OSS路径"], "OSS")
        self.assertEqual(exported[0]["交付时间"], "2026-09-20T11:00:00+00:00")
        self.assertEqual(exported[0]["统合单元"], "T1.1")
        self.assertEqual(exported[0]["分辨率"], "3840x2160")
        self.assertEqual(exported[0]["时长"], "12.346")
        self.assertEqual(DeliveryDB.exported_ids, [42])

    def test_pipeline_repairs_legacy_delivery_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db_path = data_dir / "test.sqlite3"
            db = Database(db_path)
            source_id, _ = db.add_source({"platform": "youtube", "video_id": "legacy",
                                          "url": "https://example.test/v", "title": "城市跑步跟拍"})
            candidate_id, _ = db.add_candidate({"source_id": source_id, "start_time": 0.0, "end_time": 10.0,
                                                 "duration": 10.0, "candidate_unit": "T1.1", "facts": {}})
            deliver_dir = data_dir / "deliverable" / "QT寻源数据" / "T1_高动态载具" / "第三人称"
            deliver_dir.mkdir(parents=True)
            legacy_path = deliver_dir / "unknown_legacy-1.mp4"
            legacy_path.write_bytes(b"video")
            db.create_final_clip(candidate_id, final_path=str(legacy_path), qa_status="PASS",
                                 deliverable_status="READY")

            migrated = Database(db_path)
            MediaPipeline(SimpleNamespace(data_dir=data_dir), migrated, None)
            expected = deliver_dir / "T1.1_001_城市跑步跟拍.mp4"
            self.assertTrue(expected.is_file())
            self.assertFalse(legacy_path.exists())
            self.assertEqual(migrated.delivery_rows()[0]["final_path"], str(expected))


if __name__ == "__main__":
    unittest.main()
