import os, sys, re, math, wave, tempfile, subprocess, traceback, requests, hashlib, base64, shutil
from pathlib import Path
from dataclasses import dataclass, replace
from typing import List

import cv2
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSettings, QPointF, QRectF
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QListWidget, QFileDialog, QLabel, QLineEdit, QComboBox, QSpinBox, QTextEdit,
    QTableWidget, QTableWidgetItem, QMessageBox, QProgressBar, QGroupBox,
    QFormLayout, QCheckBox, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsTextItem, QSlider, QSplitter, QScrollArea, QAbstractItemView
)
from PySide6.QtGui import QImage, QPixmap, QFont, QColor, QPen, QBrush, QPainter, QFontDatabase

APP_NAME = "ThreeGuys Shorts"
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


def load_fonts():
    font=Path(os.environ.get('WINDIR','C:/Windows')) / 'Fonts' / 'malgun.ttf'
    if font.is_file():
        QFontDatabase.addApplicationFont(str(font))
        QApplication.instance().setFont(QFont('Malgun Gothic',9))


@dataclass
class Segment:
    path: str
    start: float
    end: float
    score: float
    line: str = ""
    play_duration: float = 0.0
    voice_duration: float = 0.0

    @property
    def source_duration(self):
        return max(0.05, self.end - self.start)

    @property
    def duration(self):
        return self.play_duration if self.play_duration > 0 else self.source_duration


def natural_key(p):
    s = os.path.basename(p)
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", s)]


def ffmpeg_exe():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def ffpath(p):
    return str(Path(p).resolve())


def get_video_duration(path):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    return frames / fps if frames > 0 and fps > 0 else 0.0


def frame_score(prev, frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    sharp = cv2.Laplacian(gray, cv2.CV_64F).var()
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    sat = float(np.mean(hsv[:, :, 1]))
    bright = float(np.mean(gray))
    contrast = float(np.std(gray))
    motion = 0.0
    if prev is not None:
        pg = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
        motion = float(np.mean(cv2.absdiff(pg, gray)))
    exposure_penalty = 0.35 if bright < 18 or bright > 238 else 1.0
    return exposure_penalty * (0.32 * min(sharp, 700) + 2.3 * motion + 0.35 * sat + 0.5 * contrast)


def candidate_segments(path, seg_len=4.0, sample_step=1.0):
    dur = get_video_duration(path)
    if dur <= 0.5:
        return []
    cap = cv2.VideoCapture(path)
    prev, samples, t = None, [], 0.0
    while t < dur:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok:
            t += sample_step
            continue
        h, w = frame.shape[:2]
        scale = 360.0 / max(h, w)
        if scale < 1:
            frame = cv2.resize(frame, (max(2, int(w * scale)), max(2, int(h * scale))))
        samples.append((t, frame_score(prev, frame)))
        prev = frame
        t += sample_step
    cap.release()
    samples.sort(key=lambda x: x[1], reverse=True)
    out, occupied = [], []
    for t, sc in samples:
        start = max(0.0, min(t - seg_len / 2, max(0.0, dur - seg_len)))
        end = min(dur, start + seg_len)
        if end - start < 1.4:
            continue
        if sc < 8 or any(start < b and end > a for a, b in occupied):
            continue
        occupied.append((start, end))
        out.append(Segment(path, start, end, sc))
        if len(out) >= max(2, int(dur // 7) + 1):
            break
    return out


def choose_timeline(paths, target):
    paths = sorted(paths, key=natural_key)
    allc = []
    for i, p in enumerate(paths):
        for c in candidate_segments(p, seg_len=3.8 if target <= 40 else (4.2 if target <= 50 else 4.6)):
            allc.append((i, c))
    if not allc:
        return []

    per_clip = {}
    for i, c in allc:
        per_clip.setdefault(i, []).append(c)
    picked = []
    for i in range(len(paths)):
        vals = sorted(per_clip.get(i, []), key=lambda s: s.score, reverse=True)
        if vals:
            picked.append((i, vals[0]))

    seen = {(i, round(c.start, 2)) for i, c in picked}
    total = sum(c.source_duration for _, c in picked)
    for i, c in sorted(allc, key=lambda x: x[1].score, reverse=True):
        if total >= target:
            break
        key = (i, round(c.start, 2))
        if key in seen:
            continue
        picked.append((i, c)); seen.add(key); total += c.source_duration

    picked.sort(key=lambda x: (x[0], x[1].start))
    result, acc = [], 0.0
    for _, c in picked:
        if acc >= target:
            break
        left = target - acc
        if c.source_duration > left:
            c.end = c.start + left
        result.append(c)
        acc += c.source_duration
    return result


def make_script(description: str, segs: List[Segment], style="자극적"):
    """Timeline-first script. One compact line per selected cut."""
    desc = description.strip() or "특수청소 현장"
    banks = {
        "강한 자극형": [
            f"{desc}. 문을 열자마자 분위기가 심상치 않았습니다.",
            "겉으로 보이는 것보다 상황은 더 깊었습니다.",
            "보이는 흔적을 따라 오염 범위를 먼저 확인합니다.",
            "놓치기 쉬운 틈까지 확인하자 흔적이 이어집니다.",
            "이제 전용 약품으로 남은 오염을 반응시켜 봅니다.",
            "한 번 닦았다고 끝? 반응이 남으면 다시 처리합니다.",
            "가구 주변과 모서리까지 끝까지 확인합니다.",
            "대충 넘기면 냄새와 흔적이 다시 남을 수 있습니다.",
            "마지막으로 남은 오염이 없는지 다시 점검합니다.",
            "이런 곳까지 누가 청소하냐고요? 저희가 합니다.",
            "쓰리가이즈 특수청소. 24시간 상담 가능합니다."
        ],
        "자극적": [
            f"{desc}. 현장에 들어가자마자 심상치 않은 흔적이 보였습니다.",
            "그런데 눈에 보이는 게 전부가 아니었습니다.",
            "오염이 어디까지 번졌는지 하나씩 확인합니다.",
            "가구 주변과 틈새까지 따라가며 범위를 찾습니다.",
            "전용 약품을 뿌려 남아 있는 오염을 확인합니다.",
            "반응이 남는 부분은 다시 처리합니다.",
            "놓치기 쉬운 부분까지 반복해서 확인합니다.",
            "대충 닦고 끝내면 냄새와 흔적이 남을 수 있습니다.",
            "끝까지 확인한 뒤에야 작업을 마무리합니다.",
            "이런 곳까지 누가 청소하냐고요? 저희가 합니다.",
            "쓰리가이즈 특수청소. 24시간 상담 가능합니다."
        ],
        "스토리형": [
            f"이번 의뢰는 {desc}였습니다.",
            "현장에 도착해 먼저 전체 상태부터 살펴봤습니다.",
            "보이는 흔적을 따라 안쪽 상황도 확인합니다.",
            "가구와 주변을 따라 오염 범위를 찾아갑니다.",
            "이제 전용 약품으로 남아 있는 오염을 확인합니다.",
            "반응이 나타나는 곳은 반복해서 처리합니다.",
            "작은 틈과 모서리도 다시 살펴봅니다.",
            "처음보다 현장이 조금씩 정리되기 시작합니다.",
            "마지막으로 남은 부분이 없는지 점검합니다.",
            "이렇게 현장 하나를 마무리했습니다.",
            "쓰리가이즈 특수청소. 필요한 순간 연락주세요."
        ],
        "정보형": [
            f"{desc}. 이런 현장은 오염 범위 확인이 먼저입니다.",
            "겉으로 보이는 부분만 닦아서는 부족할 수 있습니다.",
            "주변과 틈새까지 오염이 번졌는지 확인합니다.",
            "상태에 맞는 전용 약품과 작업 방법을 선택합니다.",
            "약품 반응으로 남아 있는 오염을 확인합니다.",
            "필요한 부분은 반복 처리합니다.",
            "놓치기 쉬운 모서리도 다시 점검합니다.",
            "작업 후에는 전체 공간을 다시 확인합니다.",
            "특수청소는 제거뿐 아니라 확인 과정도 중요합니다.",
            "전문적인 처리가 필요한 이유입니다.",
            "쓰리가이즈 특수청소. 24시간 상담 가능합니다."
        ],
        "차분한 전문형": [
            f"{desc}. 현장 상태를 확인한 뒤 작업 범위를 정했습니다.",
            "먼저 눈에 보이는 오염과 주변 상태를 점검합니다.",
            "오염이 이어진 부분을 따라 범위를 확인합니다.",
            "가구 주변과 틈새도 빠짐없이 살펴봅니다.",
            "상태에 맞는 전용 약품으로 처리합니다.",
            "처리 후 반응을 확인하고 필요한 곳은 반복 작업합니다.",
            "잔여 오염이 없는지 세부 구간을 다시 점검합니다.",
            "현장에 맞는 절차로 차근차근 마무리합니다.",
            "마지막으로 전체 상태를 확인합니다.",
            "안전하고 꼼꼼한 처리가 필요한 현장이었습니다.",
            "쓰리가이즈 특수청소. 24시간 상담 가능합니다."
        ]
    }
    templates = banks.get(style, banks["자극적"])
    n = len(segs)
    lines = []
    for i in range(n):
        if i == 0:
            line = templates[0]
        elif i == n - 1:
            line = templates[-1]
        else:
            # map middle cuts across the middle script bank instead of simple cycling
            mid = templates[1:-1]
            pos = (i - 1) / max(1, n - 2)
            idx = min(len(mid) - 1, int(round(pos * (len(mid) - 1))))
            line = mid[idx]
        lines.append(line)
    return lines


# ---------------- TTS providers ----------------
def typecast_voices(api_key):
    r = requests.get("https://api.typecast.ai/v2/voices", params={"model":"ssfm-v30"}, headers={"X-API-KEY":api_key}, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else data.get("voices", data.get("data", []))


def synthesize_typecast(text, out_wav, api_key, voice_id, tempo=1.0):
    if len(text) > 2000:
        raise ValueError("Typecast 대본은 한 컷당 2000자 이하로 줄여주세요.")
    payload = {
        "voice_id": voice_id,
        "text": text,
        "model": "ssfm-v30",
        "language": "kor",
        "prompt": {"emotion_type": "smart"},
        "output": {"volume": 100, "audio_pitch": 0, "audio_tempo": float(tempo), "audio_format": "wav"}
    }
    r = requests.post("https://api.typecast.ai/v1/text-to-speech", headers={"X-API-KEY":api_key,"Content-Type":"application/json"}, json=payload, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"Typecast TTS 오류 ({r.status_code}). API 키, 잔여 사용량, 음성을 확인하세요.")
    Path(out_wav).write_bytes(r.content)


def sapi_voices():
    if os.name != "nt":
        return []
    import win32com.client
    voice = win32com.client.Dispatch("SAPI.SpVoice")
    tokens = voice.GetVoices()
    return [(tokens.Item(i).GetDescription(), i) for i in range(tokens.Count)]


def synthesize_sapi(text, out_wav, voice_index=0, rate=0):
    import pythoncom
    pythoncom.CoInitialize()
    try:
        _synthesize_sapi(text, out_wav, voice_index, rate)
    finally:
        pythoncom.CoUninitialize()


def _synthesize_sapi(text, out_wav, voice_index=0, rate=0):
    import win32com.client
    voice = win32com.client.Dispatch("SAPI.SpVoice")
    voices = voice.GetVoices()
    if voices.Count:
        voice.Voice = voices.Item(max(0, min(int(voice_index), voices.Count - 1)))
    voice.Rate = int(rate)
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Format.Type = 22
    stream.Open(str(out_wav), 3, False)
    voice.AudioOutputStream = stream
    try:
        voice.Speak(text, 16)  # Speak literal text, never interpret user text as XML.
    finally:
        stream.Close()
        voice.AudioOutputStream = None
        stream = voices = voice = None


def synthesize_google(text, out_wav, slow=False):
    from gtts import gTTS
    tmp_mp3 = str(Path(out_wav).with_suffix('.gtts.mp3'))
    gTTS(text=text, lang='ko', slow=bool(slow), timeout=(10, 60)).save(tmp_mp3)
    cmd = [ffmpeg_exe(), '-y', '-i', tmp_mp3, '-ac', '1', '-ar', '22050', '-c:a', 'pcm_s16le', str(out_wav)]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
    try: os.remove(tmp_mp3)
    except Exception: pass
    if p.returncode != 0:
        raise RuntimeError("Google TTS 변환 오류: " + p.stderr[-1500:])


def wav_duration(path):
    with wave.open(str(path), 'rb') as w:
        return w.getnframes() / float(w.getframerate())


def protect_secret(text):
    if not text or os.name != 'nt':
        return ''
    try:
        import win32crypt
        blob = win32crypt.CryptProtectData(text.encode('utf-8'), None, None, None, None, 0)
        return base64.b64encode(blob).decode('ascii')
    except Exception:
        return ''


def unprotect_secret(blob):
    if not blob or os.name != 'nt':
        return ''
    try:
        import win32crypt
        raw = base64.b64decode(blob.encode('ascii'))
        return win32crypt.CryptUnprotectData(raw, None, None, None, 0)[1].decode('utf-8')
    except Exception:
        return ''


def synthesize_line(provider, text, out_wav, voice_data, api_key, rate):
    if provider == "Typecast":
        if not api_key or not voice_data:
            raise RuntimeError("Typecast API 키와 음성을 선택하세요.")
        synthesize_typecast(text, out_wav, api_key, str(voice_data), max(0.7, min(1.3, 1.0 + rate * 0.05)))
    elif provider == "Google 무료(gTTS)":
        synthesize_google(text, out_wav, slow=(voice_data == 'slow'))
    else:
        synthesize_sapi(text, out_wav, int(voice_data or 0), rate)


# ---------------- subtitles ----------------
def esc_ass(s):
    return s.replace('\\', r'\\').replace('{', r'\{').replace('}', r'\}').replace('\n', r'\N')


def ass_time(sec):
    ticks = max(0, round(sec * 100))
    h, ticks = divmod(ticks, 360000)
    m, ticks = divmod(ticks, 6000)
    s, cs = divmod(ticks, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def emphasize_ass(text, style):
    text = esc_ass(text)
    if style not in ("핵심어 노랑 강조", "핵심어 빨강 강조"):
        return text
    color = "&H0000FFFF&" if style == "핵심어 노랑 강조" else "&H000000FF&"
    keywords = ["혈흔", "오염", "약품", "냄새", "반응", "24시간", "자살", "생존", "특수청소"]
    pattern = r"(\d+[가-힣A-Za-z%]*|" + "|".join(map(re.escape, keywords)) + r")"
    return re.sub(pattern, lambda m: "{\\c" + color + "}" + m.group(0) + "{\\c&H00FFFFFF&}", text)


def build_ass(path, segs, total, banner=True, caption_style="쇼츠 굵은 흰색+검정외곽선", cap_x=540, cap_y=1500, cap_size=74):
    if caption_style == "깔끔한 흰색":
        outline, back, border = 2, "&H30000000", 1
    elif caption_style == "검정 박스형":
        outline, back, border = 1, "&HC8000000", 3
    else:
        outline, back, border = 5, "&H60000000", 1
    header = f"""[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\nStyle: Caption,Malgun Gothic,{int(cap_size)},&H00FFFFFF,&H000000FF,&H00101010,{back},-1,0,0,0,100,100,0,0,{border},{outline},1,5,70,70,0,1\nStyle: Banner,Malgun Gothic,44,&H00FFFFFF,&H000000FF,&H00000000,&HA8000000,-1,0,0,0,100,100,0,0,3,2,0,8,40,40,55,1\nStyle: Brand,Malgun Gothic,36,&H00FFFFFF,&H000000FF,&H00000000,&H7F000000,-1,0,0,0,100,100,0,0,3,1,0,8,40,40,126,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"""
    events = []
    if banner:
        events.append(f"Dialogue: 0,{ass_time(0)},{ass_time(total)},Banner,,0,0,0,,고독사 | 혈흔 | 쓰레기집 | 특수청소 문의")
        events.append(f"Dialogue: 0,{ass_time(0)},{ass_time(total)},Brand,,0,0,0,,쓰리가이즈 특수청소  ·  24시간 상담  ·  서울·경기 수도권")
    t = 0.0
    for s in segs:
        end = t + s.duration
        txt = emphasize_ass(s.line, caption_style)
        events.append(f"Dialogue: 0,{ass_time(t)},{ass_time(end)},Caption,,0,0,0,,{{\\an5\\pos({int(cap_x)},{int(cap_y)})}}{txt}")
        t = end
    Path(path).write_text(header + "\n".join(events), encoding='utf-8-sig')


# ---------------- rendering ----------------
def run_ffmpeg(cmd, cwd=None):
    result = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError("FFmpeg 오류:\n" + result.stderr[-6000:])
    return result


def render_video(segs: List[Segment], out_mp4, provider, voice_data, api_key, voice_rate,
                 logo_path="", logo_w=300, logo_x=740, logo_y=220, effects=True, banner=True,
                 caption_style="쇼츠 굵은 흰색+검정외곽선", cap_x=540, cap_y=1500, cap_size=74, progress=None):
    if not segs or any(not s.line.strip() for s in segs):
        raise ValueError("각 컷에 대본 한 줄을 입력하세요.")
    destination = Path(out_mp4).resolve()
    if destination in [Path(s.path).resolve() for s in segs] or (
            logo_path and destination == Path(logo_path).resolve()):
        raise ValueError("원본 영상이나 로고 파일을 출력 파일로 덮어쓸 수 없습니다.")
    for s in segs:
        duration = get_video_duration(s.path)
        if not (math.isfinite(s.start) and math.isfinite(s.end) and
                0 <= s.start < s.end <= duration + 0.05):
            raise ValueError(f"원본 범위를 벗어난 컷: {Path(s.path).name}")
    # Render to a sibling temporary file; replace the destination only after validation.
    with tempfile.TemporaryDirectory(prefix="threeguys_", dir=destination.parent) as directory:
        tmp = Path(directory)
        ff = ffmpeg_exe()
        font=Path(os.environ.get('WINDIR','C:/Windows')) / 'Fonts' / 'malgun.ttf'
        if font.is_file():
            (tmp / 'fonts').mkdir()
            shutil.copyfile(font,tmp / 'fonts' / 'malgun.ttf')
        audio_files = []
        for i, s in enumerate(segs):
            wav = tmp / f"voice_{i:03d}.wav"
            synthesize_line(provider, s.line, wav, voice_data, api_key, voice_rate)
            vd = wav_duration(wav)
            if not math.isfinite(vd) or vd <= 0:
                raise ValueError(f"{i + 1}번 TTS에 음성이 없습니다.")
            s.voice_duration = vd
            # Video and audio share exact 30 fps boundaries, avoiding cumulative drift.
            s.play_duration = math.ceil(max(1.4, vd + 0.18) * 30) / 30
            audio_files.append(wav)
            if progress: progress(5 + int(25 * (i + 1) / len(segs)), f"TTS {i+1}/{len(segs)}")

        total = sum(s.duration for s in segs)
        build_ass(tmp / "captions.ass", segs, total, banner, caption_style, cap_x, cap_y, cap_size)
        # Process one source at a time to bound decoder memory and Windows command length.
        parts = []
        for i, s in enumerate(segs):
            part = tmp / f"cut_{i:03d}.mp4"
            ratio = s.duration / s.source_duration
            video = (f"[0:v]trim=duration={s.source_duration:.9f},"
                     f"setpts=(PTS-STARTPTS)*{ratio:.12f},"
                     "scale=1080:1920:force_original_aspect_ratio=increase,"
                     "crop=1080:1920,setsar=1,fps=30")
            if effects and i % 4 == 2:
                video += ",scale=1166:2074,crop=1080:1920"
            video += (f",tpad=stop_mode=clone:stop_duration={s.duration:.9f},"
                      f"trim=duration={s.duration:.9f},setpts=PTS-STARTPTS[v]")
            run_ffmpeg([ff, "-y", "-ss", f"{s.start:.9f}", "-t", f"{s.source_duration:.9f}",
                        "-i", ffpath(s.path), "-filter_complex_threads", "1",
                        "-filter_complex", video, "-map", "[v]", "-an",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "19",
                        "-pix_fmt", "yuv420p", "-threads", "2", "-t", f"{s.duration:.9f}", str(part)])
            parts.append(part)
            if progress: progress(30 + int(40 * (i + 1) / len(segs)), f"컷 동기화 {i+1}/{len(segs)}")

        # Normalize each narration to identical PCM, then concatenate without AAC priming gaps.
        voice_all = tmp / "narration.wav"
        with wave.open(str(voice_all), "wb") as joined:
            joined.setnchannels(1); joined.setsampwidth(2); joined.setframerate(48000)
            for i, (s, wav) in enumerate(zip(segs, audio_files)):
                padded = tmp / f"padded_{i:03d}.wav"
                run_ffmpeg([ff, "-y", "-i", str(wav), "-af",
                            f"apad,atrim=duration={s.duration:.9f},asetpts=PTS-STARTPTS",
                            "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(padded)])
                with wave.open(str(padded), "rb") as source:
                    joined.writeframes(source.readframes(source.getnframes()))
        (tmp / "cuts.txt").write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
        result_path = tmp / "finished.mp4"
        cmd = [ff, "-y", "-f", "concat", "-safe", "1", "-i", "cuts.txt", "-i", "narration.wav"]
        filters = "[0:v]subtitles=filename=captions.ass:fontsdir=fonts[vsub]"
        label = "[vsub]"
        if logo_path:
            if not Path(logo_path).is_file():
                raise ValueError("로고 파일을 찾을 수 없습니다.")
            cmd += ["-loop", "1", "-i", ffpath(logo_path)]
            filters += (f";[2:v]scale={int(logo_w)}:-1[logo];"
                        f"[vsub][logo]overlay={int(logo_x)}:{int(logo_y)}:"
                        "format=auto:eof_action=repeat[vout]")
            label = "[vout]"
        cmd += ["-filter_complex_threads", "1", "-filter_complex", filters,
                "-map", label, "-map", "1:a", "-c:v", "libx264", "-preset", "fast",
                "-crf", "19", "-pix_fmt", "yuv420p", "-threads", "2",
                "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.9f}",
                "-movflags", "+faststart", str(result_path)]
        if progress: progress(75, f"자막·로고 합성 중 — 최종 {total:.2f}초")
        run_ffmpeg(cmd, cwd=tmp)
        actual = get_video_duration(str(result_path))
        if abs(actual - total) > 0.1:
            raise RuntimeError(f"출력 길이 검증 실패: 예상 {total:.3f}초 / 실제 {actual:.3f}초")
        os.replace(result_path, destination)
        if progress: progress(100, f"완료 — {actual:.2f}초")



class AnalyzeWorker(QThread):
    done = Signal(object); failed = Signal(str); status = Signal(int, str)
    def __init__(self, paths, target):
        super().__init__(); self.paths = list(paths); self.target = target
    def run(self):
        try:
            self.status.emit(10, "영상 길이와 장면 변화 분석 중...")
            self.done.emit(choose_timeline(self.paths, self.target))
        except Exception:
            self.failed.emit(traceback.format_exc())


class RenderWorker(QThread):
    done = Signal(str); failed = Signal(str); status = Signal(int, str)
    def __init__(self, args):
        super().__init__(); self.args = args
    def run(self):
        try:
            render_video(*self.args, progress=lambda p,s:self.status.emit(p,s))
            self.done.emit(self.args[1])
        except Exception:
            self.failed.emit(traceback.format_exc())


class VoiceWorker(QThread):
    done = Signal(object)
    failed = Signal(str)
    def __init__(self, task):
        super().__init__(); self.task=task
    def run(self):
        try: self.done.emit(self.task())
        except Exception as exc: self.failed.emit(str(exc))
        finally: self.task=None


class ResizePixmapItem(QGraphicsPixmapItem):
    HANDLE = 32
    def __init__(self, pixmap, changed_cb=None):
        super().__init__(pixmap)
        self.changed_cb = changed_cb
        self.resize_corner = None
        self.old_scene_rect = None
        self.orig_w = max(1, pixmap.width())
        self.setFlags(QGraphicsPixmapItem.ItemIsMovable | QGraphicsPixmapItem.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setZValue(20)

    def _corner_at(self, p):
        r = self.boundingRect(); h = self.HANDLE / max(0.02, self.scale())
        tests = {
            'tl': QPointF(r.left(), r.top()), 'tr': QPointF(r.right(), r.top()),
            'bl': QPointF(r.left(), r.bottom()), 'br': QPointF(r.right(), r.bottom())
        }
        for name, pt in tests.items():
            if abs(p.x()-pt.x()) <= h and abs(p.y()-pt.y()) <= h:
                return name
        return None

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        if self.isSelected():
            painter.setPen(QPen(QColor('#00aaff'), 5 / max(0.1, self.scale())))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.boundingRect())
            painter.setBrush(QBrush(QColor('#ffffff')))
            painter.setPen(QPen(QColor('#00aaff'), 3 / max(0.1, self.scale())))
            r = self.boundingRect(); s = 24 / max(0.1, self.scale())
            for x,y in [(r.left(),r.top()),(r.right(),r.top()),(r.left(),r.bottom()),(r.right(),r.bottom())]:
                painter.drawRect(x-s/2,y-s/2,s,s)

    def mousePressEvent(self, event):
        c = self._corner_at(event.pos())
        if c:
            self.setSelected(True)
            self.resize_corner = c
            self.old_scene_rect = self.mapRectToScene(QRectF(self.pixmap().rect()))
            event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.resize_corner or self.old_scene_rect is None:
            super().mouseMoveEvent(event); return
        r = self.old_scene_rect; sp = event.scenePos()
        if self.resize_corner in ('br','tr'):
            new_w = max(30.0, sp.x() - r.left())
            new_x = r.left()
        else:
            new_w = max(30.0, r.right() - sp.x())
            new_x = r.right() - new_w
        vertical = sp.y() - r.top() if self.resize_corner in ('br','bl') else r.bottom() - sp.y()
        new_w = max(30.0, (new_w + max(1.0, vertical) * r.width() / r.height()) / 2)
        new_scale = max(0.02, min(10000 / self.orig_w, new_w / self.orig_w))
        new_w = self.orig_w * new_scale
        new_x = r.left() if self.resize_corner in ('br','tr') else r.right() - new_w
        self.setScale(new_scale)
        new_h = self.pixmap().height() * new_scale
        if self.resize_corner in ('tl','tr'):
            new_y = r.bottom() - new_h
        else:
            new_y = r.top()
        self.setPos(new_x, new_y)
        if self.changed_cb: self.changed_cb()
        event.accept()

    def mouseReleaseEvent(self, event):
        self.resize_corner = None; self.old_scene_rect = None
        super().mouseReleaseEvent(event)
        if self.changed_cb: self.changed_cb()


class CaptionItem(QGraphicsTextItem):
    def __init__(self, changed_cb=None):
        super().__init__(); self.changed_cb = changed_cb
        self.setFlags(QGraphicsTextItem.ItemIsMovable | QGraphicsTextItem.ItemIsSelectable)
        self.setDefaultTextColor(QColor('white'))
        self.setFont(QFont('Malgun Gothic', 74, QFont.Bold))
        self.setTextWidth(900)
        self.setHtml("<div align='center'>자막 미리보기</div>")
        self.setZValue(30)
    def wheelEvent(self, event):
        factor = 1.08 if event.delta() > 0 else 0.92
        self.setScale(max(0.35, min(3.5, self.scale()*factor)))
        if self.changed_cb: self.changed_cb()
        event.accept()
    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self.changed_cb: self.changed_cb()


class PreviewView(QGraphicsView):
    logo_changed = Signal(int, int, int)
    caption_changed = Signal(int, int, int)
    def __init__(self):
        super().__init__()
        self.sc = QGraphicsScene(self); self.setScene(self.sc)
        self.setSceneRect(0,0,1080,1920)
        self.setMinimumSize(225, 400)
        self.setStyleSheet("background:#111;border:1px solid #555")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.bg = QGraphicsPixmapItem(); self.sc.addItem(self.bg)
        self.banner1 = QGraphicsTextItem("고독사 | 혈흔 | 쓰레기집 | 특수청소 문의")
        self.banner1.setDefaultTextColor(QColor('white')); self.banner1.setFont(QFont('Malgun Gothic',34,QFont.Bold)); self.banner1.setPos(55,45); self.banner1.setZValue(10); self.sc.addItem(self.banner1)
        self.banner2 = QGraphicsTextItem("쓰리가이즈 특수청소 · 24시간 상담 · 서울·경기 수도권")
        self.banner2.setDefaultTextColor(QColor('white')); self.banner2.setFont(QFont('Malgun Gothic',28,QFont.Bold)); self.banner2.setPos(55,110); self.banner2.setZValue(10); self.sc.addItem(self.banner2)
        self.caption = CaptionItem(self.emit_caption); self.caption.setPos(90,1390); self.sc.addItem(self.caption)
        self.logo_item = None
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def resizeEvent(self, e):
        super().resizeEvent(e); self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def set_frame(self, frame):
        if frame is None: return
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB); h,w = frame.shape[:2]
        q = QImage(frame.data,w,h,frame.strides[0],QImage.Format_RGB888).copy()
        pm = QPixmap.fromImage(q).scaled(1080,1920,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation)
        if pm.width()!=1080 or pm.height()!=1920:
            pm = pm.copy(max(0,(pm.width()-1080)//2), max(0,(pm.height()-1920)//2), 1080,1920)
        self.bg.setPixmap(pm); self.bg.setPos(0,0)

    def set_logo(self, path, width=320, x=720, y=210):
        if self.logo_item:
            self.sc.removeItem(self.logo_item); self.logo_item = None
        if not path: return
        pm = QPixmap(path)
        if pm.isNull(): return
        self.logo_item = ResizePixmapItem(pm, self.emit_logo)
        self.logo_item.setScale(max(0.02, width / max(1, pm.width())))
        self.logo_item.setPos(x,y); self.sc.addItem(self.logo_item)

    def emit_logo(self):
        if not self.logo_item: return
        rect = self.logo_item.mapRectToScene(QRectF(self.logo_item.pixmap().rect()))
        self.logo_changed.emit(int(rect.width()), int(rect.left()), int(rect.top()))

    def emit_caption(self):
        p = self.caption.pos(); size = int(74 * self.caption.scale())
        cx = int(p.x() + 450 * self.caption.scale())
        cy = int(p.y() + 90 * self.caption.scale())
        self.caption_changed.emit(cx, cy, size)

    def set_caption(self, text):
        safe = (text or "자막 미리보기").replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        self.caption.setHtml(f"<div align='center'>{safe}</div>")

    def set_caption_geometry(self, cx, cy, size):
        scale = max(0.35, min(3.5, size / 74.0))
        self.caption.setScale(scale)
        self.caption.setPos(cx - 450*scale, cy - 90*scale)

    def set_banner(self, on):
        self.banner1.setVisible(on); self.banner2.setVisible(on)


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        load_fonts()
        self.resize(1550, 930)
        self.setMinimumSize(1180, 760)
        self.paths=[]; self.segs=[]; self.logo=""; self.voices=[]
        self.settings=QSettings("ThreeGuys","ThreeGuysShorts")
        self.settings.remove("typecast_api_key")  # Remove plaintext left by pre-V3 versions.
        self.preview_timer=QTimer(self); self.preview_timer.timeout.connect(self.preview_next)
        self.cap_x, self.cap_y, self.cap_size = 540, 1500, 74

        root=QWidget(); self.setCentralWidget(root); outer=QVBoxLayout(root)
        title=QLabel("쓰리가이즈 쇼츠 자동제작"); title.setStyleSheet("font-size:24px;font-weight:700"); outer.addWidget(title)
        sub=QLabel("영상 분석 → 실제 컷 확정 → 컷별 대본 → 컷별 TTS → 음성 길이에 영상/자막 동기화 → MP4")
        sub.setStyleSheet("color:#666"); outer.addWidget(sub)
        limitation=QLabel("장면 선택은 밝기·선명도·움직임 기반 휴리스틱입니다. 의미 인식은 없으며 대본은 수정 가능한 템플릿 초안입니다.")
        limitation.setWordWrap(True); outer.addWidget(limitation)

        splitter=QSplitter(Qt.Horizontal); splitter.setChildrenCollapsible(False); outer.addWidget(splitter,1)

        # LEFT fixed settings in scroll area
        left_wrap=QWidget(); left=QVBoxLayout(left_wrap); left.setContentsMargins(6,6,6,6)
        left_scroll=QScrollArea(); left_scroll.setWidgetResizable(True); left_scroll.setWidget(left_wrap); left_scroll.setMinimumWidth(340); left_scroll.setMaximumWidth(390)
        splitter.addWidget(left_scroll)

        g1=QGroupBox("1) 원본 영상"); gl=QVBoxLayout(g1)
        self.list=QListWidget(); self.list.setMinimumHeight(150); gl.addWidget(self.list)
        br=QHBoxLayout(); add=QPushButton("영상 여러 개 추가"); add.clicked.connect(self.add_videos); br.addWidget(add); rem=QPushButton("선택 삭제"); rem.clicked.connect(self.remove_video); br.addWidget(rem); gl.addLayout(br); left.addWidget(g1)

        g2=QGroupBox("2) 현장 설명 / 길이"); f=QFormLayout(g2)
        self.desc=QLineEdit(); self.desc.setPlaceholderText("예: 모텔 혈흔 특수청소, 혈흔 제거 약품 사용"); f.addRow("현장 설명",self.desc)
        self.target=QComboBox(); self.target.addItems(["40","50","60"]); self.target.setCurrentText("50"); f.addRow("목표 길이",self.target)
        self.analyze=QPushButton("1. 영상 분석 + 쓸 장면 선택"); self.analyze.clicked.connect(self.do_analyze); f.addRow(self.analyze); left.addWidget(g2)

        gt=QGroupBox("3) TTS 공급자 / 목소리"); ft=QFormLayout(gt)
        self.provider=QComboBox(); self.provider.addItems(["Windows 기본 음성","Google 무료(gTTS)","Typecast"]); self.provider.currentTextChanged.connect(self.provider_changed); ft.addRow("TTS",self.provider)
        self.api_key=QLineEdit(); self.api_key.setEchoMode(QLineEdit.Password); self.api_key.setPlaceholderText("Typecast 선택 시 입력")
        saved=unprotect_secret(self.settings.value("typecast_key_dpapi","")); self.api_key.setText(saved); ft.addRow("Typecast API",self.api_key)
        vr=QHBoxLayout(); self.voice=QComboBox(); vr.addWidget(self.voice,1); load=QPushButton("목록 새로고침"); load.clicked.connect(self.load_provider_voices); vr.addWidget(load); ft.addRow("목소리",vr)
        self.rate=QSpinBox(); self.rate.setRange(-5,5); self.rate.setValue(0); ft.addRow("말하기 속도",self.rate)
        pv=QPushButton("선택 음성 미리듣기"); pv.clicked.connect(self.preview_voice); ft.addRow(pv); left.addWidget(gt)

        go=QGroupBox("4) 로고 / 화면 옵션"); fo=QFormLayout(go)
        lr=QHBoxLayout(); self.logo_label=QLabel("없음"); lb=QPushButton("로고 선택"); lb.clicked.connect(self.pick_logo); lr.addWidget(lb); lr.addWidget(self.logo_label,1); fo.addRow(lr)
        self.lw=QSpinBox(); self.lw.setRange(20,10000); self.lw.setValue(320); fo.addRow("로고 폭",self.lw)
        self.lx=QSpinBox(); self.lx.setRange(-5000,5000); self.lx.setValue(720); fo.addRow("로고 X",self.lx)
        self.ly=QSpinBox(); self.ly.setRange(-5000,5000); self.ly.setValue(210); fo.addRow("로고 Y",self.ly)
        self.effects=QCheckBox("자동 펀치인 효과"); self.effects.setChecked(True); fo.addRow(self.effects)
        self.banner=QCheckBox("상단 광고 배너"); self.banner.setChecked(True); fo.addRow(self.banner); left.addWidget(go); left.addStretch(1)

        # CENTER fixed preview. It never participates in right-panel layout.
        center=QWidget(); center.setMinimumWidth(300); center.setMaximumWidth(450); cl=QVBoxLayout(center); cl.setContentsMargins(8,8,8,8)
        gp=QGroupBox("9:16 미리보기"); gpl=QVBoxLayout(gp)
        info=QLabel("로고: 클릭 후 모서리 □ 드래그=크기 / 본체 드래그=이동\n자막: 드래그=이동 / 선택 후 휠=크기")
        info.setWordWrap(True); info.setStyleSheet("color:#666"); gpl.addWidget(info)
        self.preview=PreviewView(); self.preview.logo_changed.connect(self.logo_changed); self.preview.caption_changed.connect(self.caption_changed); gpl.addWidget(self.preview,1)
        self.preview.set_caption_geometry(self.cap_x,self.cap_y,self.cap_size)
        pr=QHBoxLayout(); self.play_btn=QPushButton("▶ 장면 넘겨보기"); self.play_btn.clicked.connect(self.toggle_preview); pr.addWidget(self.play_btn); self.preview_slider=QSlider(Qt.Horizontal); self.preview_slider.setRange(0,0); self.preview_slider.valueChanged.connect(self.preview_seek); pr.addWidget(self.preview_slider,1); gpl.addLayout(pr); cl.addWidget(gp); cl.addStretch(1); splitter.addWidget(center)

        # RIGHT content in scroll area
        right_wrap=QWidget(); right=QVBoxLayout(right_wrap); right.setContentsMargins(6,6,6,6)
        right_scroll=QScrollArea(); right_scroll.setWidgetResizable(True); right_scroll.setWidget(right_wrap); right_scroll.setMinimumWidth(360); splitter.addWidget(right_scroll)

        g4=QGroupBox("선택된 타임라인 — 한 행 = 한 장면 = 한 대본 = 한 TTS"); gr=QVBoxLayout(g4)
        self.table=QTableWidget(0,7); self.table.setHorizontalHeaderLabels(["#","파일","시작","원본컷","동기화컷","TTS","대본"]); self.table.horizontalHeader().setStretchLastSection(True); self.table.setMinimumHeight(300); self.table.setSelectionBehavior(QAbstractItemView.SelectRows); gr.addWidget(self.table); right.addWidget(g4)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        trim_row=QHBoxLayout()
        trim_btn=QPushButton("선택 컷 시작/끝 수정"); trim_btn.clicked.connect(self.trim_cut); trim_row.addWidget(trim_btn)
        delete_btn=QPushButton("선택 컷 삭제"); delete_btn.clicked.connect(self.delete_cuts); trim_row.addWidget(delete_btn); gr.addLayout(trim_row)
        self.length_info=QLabel("목표는 편집 타임라인 길이입니다. 최종 MP4 길이는 실제 TTS에 맞춰 조정됩니다.")
        self.length_info.setWordWrap(True); gr.addWidget(self.length_info)

        g5=QGroupBox("타임라인 기반 대본 / 자막"); gs=QVBoxLayout(g5)
        sr=QHBoxLayout(); sr.addWidget(QLabel("대본 스타일")); self.script_style=QComboBox(); self.script_style.addItems(["강한 자극형","자극적","스토리형","정보형","차분한 전문형"]); self.script_style.setCurrentText("자극적"); sr.addWidget(self.script_style,1); gs.addLayout(sr)
        cr=QHBoxLayout(); cr.addWidget(QLabel("자막 스타일")); self.caption_style=QComboBox(); self.caption_style.addItems(["쇼츠 굵은 흰색+검정외곽선","핵심어 노랑 강조","핵심어 빨강 강조","깔끔한 흰색","검정 박스형"]); cr.addWidget(self.caption_style,1); gs.addLayout(cr)
        self.script=QTextEdit(); self.script.setMinimumHeight(220); self.script.setPlaceholderText("영상 분석 후 대본 만들기를 누르세요. 한 줄이 한 장면입니다."); gs.addWidget(self.script)
        self.script_btn=QPushButton("2. 선택된 장면 기준 대본 만들기"); self.script_btn.setEnabled(False); self.script_btn.clicked.connect(self.do_script); gs.addWidget(self.script_btn)
        right.addWidget(g5)

        self.render=QPushButton("3. 컷별 TTS 동기화 후 MP4 자동 제작"); self.render.setEnabled(False); self.render.clicked.connect(self.do_render); self.render.setMinimumHeight(50); self.render.setStyleSheet("font-size:17px;font-weight:700"); right.addWidget(self.render); right.addStretch(1)

        self.prog=QProgressBar(); outer.addWidget(self.prog); self.status=QLabel("준비됨"); outer.addWidget(self.status)
        splitter.setSizes([360,440,720]); splitter.setStretchFactor(0,0); splitter.setStretchFactor(1,0); splitter.setStretchFactor(2,1)
        self.banner.toggled.connect(self.preview.set_banner)
        for combo in self.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)
        for box in (self.lw,self.lx,self.ly): box.valueChanged.connect(self.update_logo)
        self.provider.setCurrentText(self.settings.value("tts_provider","Windows 기본 음성")); self.provider_changed(self.provider.currentText())

    # ---- providers ----
    def provider_changed(self, name):
        self.settings.setValue("tts_provider",name)
        self.api_key.setEnabled(name == "Typecast")
        self.load_provider_voices(silent=True)

    def load_provider_voices(self, silent=False):
        if getattr(self,'voice_worker',None) is not None and self.voice_worker.isRunning(): return
        name=self.provider.currentText(); self.voice.clear()
        try:
            if name == "Windows 기본 음성":
                vals=sapi_voices()
                for label,idx in vals: self.voice.addItem(label,idx)
                if not vals and not silent: QMessageBox.warning(self,"Windows TTS","Windows 음성을 찾지 못했습니다.")
            elif name == "Google 무료(gTTS)":
                self.voice.addItem("Google 한국어 기본",'normal'); self.voice.addItem("Google 한국어 느리게",'slow')
            else:
                key=self.api_key.text().strip()
                if not key:
                    if not silent: QMessageBox.warning(self,"Typecast","API 키를 입력하세요.")
                    return
                enc=protect_secret(key)
                if enc: self.settings.setValue("typecast_key_dpapi",enc)
                self.status.setText("Typecast 음성 목록 불러오는 중...")
                self.provider.setEnabled(False)
                self.voice_worker=VoiceWorker(lambda:typecast_voices(key))
                self.voice_worker.done.connect(self.typecast_loaded)
                self.voice_worker.failed.connect(self.voice_failed)
                self.voice_worker.start()
                return
            self.status.setText(f"{name}: 목소리 {self.voice.count()}개")
        except Exception as e:
            if not silent: QMessageBox.critical(self,"TTS 목록 오류",str(e))

    def preview_voice(self):
        if self.busy(): return
        if self.voice.count()==0:
            self.load_provider_voices()
            if self.voice.count()==0: return
        try:
            import winsound
            provider=self.provider.currentText(); data=self.voice.currentData(); key=self.api_key.text().strip()
            cache_key=hashlib.md5((provider+str(data)+str(self.rate.value())).encode()).hexdigest()
            out=Path(tempfile.gettempdir())/f"threeguys_preview_{cache_key}.wav"
            rate=self.rate.value()
            def synth():
                if not out.exists(): synthesize_line(provider,"쓰리가이즈 특수청소입니다.",out,data,key,rate)
                return str(out)
            self.provider.setEnabled(False); self.status.setText("미리듣기 음성 생성 중...")
            self.voice_worker=VoiceWorker(synth)
            self.voice_worker.done.connect(self.voice_ready)
            self.voice_worker.failed.connect(self.voice_failed)
            self.voice_worker.start()
        except Exception as e: QMessageBox.critical(self,"TTS 미리듣기 오류",str(e))

    def typecast_loaded(self, voices):
        for v in voices:
            vid=v.get('voice_id'); label=v.get('voice_name') or v.get('name') or vid
            if vid: self.voice.addItem(label,vid)
        self.provider.setEnabled(True); self.status.setText(f"Typecast 음성 {self.voice.count()}개")

    def voice_ready(self, path):
        import winsound
        self.provider.setEnabled(True)
        try:
            winsound.PlaySound(path,winsound.SND_FILENAME|winsound.SND_ASYNC)
            self.status.setText("미리듣기 재생 중")
        except Exception as exc: self.voice_failed(str(exc))

    def voice_failed(self, message):
        self.provider.setEnabled(True); self.status.setText("음성 요청 실패")
        QMessageBox.warning(self,"음성 오류",message)

    # ---- media/ui ----
    def add_videos(self):
        if self.busy(): return
        files,_=QFileDialog.getOpenFileNames(self,"영상 선택","","Videos (*.mp4 *.mov *.m4v *.avi *.mkv *.webm)")
        if files: self.invalidate_timeline()
        for p in files:
            if p not in self.paths: self.paths.append(p); self.list.addItem(p)

    def remove_video(self):
        if self.busy(): return
        rows=sorted({i.row() for i in self.list.selectedIndexes()},reverse=True)
        if rows: self.invalidate_timeline()
        for r in rows: self.paths.pop(r); self.list.takeItem(r)

    def pick_logo(self):
        p,_=QFileDialog.getOpenFileName(self,"로고/이미지 선택","","Images (*.png *.jpg *.jpeg *.webp)")
        if p:
            self.logo=p; self.logo_label.setText(os.path.basename(p)); self.preview.set_logo(p,self.lw.value(),self.lx.value(),self.ly.value())

    def logo_changed(self,w,x,y):
        for box,val in [(self.lw,w),(self.lx,x),(self.ly,y)]: box.blockSignals(True); box.setValue(val); box.blockSignals(False)

    def update_logo(self):
        if self.logo: self.preview.set_logo(self.logo,self.lw.value(),self.lx.value(),self.ly.value())

    def busy(self):
        return any(getattr(self, name, None) is not None and getattr(self, name).isRunning()
                   for name in ('worker','rworker','voice_worker'))

    def closeEvent(self,event):
        if self.busy():
            self.status.setText("작업이 끝난 뒤 창을 닫아주세요."); event.ignore()
        else: event.accept()

    def invalidate_timeline(self):
        self.preview_timer.stop(); self.play_btn.setText("▶ 장면 넘겨보기")
        self.segs=[]; self.script.clear(); self.refresh_table()
        self.script_btn.setEnabled(False); self.render.setEnabled(False)
        self.preview_slider.setRange(0,0)

    def trim_cut(self):
        if self.busy(): return
        row=self.table.currentRow()
        if row < 0: return
        from PySide6.QtWidgets import QInputDialog
        s=self.segs[row]
        value,ok=QInputDialog.getText(self,"컷 범위","원본 시작초, 끝초",text=f"{s.start:.3f}, {s.end:.3f}")
        if not ok: return
        try:
            start,end=map(float,value.split(','))
            if not (0 <= start < end <= get_video_duration(s.path)): raise ValueError()
        except ValueError:
            QMessageBox.warning(self,"컷 범위","원본 안의 시작초, 끝초를 입력하세요."); return
        s.start,s.end=start,end; s.play_duration=0; s.voice_duration=0
        self.refresh_table(); self.show_preview_segment(row)

    def delete_cuts(self):
        if self.busy(): return
        rows=sorted({idx.row() for idx in self.table.selectedIndexes()},reverse=True)
        for row in rows: self.segs.pop(row)
        self.script.setPlainText("\n".join(s.line for s in self.segs)); self.refresh_table()
        self.preview_slider.setRange(0,max(0,len(self.segs)-1))
        self.script_btn.setEnabled(bool(self.segs)); self.render.setEnabled(bool(self.segs and all(s.line for s in self.segs)))
        self.show_preview_segment(self.preview_slider.value())

    def caption_changed(self,x,y,size):
        self.cap_x,self.cap_y,self.cap_size=x,y,size

    def set_status(self,p,s): self.prog.setValue(p); self.status.setText(s)

    def do_analyze(self):
        if self.busy(): return
        if not self.paths:
            QMessageBox.warning(self,"확인","먼저 원본 영상을 추가하세요."); return
        self.script_btn.setEnabled(False); self.render.setEnabled(False); self.script.clear(); self.segs=[]; self.analyze.setEnabled(False)
        self.worker=AnalyzeWorker(self.paths,int(self.target.currentText())); self.worker.status.connect(self.set_status); self.worker.done.connect(self.analyzed); self.worker.failed.connect(self.fail); self.worker.start()

    def analyzed(self,segs):
        self.analyze.setEnabled(True); self.segs=segs; self.refresh_table(); self.preview_slider.setRange(0,max(0,len(segs)-1)); self.preview_slider.setValue(0); self.show_preview_segment(0); self.script_btn.setEnabled(bool(segs)); self.set_status(100,f"{len(segs)}개 컷 선택 완료. 이제 컷 기준 대본을 만드세요.")
        if not segs: QMessageBox.warning(self,"분석 결과","사용 가능한 장면을 찾지 못했습니다.")

    def refresh_table(self):
        self.length_info.setText(f"선택 {sum(s.source_duration for s in self.segs):.2f}초 / 목표 {self.target.currentText()}초 · "
                                 + (f"TTS 동기화 {sum(s.duration for s in self.segs):.2f}초" if any(s.voice_duration for s in self.segs) else "최종 길이는 TTS 합성 후 확정"))
        self.table.setRowCount(len(self.segs))
        for r,s in enumerate(self.segs):
            vals=[str(r+1),os.path.basename(s.path),f"{s.start:.1f}",f"{s.source_duration:.1f}s",f"{s.duration:.1f}s",f"{s.voice_duration:.1f}s" if s.voice_duration else "-",s.line]
            for c,v in enumerate(vals): self.table.setItem(r,c,QTableWidgetItem(v))
        self.table.resizeColumnsToContents(); self.table.horizontalHeader().setStretchLastSection(True)

    def do_script(self):
        if self.busy(): return
        if not self.segs: QMessageBox.warning(self,"확인","영상 분석부터 해야 합니다."); return
        lines=make_script(self.desc.text(),self.segs,self.script_style.currentText())
        for s,line in zip(self.segs,lines): s.line=line; s.play_duration=0; s.voice_duration=0
        self.script.setPlainText("\n".join(lines)); self.refresh_table(); self.render.setEnabled(True); self.status.setText("장면별 대본 생성 완료. 한 줄이 해당 장면에 그대로 연결됩니다."); self.show_preview_segment(self.preview_slider.value())

    def sync_script_from_box(self):
        lines=[x.strip() for x in self.script.toPlainText().splitlines()]
        if len(lines)!=len(self.segs) or not all(lines): return False
        for s,l in zip(self.segs,lines): s.line=l
        return True

    def show_preview_segment(self,idx):
        if not self.segs or idx<0 or idx>=len(self.segs): return
        s=self.segs[idx]; cap=cv2.VideoCapture(s.path); cap.set(cv2.CAP_PROP_POS_MSEC,s.start*1000); ok,frame=cap.read(); cap.release()
        if ok: self.preview.set_frame(frame)
        self.preview.set_caption(s.line or f"장면 {idx+1} 자막 미리보기"); self.preview.set_banner(self.banner.isChecked())
        if self.logo and not self.preview.logo_item: self.preview.set_logo(self.logo,self.lw.value(),self.lx.value(),self.ly.value())

    def preview_seek(self,v): self.show_preview_segment(v)
    def toggle_preview(self):
        if not self.segs: return
        if self.preview_timer.isActive(): self.preview_timer.stop(); self.play_btn.setText("▶ 장면 넘겨보기")
        else: self.preview_timer.start(1000); self.play_btn.setText("■ 정지")
    def preview_next(self):
        if not self.segs: self.preview_timer.stop(); return
        n=self.preview_slider.value()+1
        if n>=len(self.segs): n=0
        self.preview_slider.setValue(n)

    def do_render(self):
        if self.busy(): return
        if not self.segs: return
        if not self.sync_script_from_box():
            QMessageBox.warning(self,"대본 줄 수","대본은 선택된 장면 수와 같은 줄 수여야 합니다. 한 장면당 한 줄로 맞춰주세요."); return
        if self.voice.count()==0:
            self.load_provider_voices()
            if self.voice.count()==0: return
        out,_=QFileDialog.getSaveFileName(self,"완성 MP4 저장","ThreeGuys_Shorts.mp4","MP4 (*.mp4)")
        if not out: return
        provider=self.provider.currentText(); data=self.voice.currentData(); key=self.api_key.text().strip()
        if provider=="Typecast" and not key:
            QMessageBox.warning(self,"Typecast","API 키를 입력하세요."); return
        if provider=="Typecast":
            enc=protect_secret(key)
            if enc: self.settings.setValue("typecast_key_dpapi",enc)
        self.render.setEnabled(False); self.analyze.setEnabled(False); self.script_btn.setEnabled(False)
        self.render_segs=[replace(s) for s in self.segs]
        self.script.setReadOnly(True)
        args=(self.render_segs,out,provider,data,key,self.rate.value(),self.logo,self.lw.value(),self.lx.value(),self.ly.value(),self.effects.isChecked(),self.banner.isChecked(),self.caption_style.currentText(),self.cap_x,self.cap_y,self.cap_size)
        self.rworker=RenderWorker(args); self.rworker.status.connect(self.set_status); self.rworker.done.connect(self.render_done); self.rworker.failed.connect(self.fail); self.rworker.start()

    def render_done(self,out):
        self.segs=self.render_segs; self.script.setReadOnly(False)
        self.render.setEnabled(True); self.analyze.setEnabled(True); self.script_btn.setEnabled(True); self.refresh_table(); self.set_status(100,"MP4 제작 완료 — 컷별 TTS/자막 동기화 적용됨")
        QMessageBox.information(self,"완료",f"완성되었습니다.\n\n{out}")
        try: os.startfile(os.path.dirname(out))
        except Exception: pass

    def fail(self,msg):
        self.script.setReadOnly(False)
        self.analyze.setEnabled(True); self.script_btn.setEnabled(bool(self.segs)); self.render.setEnabled(bool(self.segs and self.script.toPlainText().strip())); self.set_status(0,"오류 발생"); QMessageBox.critical(self,"오류",msg[-7000:])


if __name__=='__main__':
    if '--self-test' in sys.argv:
        from smoke_test import run_checks
        run_checks(sys.modules[__name__],sys.argv[sys.argv.index('--self-test')+1])
        sys.exit(0)
    app=QApplication(sys.argv); app.setStyle('Fusion'); w=Main(); w.show(); sys.exit(app.exec())
