import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from qt_tool.camera import CameraMotionAnalyzer, classify_window, summarize
from qt_tool.db import Database
from qt_tool.media import MediaPipeline
from qt_tool.rules import RuleEngine

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = np = None


@unittest.skipIf(np is None, 'optional OpenCV unavailable')
class CameraTests(unittest.TestCase):
    def window(self, matrices):
        return classify_window([{'matrix': m} for m in matrices], 0, 3, 550)

    def test_fixed_pan_shake_zoom_slow_and_missing_evidence(self):
        identity = [[1, 0, 0], [0, 1, 0]]
        self.assertEqual(self.window([identity] * 12)['status'], 'FIXED')
        self.assertEqual(self.window([[[1, 0, 1], [0, 1, 0]]] * 12)['status'], 'MOVING')
        self.assertEqual(self.window([[[1, 0, (-1)**i * 3], [0, 1, 0]] for i in range(12)])['status'], 'SHAKE')
        self.assertEqual(self.window([[[1.002, 0, 0], [0, 1.002, 0]]] * 12)['status'], 'ZOOM')
        self.assertEqual(self.window([[[1.002, 0, 2], [0, 1.002, 0]]] * 12)['status'], 'ZOOM')
        self.assertEqual(self.window([[[1, 0, .2], [0, 1, 0]]] * 12)['status'], 'UNKNOWN')
        self.assertEqual(self.window([identity] * 11 + [None])['status'], 'UNKNOWN')

    def test_rotation_and_mixed_are_not_fixed(self):
        a = math.radians(.1)
        rotation = [[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0]]
        self.assertEqual(self.window([rotation] * 12)['status'], 'MOVING')
        report = summarize([dict(start=0, end=3, status='FIXED'), dict(start=3, end=6, status='MOVING')], 0, 6)
        self.assertEqual(report['status'], 'MIXED')
        self.assertTrue(report['advisory_only'])
        self.assertEqual(summarize([dict(start=0, end=3, status='FIXED')], 0, 20)['status'], 'UNKNOWN')

    def test_absolute_estimates_do_not_accumulate_zoom_or_bridge_missing_data(self):
        samples = [{'matrix':[[1,0,i],[0,1,0]], 'absolute':True} for i in range(1,13)]
        result = classify_window(samples,0,3,550)
        self.assertEqual(result['status'],'MOVING')
        self.assertAlmostEqual(result['drift'],12/550,places=4)
        samples[3]['matrix'] = None
        self.assertEqual(classify_window(samples,0,3,550)['status'],'MOVING')
        samples[4]['matrix'] = None
        self.assertEqual(classify_window(samples,0,3,550)['status'],'UNKNOWN')

    @staticmethod
    def textured():
        rng = np.random.default_rng(34)
        return cv2.GaussianBlur(rng.integers(0, 256, (270, 480), dtype=np.uint8), (3, 3), 0)

    def test_background_consensus_rejects_local_motion_and_low_texture(self):
        analyzer = CameraMotionAnalyzer()
        base = self.textured()
        shifted = cv2.warpAffine(base, np.float32([[1, 0, 3], [0, 1, 0]]), (480, 270))
        pair = analyzer.estimate_pair(base, shifted)
        self.assertIsNotNone(pair)
        self.assertAlmostEqual(pair[0][2], 3, delta=.3)
        a, b = base.copy(), base.copy()
        a[80:190, 130:270] = 220
        b[80:190, 220:360] = 30
        pair = analyzer.estimate_pair(a, b)
        # Only edge tracks survive this large occlusion: abstain rather than
        # guessing that motion of the foreground represents the camera.
        self.assertIsNone(pair)
        blank = np.full_like(base, 100)
        self.assertIsNone(analyzer.estimate_pair(blank, blank))

    def test_end_to_end_proxy_fixtures(self):
        analyzer = CameraMotionAnalyzer()
        base = self.textured()
        with tempfile.TemporaryDirectory() as tmp:
            for kind, expected in [('fixed', 'FIXED'), ('pan', 'MOVING'), ('shake', 'SHAKE'), ('zoom', 'ZOOM'), ('mixed', 'MIXED'), ('blank', 'UNKNOWN')]:
                path = Path(tmp) / (kind + '.avi')
                out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 12, (480, 270))
                self.assertTrue(out.isOpened())
                for i in range(72):
                    matrix = np.float32([[1, 0, 0], [0, 1, 0]])
                    if kind == 'pan':
                        matrix[0, 2] = i * .5
                    if kind == 'mixed':
                        matrix[0, 2] = max(0, i - 36) * .5
                    if kind == 'shake':
                        matrix[0, 2] = 3 * math.sin(i * math.pi / 6)
                    if kind == 'zoom':
                        matrix = cv2.getRotationMatrix2D((240, 135), 0, 1 + i * .0015)
                    frame = cv2.warpAffine(base, matrix, (480, 270), borderMode=cv2.BORDER_REFLECT)
                    if kind == 'fixed':
                        x = 130 + i % 80
                        frame[85:180, x:x+70] = 220  # moving hand on stationary board
                    if kind == 'blank':
                        frame[:] = 90
                    out.write(cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR))
                out.release()
                report = analyzer.analyze(path, 0, 6)
                self.assertEqual(report['status'], expected, (kind, report))
                self.assertTrue(report['advisory_only'])
                self.assertEqual(analyzer.analyze(path, 0, 6, time_budget=0)['status'], 'UNKNOWN')
            self.assertEqual(analyzer.analyze(Path(tmp) / 'missing.mp4', 0, 6)['status'], 'UNKNOWN')


class CameraPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Database(self.root / 'test.sqlite3')
        self.sid, _ = self.db.add_source({'platform':'test', 'video_id':'camera', 'url':'https://example.test/camera'})
        self.ids = []
        for i, state in enumerate(['FIXED','SHAKE','ZOOM','MOVING','UNKNOWN','MIXED',None]):
            cid, _ = self.db.add_candidate({'source_id': self.sid, 'start_time': i*10, 'end_time':i*10+6,
                'duration':6, 'candidate_bucket':'T4', 'candidate_unit':'T4.2', 'proxy_path':'proxy.mp4',
                'facts': {'retained':True, **({'camera_motion':{'status':state}} if state else {})}})
            self.ids.append(cid)

    def tearDown(self):
        self.tmp.cleanup()

    def test_filtered_pagination_and_unknown_legacy(self):
        self.assertEqual(self.db.count_candidates('WAITING_REVIEW','T4','FIXED'),2)
        self.assertEqual(len(self.db.list_candidates('WAITING_REVIEW',1,1,'T4','FIXED')),1)
        self.assertEqual(self.db.count_candidates('WAITING_REVIEW','T1','FIXED'),0)
        self.assertEqual(self.db.count_candidates('WAITING_REVIEW',None,'UNTESTED'),1)
        self.assertEqual(self.db.count_candidates('WAITING_REVIEW',None,'UNKNOWN'),1)
        with self.assertRaises(ValueError):
            self.db.count_candidates(camera="invalid' OR 1=1")

    def test_advice_preserves_state_and_rejects_stale_results(self):
        cid = self.ids[0]
        self.db.save_camera_advice(cid,0,6,{'status':'ZOOM'})
        row = self.db.get_candidate(cid)
        self.assertEqual(row['status'],'WAITING_REVIEW')
        self.assertTrue(json.loads(row['facts_json'])['retained'])
        with self.assertRaises(ValueError):
            self.db.save_camera_advice(cid,0,5,{'status':'FIXED'})
        self.db.review(cid,{'decision':'ACCEPT','final_viewpoint':'third_person'})
        with self.assertRaises(ValueError):
            self.db.save_camera_advice(cid,0,6,{'status':'FIXED'})

    def test_trim_invalidates_advice_and_manual_check_never_rejects(self):
        rules = Mock()
        rules.duration_bucket.return_value = ('short', None, '')
        rules.evaluate.return_value = []
        rules.unit_gate.return_value = None
        pipeline = MediaPipeline(SimpleNamespace(data_dir=self.root), self.db, rules)
        pipeline.camera_analyzer.analyze = Mock(return_value={'status':'ZOOM','advisory_only':True})
        cid = self.ids[0]
        pipeline.check_candidate_camera(cid)
        self.assertEqual(self.db.get_candidate(cid)['status'],'WAITING_REVIEW')
        pipeline.trim_candidate(cid,0,5.5)
        self.assertNotIn('camera_motion',json.loads(self.db.get_candidate(cid)['facts_json']))

    def test_analysis_attaches_advice_before_subject_without_rejecting_or_trimming(self):
        calls = []
        rules = Mock()
        rules.buckets = {'T4':{'material_type':'live_action'}}
        rules.duration_bucket.return_value = ('short',None,'')
        rules.evaluate.return_value = []
        rules.unit_gate.return_value = None
        rules.automatic_reject.return_value = False
        pipeline = MediaPipeline(SimpleNamespace(data_dir=self.root), self.db, rules)
        self.db.update_source(self.sid, proxy_path='proxy.mp4',target_unit='T4.2')
        pipeline.probe = Mock(return_value={'playable':True})
        pipeline.detect_shots = Mock(side_effect=lambda p: calls.append('shots') or [(100,106)])
        pipeline.camera_analyzer.analyze = Mock(side_effect=lambda *a: calls.append('camera') or {'status':'ZOOM'})
        subject = SimpleNamespace(segments=((100,106),),facts_for=lambda s:{})
        pipeline.subject_analyzer.analyze = Mock(side_effect=lambda *a: calls.append('subject') or subject)
        pipeline.representative_frame_hash = Mock(return_value=None)
        ids = pipeline.analyze_source(self.sid)
        row = self.db.get_candidate(ids[0])
        self.assertEqual(calls,['shots','camera','subject'])
        self.assertEqual((row['start_time'],row['end_time'],row['status']),(100,106,'WAITING_REVIEW'))
        self.assertEqual(json.loads(row['facts_json'])['camera_motion']['status'],'ZOOM')

    def test_advice_does_not_become_qa_failure(self):
        root = Path(__file__).resolve().parents[1]
        rules = RuleEngine(root/'rules/qt_rules_v4.yaml',root/'rules/conflicts.yaml')
        # This report is separate from deterministic rule gates in the pilot.
        baseline = [r.to_dict() for r in rules.evaluate({})]
        for state in ['FIXED','SHAKE','ZOOM','UNKNOWN','MOVING']:
            self.assertEqual([r.to_dict() for r in rules.evaluate({'camera_motion':{'status':state}})], baseline)


if __name__ == '__main__':
    unittest.main()
