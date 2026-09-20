from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from qt_tool.media import MediaPipeline, merge_facts


class MediaTests(unittest.TestCase):
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
            "viewpoint": "third_person", "created_at": "2026-09-20T10:00:00+00:00",
            "unit": "T1.1", "width": 3840, "height": 2160, "duration": 12.3456,
        }

        class DeliveryDB:
            @staticmethod
            def delivery_rows():
                return [row]

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
        self.assertEqual(exported[0]["统合单元"], "T1.1")
        self.assertEqual(exported[0]["分辨率"], "3840x2160")
        self.assertEqual(exported[0]["时长"], "12.346")


if __name__ == "__main__":
    unittest.main()
