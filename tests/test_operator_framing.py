import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from qt_tool.operator_framing import (OperatorFramingAnalyzer, classify_frame,
                                     guard_identity, summarize, supported)
from qt_tool.db import Database
from qt_tool.media import MediaPipeline
from qt_tool.rules import RuleEngine


def pose(scale=1, hidden=()):
    points = [(.5, .1)] * 11 + [(.3, .3), (.7, .3), (.2, .4), (.8, .4)]
    points += [(.25, .45)] * 8 + [(.35, .6), (.65, .6)] + [(.4, .9)] * 8
    return [SimpleNamespace(x=.4+x*scale, y=y*scale, visibility=0 if i in hidden else 1, presence=1)
            for i, (x, y) in enumerate(points)]


class OperatorTests(unittest.TestCase):
    def test_no_detection_is_not_failure_and_hands_need_evidence(self):
        self.assertEqual(classify_frame([], 0)['status'], 'UNKNOWN')
        self.assertEqual(classify_frame([], 2)['status'], 'HANDS_ONLY')
        self.assertEqual(classify_frame([pose(.5), pose(.5)], 2)['status'], 'UNKNOWN')

    def test_visible_small_partial_and_unsupported(self):
        self.assertEqual(classify_frame([pose(.5)], 0)['status'], 'VISIBLE')
        self.assertEqual(classify_frame([pose(.2)], 0)['status'], 'SMALL')
        self.assertEqual(classify_frame([pose(.5, range(11))], 1)['status'], 'PARTIAL')
        self.assertFalse(supported('T8.3'))
        self.assertTrue(supported('T4.2'))

    def test_unusual_pose_does_not_claim_visible_and_bystander_is_uncertain(self):
        person = pose(.5)
        for i in range(11):
            person[i].y = .4
        self.assertNotEqual(classify_frame([person], 0)['status'], 'VISIBLE')
        self.assertEqual(guard_identity({'status':'SMALL', 'area_ratio':.01},
                                       [{'status':'VISIBLE', 'area_ratio':.3}])['status'], 'UNKNOWN')

    def test_sustained_warning_sparse_single_frame_and_missing_coverage(self):
        samples = [dict(start=i, end=i+1, status='HANDS_ONLY', reason='hand') for i in range(5)]
        self.assertEqual(summarize(samples, 0, 5)['status'], 'HANDS_ONLY')
        self.assertTrue(summarize(samples, 0, 5)['advisory_only'])
        self.assertEqual(summarize(samples[:1], 0, 5)['status'], 'UNKNOWN')
        self.assertEqual(summarize([dict(start=0,end=10,status='SMALL',reason='small')], 0, 10)['status'], 'UNKNOWN')
        visible = [dict(start=0,end=1,status='VISIBLE',reason='visible')]
        self.assertEqual(summarize(visible, 0, 10)['status'], 'UNKNOWN')
        self.assertEqual(summarize(samples, 0, 20)['status'], 'MIXED')

    def test_unavailable_and_busy_components_degrade_to_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            analyzer = OperatorFramingAnalyzer(Path(tmp))
            self.assertEqual(analyzer.analyze('missing.mp4',0,6,'T4.2')['status'], 'UNKNOWN')
            self.assertEqual(analyzer.analyze('missing.mp4',0,6,'T8.3')['status'], 'NOT_APPLICABLE')
            with patch.object(Path, 'is_file', return_value=True):
                analyzer.slot.acquire()
                self.assertEqual(analyzer.analyze('missing.mp4',0,6,'T4.2')['status'], 'UNKNOWN')
                analyzer.slot.release()

    def test_persistence_and_trim_do_not_change_review_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root/'test.sqlite3')
            sid, _ = db.add_source({'platform':'test','video_id':'op','url':'https://example.test/op'})
            cid, _ = db.add_candidate({'source_id':sid,'start_time':0,'end_time':6,'duration':6,
                                      'candidate_unit':'T4.2','candidate_bucket':'T4','proxy_path':'p.mp4',
                                      'facts':{'retained':True}})
            rules = Mock()
            rules.duration_bucket.return_value = ('short',None,'')
            rules.evaluate.return_value = []
            rules.unit_gate.return_value = None
            pipeline = MediaPipeline(SimpleNamespace(data_dir=root), db, rules)
            pipeline.operator_analyzer.analyze = Mock(return_value={'status':'HANDS_ONLY','advisory_only':True})
            pipeline.check_candidate_operator(cid)
            row = db.get_candidate(cid)
            self.assertEqual(row['status'],'WAITING_REVIEW')
            self.assertTrue(json.loads(row['facts_json'])['retained'])
            with self.assertRaises(ValueError):
                db.save_visual_advice(cid,0,5,'operator_framing',{})
            pipeline.trim_candidate(cid,0,5.5)
            self.assertNotIn('operator_framing',json.loads(db.get_candidate(cid)['facts_json']))
            db.review(cid, {'decision':'ACCEPT','final_viewpoint':'third_person'})
            with self.assertRaises(ValueError):
                pipeline.check_candidate_operator(cid)

    def test_advice_is_not_hard_qa_gate(self):
        root = Path(__file__).resolve().parents[1]
        rules = RuleEngine(root/'rules/qt_rules_v4.yaml',root/'rules/conflicts.yaml')
        baseline = [r.to_dict() for r in rules.evaluate({})]
        for status in ['HANDS_ONLY','SMALL','PARTIAL','UNKNOWN','VISIBLE']:
            self.assertEqual([r.to_dict() for r in rules.evaluate({'operator_framing':{'status':status}})], baseline)


if __name__ == '__main__':
    unittest.main()
