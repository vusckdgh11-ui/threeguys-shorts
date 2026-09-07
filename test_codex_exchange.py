import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

import codex_exchange as exchange


class CodexExchangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.video = self.root / "source.avi"
        writer = cv2.VideoWriter(str(self.video), cv2.VideoWriter_fourcc(*"MJPG"), 5, (64, 48))
        for value in range(20):
            writer.write(np.full((48, 64, 3), value * 10, np.uint8))
        writer.release()

    def tearDown(self):
        self.temp.cleanup()

    def make_result(self):
        job, prompt = exchange.export_job([str(self.video)], 40, "확인된 현장 설명", "스토리형", 1.0, self.root / "jobs")
        request = json.loads((Path(job) / "analysis-request.json").read_text(encoding="utf-8"))
        result = {"exchange_version": 1, "job_id": request["job_id"], "cuts": [{
            "source_id": "source_001", "start": 0.0, "end": 2.0,
            "description": "밝기가 변하는 화면", "selection_reason": "변화가 보임",
            "visual_evidence": "0초와 2초 이미지", "narration": "현장의 변화가 시작됩니다."
        }]}
        result_path = Path(job) / "analysis-result.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return Path(job), result_path, prompt

    def test_export_and_valid_import(self):
        job, result_path, prompt = self.make_result()
        self.assertIn(str(job), prompt)
        self.assertGreater(len(list((job / "frames").rglob("*.jpg"))), 1)
        self.assertGreater(len(list((job / "contact_sheets").rglob("*.jpg"))), 0)
        request, cuts = exchange.import_result(result_path)
        self.assertEqual(request["audio_analyzed"], False)
        self.assertEqual(cuts[0]["line"], "현장의 변화가 시작됩니다.")

    def test_rejects_wrong_job_and_overlap(self):
        _, result_path, _ = self.make_result()
        data = json.loads(result_path.read_text(encoding="utf-8"))
        data["job_id"] = "wrong"
        result_path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(exchange.ExchangeError):
            exchange.import_result(result_path)

        job, result_path, _ = self.make_result()
        data = json.loads(result_path.read_text(encoding="utf-8"))
        data["cuts"].append({**data["cuts"][0], "start": 1.0, "end": 3.0})
        result_path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(exchange.ExchangeError):
            exchange.import_result(result_path)


if __name__ == "__main__":
    unittest.main()
