"""Create and validate offline exchange folders for Codex-assisted editing."""
import hashlib
import json
import math
import os
import time
import uuid
from pathlib import Path

import cv2
import numpy as np


VERSION = 1
MAX_FRAMES = 1200


class ExchangeError(RuntimeError):
    pass


def _fingerprint(path):
    path = Path(path)
    size = path.stat().st_size
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))
    with path.open("rb") as source:
        digest.update(source.read(1024 * 1024))
        if size > 1024 * 1024:
            source.seek(max(0, size - 1024 * 1024))
            digest.update(source.read(1024 * 1024))
    return digest.hexdigest()


def _video_info(path):
    path = Path(path).resolve()
    capture = cv2.VideoCapture(str(path))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = frames / fps if fps > 0 else 0
        if not math.isfinite(duration) or duration < 0.3:
            raise ExchangeError(f"영상을 읽을 수 없습니다: {path.name}")
    finally:
        capture.release()
    stat = path.stat()
    return {
        "path": str(path), "name": path.name, "duration": round(duration, 4),
        "fps": round(fps, 4),
        "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
        "fingerprint": _fingerprint(path),
    }


def _write_frame(capture, timestamp, destination):
    capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
    ok, frame = capture.read()
    if not ok:
        raise ExchangeError(f"{timestamp:.2f}초 프레임을 읽지 못했습니다.")
    height, width = frame.shape[:2]
    scale = min(1, 768 / max(height, width))
    if scale < 1:
        frame = cv2.resize(frame, (max(2, round(width * scale)), max(2, round(height * scale))))
    cv2.putText(frame, f"{timestamp:.3f}s", (12, 34), cv2.FONT_HERSHEY_SIMPLEX,
                0.8, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(frame, f"{timestamp:.3f}s", (12, 34), cv2.FONT_HERSHEY_SIMPLEX,
                0.8, (255, 255, 255), 2, cv2.LINE_AA)
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise ExchangeError("분석용 장면 이미지를 저장하지 못했습니다.")
    destination.write_bytes(encoded.tobytes())


def _write_contact_sheets(job, source_id, frame_items):
    sheet_dir = job / "contact_sheets" / source_id
    sheet_dir.mkdir(parents=True)
    relative_sheets = []
    cell_width, cell_height, columns = 256, 144, 4
    for group_index in range(0, len(frame_items), 12):
        group = frame_items[group_index:group_index + 12]
        rows = math.ceil(len(group) / columns)
        canvas = np.full((rows * cell_height, columns * cell_width, 3), 24, np.uint8)
        for index, item in enumerate(group):
            encoded = np.frombuffer((job / item["file"]).read_bytes(), dtype=np.uint8)
            frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
            if frame is None:
                raise ExchangeError("장면 모음판을 만들 이미지가 손상됐습니다.")
            height, width = frame.shape[:2]
            scale = min(cell_width / width, cell_height / height)
            resized = cv2.resize(frame, (max(1, round(width * scale)), max(1, round(height * scale))))
            row, column = divmod(index, columns)
            x = column * cell_width + (cell_width - resized.shape[1]) // 2
            y = row * cell_height + (cell_height - resized.shape[0]) // 2
            canvas[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
        relative = Path("contact_sheets") / source_id / f"sheet_{group_index // 12:04d}.jpg"
        ok, encoded = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 84])
        if not ok:
            raise ExchangeError("장면 모음판을 저장하지 못했습니다.")
        (job / relative).write_bytes(encoded.tobytes())
        relative_sheets.append(relative.as_posix())
    return relative_sheets


def export_job(paths, target, brief, style, step, root, progress=None):
    if step not in (0.5, 1.0, 2.0):
        raise ExchangeError("지원하지 않는 분석 간격입니다.")
    sources = [_video_info(path) for path in paths]
    frame_count = sum(math.ceil(source["duration"] / step) + 1 for source in sources)
    if frame_count > MAX_FRAMES:
        raise ExchangeError(f"분석 이미지 {frame_count}장이 한도 {MAX_FRAMES}장을 넘습니다. 분석 간격을 늘리거나 영상을 나눠주세요.")

    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    job = Path(root).resolve() / job_id
    job.mkdir(parents=True, exist_ok=False)
    manifest_sources = []
    completed = 0
    for index, source in enumerate(sources, 1):
        source_id = f"source_{index:03d}"
        frame_dir = job / "frames" / source_id
        frame_dir.mkdir(parents=True)
        last = max(0, source["duration"] - max(0.08, 1 / source["fps"]))
        stamps = [round(i * step, 3) for i in range(math.ceil(last / step))]
        stamps = sorted(set(stamps + [round(last, 3)]))
        capture = cv2.VideoCapture(source["path"])
        frame_items = []
        try:
            for frame_index, timestamp in enumerate(stamps):
                relative = Path("frames") / source_id / f"frame_{frame_index:05d}_{round(timestamp * 1000):010d}ms.jpg"
                _write_frame(capture, timestamp, job / relative)
                frame_items.append({"timestamp": timestamp, "file": relative.as_posix()})
                completed += 1
                if progress:
                    progress(round(100 * completed / frame_count), f"분석 장면 준비 {completed}/{frame_count}")
        finally:
            capture.release()
        contact_sheets = _write_contact_sheets(job, source_id, frame_items)
        manifest_sources.append({"source_id": source_id, **source, "frames": frame_items,
                                 "contact_sheets": contact_sheets})

    request = {
        "exchange_version": VERSION, "job_id": job_id, "target_seconds": int(target),
        "field_notes": brief, "narration_style": style, "sample_interval_seconds": step,
        "audio_analyzed": False, "sources": manifest_sources,
    }
    (job / "analysis-request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    prompt = f"""다음 폴더의 analysis-request.json, contact_sheets와 frames 이미지를 직접 확인해 영상 편집안을 만들어줘.

폴더: {job}

contact_sheets로 전체 흐름을 먼저 훑고, 사용할 구간은 frames의 개별 이미지를 다시 확인해. 영상 업로드 순서가 아니라 장면의 의미에 맞춰 재구성하고, 현장 설명은 확인된 맥락으로만 사용해. 보이지 않거나 적히지 않은 사실은 만들지 마. 각 컷은 실제 source_id와 타임스탬프 범위 안에서 1.2초 이상이어야 하고, 같은 원본 구간이 겹치면 안 돼. 전체 선택 길이는 {target}초를 넘기지 마. 각 컷마다 자연스러운 한국어 대본 한 줄을 만들고 현장 설명을 그대로 복사하거나 일반적인 업체 광고 문구를 반복하지 마. 원본 음성은 분석 자료에 없다는 점을 지켜줘.

완성 결과를 반드시 이 폴더의 analysis-result.json에 저장해. JSON 형식은 다음과 같아.
{{
  "exchange_version": 1,
  "job_id": "{job_id}",
  "cuts": [
    {{
      "source_id": "source_001",
      "start": 0.0,
      "end": 2.5,
      "description": "화면에서 실제로 보이는 장면 설명",
      "selection_reason": "이 장면을 이 순서에 배치한 이유",
      "visual_evidence": "확인한 이미지 파일과 시각 근거",
      "narration": "이 컷에 맞는 한국어 대본 한 줄"
    }}
  ]
}}
"""
    (job / "코덱스에 붙여넣을 문장.txt").write_text(prompt, encoding="utf-8")
    return str(job), prompt


def import_result(result_path):
    result_path = Path(result_path).resolve()
    request_path = result_path.parent / "analysis-request.json"
    if result_path.name != "analysis-result.json" or not request_path.is_file():
        raise ExchangeError("분석자료 폴더 안의 analysis-result.json을 선택하세요.")
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ExchangeError("Codex 분석 결과 JSON을 읽을 수 없습니다.") from None
    if result.get("exchange_version") != VERSION or result.get("job_id") != request.get("job_id"):
        raise ExchangeError("선택한 결과가 이 분석자료 작업과 일치하지 않습니다.")
    sources = {source["source_id"]: source for source in request.get("sources", [])}
    cuts = result.get("cuts")
    if not isinstance(cuts, list) or not cuts or len(cuts) > 80:
        raise ExchangeError("분석 결과의 컷 목록이 비어 있거나 너무 많습니다.")
    validated = []
    total = 0.0
    intervals = []
    required = ("description", "selection_reason", "visual_evidence", "narration")
    for cut in cuts:
        if not isinstance(cut, dict) or cut.get("source_id") not in sources:
            raise ExchangeError("분석 결과에 존재하지 않는 원본 영상이 있습니다.")
        source = sources[cut["source_id"]]
        path = Path(source["path"])
        if not path.is_file() or _fingerprint(path) != source["fingerprint"]:
            raise ExchangeError(f"원본 영상이 바뀌었거나 없어졌습니다: {source['name']}")
        start, end = cut.get("start"), cut.get("end")
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            raise ExchangeError("컷의 시작/끝 시간이 숫자가 아닙니다.")
        start, end = float(start), float(end)
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= source["duration"] + 0.01 and end - start >= 1.2):
            raise ExchangeError(f"원본 범위를 벗어난 컷이 있습니다: {source['name']}")
        if any(source["source_id"] == sid and start < old_end and end > old_start for sid, old_start, old_end in intervals):
            raise ExchangeError("같은 원본 영상의 컷 범위가 서로 겹칩니다.")
        values = []
        for field in required:
            value = cut.get(field)
            if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
                raise ExchangeError(f"컷의 {field} 내용이 비어 있거나 형식이 잘못됐습니다.")
            values.append(value.strip())
        if len(values[3]) > 120:
            raise ExchangeError("한 컷의 대본이 120자를 넘습니다.")
        total += end - start
        if total > float(request["target_seconds"]) + 0.01:
            raise ExchangeError("분석 결과가 선택한 목표 길이를 초과했습니다.")
        intervals.append((source["source_id"], start, end))
        validated.append({"path": str(path), "start": start, "end": end,
                          "description": values[0], "reason": values[1],
                          "evidence": values[2], "line": values[3]})
    return request, validated
