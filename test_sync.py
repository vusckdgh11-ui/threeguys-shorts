import unittest
from unittest.mock import patch
import app


class TimelineTests(unittest.TestCase):



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
