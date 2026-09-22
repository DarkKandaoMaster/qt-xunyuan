import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from qt_tool.db import Database
from qt_tool.media import MediaPipeline


class FinalDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Database(self.root / 'test.sqlite3')
        self.sid, _ = self.db.add_source({'platform': 'test', 'video_id': 'same', 'url': 'https://example.test/v', 'duration': 100})
        self.pipeline = MediaPipeline(SimpleNamespace(data_dir=self.root, ytdlp_bin='fake', ffmpeg_bin='fake'), self.db, None)
        self.pipeline._ytdlp = lambda *args, **kwargs: list(args)

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_source_downloaded_once_with_concurrent_candidates(self):
        started, release = Event(), Event()
        def run(args, *a, **k):
            self.assertEqual(args[args.index('-f') + 1].split('/')[0], 'bestvideo[height<=2160]+bestaudio')
            self.assertEqual(args[args.index('--retries') + 1], '5')
            self.assertIn('http:exp=1:8', args)
            self.assertIn('--abort-on-unavailable-fragments', args)
            started.set()
            self.assertTrue(release.wait(4))
            output = Path(args[args.index('-o') + 1].replace('%(ext)s', 'mp4'))
            output.write_bytes(b'complete')
            return SimpleNamespace(returncode=0, stderr='ok')
        self.pipeline._validate_original = Mock()
        with patch('qt_tool.media._command_exists', return_value=True), patch('qt_tool.media._run_download', side_effect=run) as download:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.pipeline.download_final, self.sid)
                self.assertTrue(started.wait(2))
                second = pool.submit(self.pipeline.download_final, self.sid)
                release.set()
                self.assertEqual(first.result(5), second.result(5))
            self.assertEqual(download.call_count, 1)

    def test_truncated_video_rejected_even_when_container_and_audio_are_complete(self):
        original = self.root / 'original' / 'damaged.mp4'
        original.parent.mkdir()
        original.write_bytes(b'damaged')
        self.pipeline.probe = Mock(return_value={'playable': True, 'has_audio': True,
            'duration': 100, 'video_duration': 49, 'audio_duration': 100})
        with self.assertRaisesRegex(ValueError, '不完整'):
            self.pipeline._validate_original(original, {'duration': 100})

    def test_probe_warnings_are_not_ignored_and_decode_failure_not_cached(self):
        original = self.root / 'original' / 'bad.mp4'
        original.parent.mkdir()
        original.write_bytes(b'bad')
        self.pipeline.probe = Mock(return_value={'playable': True, 'has_audio': True, 'probe_error': 'sample size too large'})
        with self.assertRaisesRegex(ValueError, '损坏'):
            self.pipeline._validate_original(original, {'duration': 100})
        self.pipeline.probe.return_value = {'playable': True, 'has_audio': True, 'duration': 100, 'video_duration': 100, 'audio_duration': 100}
        with patch('qt_tool.media._run', return_value=SimpleNamespace(returncode=1, stderr='corrupt packet')):
            with self.assertRaisesRegex(ValueError, '解码'):
                self.pipeline._validate_original(original, {'duration': 100})
        self.assertFalse(self.pipeline._verified_originals)

    def test_bad_legacy_source_is_quarantined_and_failed_attempt_not_reused(self):
        original = self.root / 'original' / 'test_same.mp4'
        original.parent.mkdir()
        original.write_bytes(b'bad')
        self.pipeline._validate_original = Mock(side_effect=ValueError('damaged'))
        with patch('qt_tool.media._command_exists', return_value=True), patch('qt_tool.media._run_download', return_value=SimpleNamespace(returncode=1, stderr='ERROR: merge failure')):
            with self.assertRaisesRegex(RuntimeError, '诊断日志'):
                self.pipeline.download_final(self.sid)
        self.assertFalse(original.exists())
        self.assertEqual(list((self.root / 'original' / 'quarantine').glob('*/test_same.mp4'))[0].read_bytes(), b'bad')
        self.assertIsNone(self.db.get_source(self.sid)['original_path'])
        self.assertTrue(list((self.root / 'original').glob('source_*/*/download.log')))

    def test_download_selection_excludes_incomplete_files_and_logs(self):
        for name in ('a.mp4.part', 'a.mp4.ytdl', 'a.f401.mp4', 'a.log'):
            (self.root / name).write_bytes(b'incomplete')
        with self.assertRaises(FileNotFoundError):
            self.pipeline._find_download(self.root, 'a.*')

    def test_verified_file_reused_but_modified_file_rechecked(self):
        original = self.root / 'original' / 'good.mp4'
        original.parent.mkdir()
        original.write_bytes(b'complete')
        self.pipeline.probe = Mock(return_value={'playable': True, 'has_audio': True, 'duration': 100,
                                                 'video_duration': 100, 'audio_duration': 100})
        with patch('qt_tool.media._run', return_value=SimpleNamespace(returncode=0, stderr='')) as decode:
            self.pipeline._validate_original(original, {'duration': 100})
            self.pipeline._validate_original(original, {'duration': 100})
            self.assertEqual(decode.call_count, 1)
            original.write_bytes(b'changed file size')
            self.pipeline._validate_original(original, {'duration': 100})
            self.assertEqual(decode.call_count, 2)

    def test_same_candidate_cannot_be_finalized_twice_at_once(self):
        lock = self.pipeline._final_candidate_locks.setdefault(123, __import__('threading').Lock())
        with lock:
            with self.assertRaisesRegex(ValueError, '请勿重复提交'):
                self.pipeline.final_qa_and_deliver(123)

    def test_av1_hardware_validation_falls_back_without_skipping_checks(self):
        original = self.root / 'original' / 'good.mp4'
        original.parent.mkdir()
        original.write_bytes(b'complete')
        self.pipeline.probe = Mock(return_value={'playable': True, 'has_audio': True, 'duration': 100,
            'video_duration': 100, 'audio_duration': 100, 'video_codec': 'av1'})
        with patch('qt_tool.media._run', side_effect=[SimpleNamespace(returncode=1, stderr='no cuda'),
                SimpleNamespace(returncode=0, stderr='')]) as decode:
            self.pipeline._validate_original(original, {'duration': 100})
        self.assertEqual(decode.call_count, 2)
        self.assertIn('av1_cuvid', decode.call_args_list[0].args[0])
        self.assertNotIn('av1_cuvid', decode.call_args_list[1].args[0])
        self.assertTrue(self.pipeline._verified_originals)

    def test_isolated_originals_cannot_collide_in_shared_clip_directory(self):
        self.db.candidate_part_number = Mock(return_value=1)
        first = self.pipeline._clip_path({'id': 10, 'source_id': 1, 'original_path': 'original/source_1/a/source.mp4'})
        second = self.pipeline._clip_path({'id': 11, 'source_id': 2, 'original_path': 'original/source_2/b/source.mp4'})
        self.assertNotEqual(first, second)
        self.assertEqual(first.name, 'source_1-1.mp4')

    def test_clip_bounds_decoder_and_encoder_threads_without_lowering_quality(self):
        original = self.root / 'original' / 'source.mp4'
        self.pipeline.download_final = Mock(return_value=original)
        self.pipeline.db.get_candidate = Mock(return_value={
            'id': 1, 'source_id': self.sid, 'status': 'ACCEPTED', 'start_time': 16.147, 'duration': 5.0,
        })
        self.pipeline._clip_path = Mock(return_value=self.root / 'clips' / 'output.mp4')
        with patch('qt_tool.media._command_exists', return_value=True), patch(
            'qt_tool.media._run', return_value=SimpleNamespace(returncode=0, stderr='')
        ) as run:
            self.pipeline.clip_final(1)
        args = run.call_args.args[0]
        split = args.index('-i')
        self.assertEqual(args[:split][-2:], ['-threads', '4'])
        output_args = args[split + 2:]
        self.assertEqual(output_args[output_args.index('-threads') + 1], '4')
        self.assertEqual(output_args[output_args.index('-crf') + 1], '17')
        self.assertNotIn('-r', args)
        self.assertNotIn('-vf', args)


if __name__ == '__main__':
    unittest.main()
