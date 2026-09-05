import unittest
from unittest.mock import patch
import app


class TimelineTests(unittest.TestCase):
    def test_targets_never_overshoot(self):
        for target in (40,50,60):
            candidates=[app.Segment('clip',i*4.2,(i+1)*4.2,100-i) for i in range(20)]
            with patch.object(app,'candidate_segments',return_value=candidates):
                selected=app.choose_timeline(['clip'],target)
            self.assertAlmostEqual(sum(s.source_duration for s in selected),target)

    def test_insufficient_footage_is_not_fabricated(self):
        with patch.object(app,'candidate_segments',return_value=[app.Segment('clip',0,3,100)]):
            selected=app.choose_timeline(['clip'],60)
        self.assertEqual(sum(s.source_duration for s in selected),3)

    def test_one_line_per_cut(self):
        cuts=[app.Segment('clip',0,2,100) for _ in range(20)]
        self.assertEqual(len(app.make_script('현장',cuts)),20)

    def test_no_silent_typecast_truncation(self):
        with self.assertRaises(ValueError):
            app.synthesize_typecast('가'*2001,'unused.wav','','')

    def test_sapi_in_worker_thread(self):
        if app.os.name != 'nt': self.skipTest('Windows only')
        import tempfile
        from concurrent.futures import ThreadPoolExecutor
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            output=Path(d)/'sapi.wav'
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(app.synthesize_sapi,'Windows voice test.',output).result(timeout=30)
            self.assertGreater(app.wav_duration(output),0.1)


if __name__ == '__main__': unittest.main()
