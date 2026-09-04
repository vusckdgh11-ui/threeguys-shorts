import os, sys, math, json, wave, tempfile, subprocess, traceback, re, requests
from pathlib import Path
from dataclasses import dataclass
from typing import List

import cv2
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSettings, QRectF
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QListWidget, QFileDialog, QLabel, QLineEdit, QComboBox, QSpinBox, QTextEdit,
    QTableWidget, QTableWidgetItem, QMessageBox, QProgressBar, QGroupBox,
    QFormLayout, QCheckBox, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsTextItem, QSlider, QSplitter
)
from PySide6.QtGui import QImage, QPixmap, QFont, QColor, QBrush, QPen

APP_NAME = "ThreeGuys Shorts"
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}

@dataclass
class Segment:
    path: str
    start: float
    end: float
    score: float
    line: str = ""

    @property
    def duration(self):
        return max(0.05, self.end - self.start)


def natural_key(p):
    s = os.path.basename(p)
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", s)]


def ffmpeg_exe():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def get_video_duration(path):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    if frames > 0 and fps > 0:
        return frames / fps
    return 0.0


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
    exposure_penalty = 1.0
    if bright < 18 or bright > 238:
        exposure_penalty = 0.35
    return exposure_penalty * (0.32 * min(sharp, 700) + 2.3 * motion + 0.35 * sat + 0.5 * contrast)


def candidate_segments(path, seg_len=4.0, sample_step=1.0):
    dur = get_video_duration(path)
    if dur <= 0.5:
        return []
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    prev = None
    samples = []
    t = 0.0
    while t < dur:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok:
            t += sample_step
            continue
        h, w = frame.shape[:2]
        scale = 360.0 / max(h, w)
        if scale < 1:
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
        sc = frame_score(prev, frame)
        samples.append((t, sc))
        prev = frame
        t += sample_step
    cap.release()
    if not samples:
        return []
    samples.sort(key=lambda x: x[1], reverse=True)
    out = []
    occupied = []
    for t, sc in samples:
        start = max(0.0, min(t - seg_len / 2, max(0.0, dur - seg_len)))
        end = min(dur, start + seg_len)
        if end - start < 1.4:
            continue
        center = (start + end) / 2
        if any(abs(center - c) < seg_len * 0.75 for c in occupied):
            continue
        occupied.append(center)
        out.append(Segment(path, start, end, sc))
        if len(out) >= max(2, int(dur // 7) + 1):
            break
    return out


def choose_timeline(paths, target):
    paths = sorted(paths, key=natural_key)
    allc = []
    for i, p in enumerate(paths):
        cands = candidate_segments(p, seg_len=4.2 if target <= 50 else 4.8)
        for c in cands:
            allc.append((i, c))
    if not allc:
        return []

    # 한 영상에 몰리지 않도록 각 클립 최고 장면을 먼저 채운 뒤, 점수순으로 보충
    picked = []
    per_clip = {}
    for i, c in allc:
        per_clip.setdefault(i, []).append(c)
    for i in range(len(paths)):
        vals = sorted(per_clip.get(i, []), key=lambda s: s.score, reverse=True)
        if vals:
            picked.append((i, vals[0]))
    remaining = sorted(allc, key=lambda x: x[1].score, reverse=True)
    seen = {(i, round(c.start, 2)) for i, c in picked}
    total = sum(c.duration for _, c in picked)
    for i, c in remaining:
        if total >= target:
            break
        key = (i, round(c.start, 2))
        if key in seen:
            continue
        picked.append((i, c)); seen.add(key); total += c.duration

    # 목표 길이에 맞춰 마지막 장면을 자른다.
    picked.sort(key=lambda x: (x[0], x[1].start))
    result = []
    acc = 0.0
    for _, c in picked:
        if acc >= target:
            break
        left = target - acc
        if c.duration > left:
            if left >= 1.2:
                c.end = c.start + left
                result.append(c)
                acc += left
            break
        result.append(c)
        acc += c.duration
    return result


def make_script(description: str, segs: List[Segment], style="자극적"):
    desc = description.strip() or "특수청소 현장"
    banks = {
        "강한 자극형": [
            f"{desc}. 문을 열자마자 예상보다 훨씬 심각한 현장이 나왔습니다.",
            "처음 보이는 곳만 보고 끝이라고 생각하면 큰일 납니다.",
            "오염은 눈에 보이는 곳보다 더 깊숙이 번져 있었습니다.",
            "특히 이런 틈을 놓치면 냄새와 흔적이 다시 올라올 수 있습니다.",
            "그래서 전용 약품을 뿌려 숨어 있는 오염까지 반응을 확인합니다.",
            "한 번 닦았다고 끝? 아닙니다. 반응이 남으면 다시 처리합니다.",
            "가구 아래와 모서리까지 확인하자 놓치기 쉬운 흔적이 계속 나옵니다.",
            "일반 청소처럼 보이지만 실제 작업은 전혀 다릅니다.",
            "이런 현장을 직접 처리하기 어려운 이유가 바로 여기에 있습니다.",
            "이런 곳까지 누가 청소하냐고요? 저희가 합니다.",
            "쓰리가이즈 특수청소. 서울·경기 수도권 24시간 상담 가능합니다."
        ],
        "자극적": [
            f"{desc}. 현장에 들어가자마자 심상치 않은 흔적이 보였습니다.",
            "그런데 눈에 보이는 게 전부가 아니었습니다.",
            "오염이 어디까지 번졌는지 하나씩 확인해 봤습니다.",
            "가구 주변과 틈새까지 확인하자 남아 있는 흔적이 보입니다.",
            "전용 약품을 사용하면 놓친 오염이 있는지 반응으로 확인할 수 있습니다.",
            "반응이 남는 부분은 다시 확인하고 반복해서 처리합니다.",
            "대충 닦고 끝내면 냄새와 오염이 다시 남을 수 있습니다.",
            "그래서 이런 현장은 순서와 확인 과정이 중요합니다.",
            "끝까지 확인한 뒤에야 작업을 마무리합니다.",
            "이런 곳까지 누가 청소하냐고요? 저희가 합니다.",
            "쓰리가이즈 특수청소. 서울·경기 수도권 24시간 상담 가능합니다."
        ],
        "스토리형": [
            f"이번 의뢰는 {desc}였습니다.",
            "현장에 도착해 문을 열고 먼저 전체 상태부터 살펴봤습니다.",
            "처음에는 보이는 부분부터 확인했지만 안쪽 상황도 확인이 필요했습니다.",
            "가구와 주변을 따라 오염 범위를 하나씩 찾아갑니다.",
            "이제 전용 약품으로 남아 있는 오염을 확인합니다.",
            "반응이 나타나는 곳은 놓치지 않고 반복 처리합니다.",
            "작은 틈과 모서리도 다시 살펴봅니다.",
            "처음 모습과 비교하면 현장이 조금씩 정리되기 시작합니다.",
            "마지막으로 남은 부분이 없는지 다시 점검합니다.",
            "이렇게 현장 하나를 마무리했습니다.",
            "쓰리가이즈 특수청소. 필요한 순간 연락주세요."
        ],
        "정보형": [
            f"{desc}. 이런 현장은 먼저 오염 범위를 확인하는 것이 중요합니다.",
            "겉으로 보이는 부분만 닦아서는 충분하지 않을 수 있습니다.",
            "주변과 틈새까지 오염이 번졌는지 확인합니다.",
            "상태에 맞는 전용 약품과 작업 방법을 선택합니다.",
            "약품 반응을 보면서 남아 있는 오염을 확인합니다.",
            "필요한 부분은 반복 처리해 잔여 오염을 줄입니다.",
            "가구 주변과 모서리처럼 놓치기 쉬운 곳도 점검합니다.",
            "작업 후에는 전체 공간을 다시 확인합니다.",
            "특수청소는 제거뿐 아니라 확인 과정까지 중요합니다.",
            "전문적인 처리가 필요한 이유입니다.",
            "쓰리가이즈 특수청소. 서울·경기 수도권 24시간 상담 가능합니다."
        ],
        "차분한 전문형": [
            f"{desc}. 현장 상태를 확인한 뒤 작업 범위를 정했습니다.",
            "먼저 눈에 보이는 오염과 주변 상태를 점검합니다.",
            "오염이 이어진 부분을 따라 범위를 확인합니다.",
            "가구 주변과 틈새도 빠짐없이 살펴봅니다.",
            "상태에 맞는 전용 약품으로 처리합니다.",
            "처리 후 반응을 확인하고 필요한 부분을 반복 작업합니다.",
            "잔여 오염이 없는지 세부 구간을 다시 점검합니다.",
            "현장에 맞는 절차로 차근차근 마무리합니다.",
            "마지막으로 전체 상태를 확인합니다.",
            "안전하고 꼼꼼한 처리가 필요한 현장이었습니다.",
            "쓰리가이즈 특수청소. 서울·경기 수도권 24시간 상담 가능합니다."
        ]
    }
    templates = banks.get(style, banks["자극적"])
    lines=[]; n=len(segs)
    for i in range(n):
        if i == 0: line=templates[0]
        elif i == n-1: line=templates[-1]
        else: line=templates[1+((i-1)%(len(templates)-2))]
        lines.append(line)
    return lines



def typecast_voices(api_key):
    r = requests.get("https://api.typecast.ai/v2/voices", params={"model":"ssfm-v30"}, headers={"X-API-KEY":api_key}, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else data.get("voices", data.get("data", []))


def synthesize_typecast(text, out_wav, api_key, voice_id, tempo=1.0):
    payload = {
        "voice_id": voice_id, "text": text[:2000], "model": "ssfm-v30", "language": "kor",
        "prompt": {"emotion_type": "smart"},
        "output": {"volume": 100, "audio_pitch": 0, "audio_tempo": float(tempo), "audio_format": "wav"}
    }
    r = requests.post("https://api.typecast.ai/v1/text-to-speech", headers={"X-API-KEY":api_key,"Content-Type":"application/json"}, json=payload, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"Typecast TTS 오류 ({r.status_code}): {r.text[:1000]}")
    Path(out_wav).write_bytes(r.content)

def sapi_voices():
    if os.name != "nt":
        return []
    import win32com.client
    voice = win32com.client.Dispatch("SAPI.SpVoice")
    tokens = voice.GetVoices()
    out = []
    for i in range(tokens.Count):
        tok = tokens.Item(i)
        out.append((tok.GetDescription(), i))
    return out


def synthesize_sapi(text, out_wav, voice_index=0, rate=0):
    import win32com.client
    voice = win32com.client.Dispatch("SAPI.SpVoice")
    voices = voice.GetVoices()
    if voices.Count:
        voice.Voice = voices.Item(max(0, min(voice_index, voices.Count - 1)))
    voice.Rate = int(rate)
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    # 22kHz 16-bit mono PCM
    stream.Format.Type = 22
    stream.Open(str(out_wav), 3, False)
    voice.AudioOutputStream = stream
    voice.Speak(text)
    stream.Close()


def wav_duration(path):
    with wave.open(str(path), 'rb') as w:
        return w.getnframes() / float(w.getframerate())


def esc_ass(s):
    return s.replace('\\', r'\\').replace('{', r'\{').replace('}', r'\}').replace('\n', r'\N')


def ass_time(sec):
    h = int(sec // 3600); sec -= h * 3600
    m = int(sec // 60); sec -= m * 60
    return f"{h}:{m:02d}:{sec:05.2f}"


def build_ass(path, segs, total, banner=True):
    header = """[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\nStyle: Caption,Malgun Gothic,68,&H00FFFFFF,&H000000FF,&H00101010,&H90000000,-1,0,0,0,100,100,0,0,1,4,1,2,70,70,255,1\nStyle: Banner,Malgun Gothic,44,&H00FFFFFF,&H000000FF,&H00000000,&HA8000000,-1,0,0,0,100,100,0,0,3,2,0,8,40,40,55,1\nStyle: Brand,Malgun Gothic,36,&H00FFFFFF,&H000000FF,&H00000000,&H7F000000,-1,0,0,0,100,100,0,0,3,1,0,8,40,40,126,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"""
    events = []
    if banner:
        events.append(f"Dialogue: 0,{ass_time(0)},{ass_time(total)},Banner,,0,0,0,,고독사 | 혈흔 | 쓰레기집 | 특수청소 문의")
        events.append(f"Dialogue: 0,{ass_time(0)},{ass_time(total)},Brand,,0,0,0,,쓰리가이즈 특수청소  ·  24시간 상담  ·  서울·경기 수도권")
    t = 0.0
    for s in segs:
        end = t + s.duration
        events.append(f"Dialogue: 0,{ass_time(t)},{ass_time(end)},Caption,,0,0,0,,{esc_ass(s.line)}")
        t = end
    Path(path).write_text(header + "\n".join(events), encoding='utf-8-sig')


def ffpath(p):
    return str(Path(p).resolve())


def render_video(segs: List[Segment], out_mp4, api_key, voice_id, voice_rate, logo_path="", logo_w=300, logo_x=740, logo_y=220, effects=True, banner=True, progress=None):
    tmp = Path(tempfile.mkdtemp(prefix="threeguys_"))
    try:
        # TTS: 전체 대본을 한 번에 합성
        tts_wav = tmp / "voice.wav"
        full_text = " ".join(s.line for s in segs if s.line.strip())
        
        if api_key and voice_id:
            synthesize_typecast(full_text, tts_wav, api_key, voice_id, max(0.7, min(1.3, 1.0 + voice_rate * 0.05)))
        else:
            synthesize_sapi(full_text, tts_wav, 0, voice_rate)
        audio_dur = wav_duration(tts_wav)
        video_total = sum(s.duration for s in segs)

        # 음성이 영상보다 약간 길면 마지막 장면을 가능한 범위까지 늘림
        if audio_dur > video_total + 0.2 and segs:
            last = segs[-1]
            src_dur = get_video_duration(last.path)
            can = max(0, src_dur - last.end)
            add = min(can, audio_dur - video_total + 0.2)
            last.end += add
            video_total += add

        ass = tmp / "captions.ass"
        build_ass(ass, segs, video_total, banner=banner)

        ff = ffmpeg_exe()
        cmd = [ff, '-y']
        for s in segs:
            cmd += ['-ss', f'{s.start:.3f}', '-t', f'{s.duration:.3f}', '-i', ffpath(s.path)]
        logo_input_index = None
        if logo_path and os.path.exists(logo_path):
            logo_input_index = len(segs)
            cmd += ['-i', ffpath(logo_path)]
        cmd += ['-i', ffpath(tts_wav)]
        audio_index = len(segs) + (1 if logo_input_index is not None else 0)

        filters = []
        concat_inputs = []
        for i, s in enumerate(segs):
            base = f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
            if effects and i % 4 == 2:
                base += ",scale=1166:2074,crop=1080:1920"
            base += f",fps=30,setsar=1[v{i}]"
            filters.append(base)
            concat_inputs.append(f"[v{i}]")
        filters.append("".join(concat_inputs) + f"concat=n={len(segs)}:v=1:a=0[vcat]")

        # ASS path escaping for filter
        ass_filter_path = str(ass).replace('\\', '/').replace(':', r'\:').replace("'", r"\'")
        filters.append(f"[vcat]subtitles='{ass_filter_path}'[vsub]")
        final_label = '[vsub]'
        if logo_input_index is not None:
            filters.append(f"[{logo_input_index}:v]scale={int(logo_w)}:-1[logo]")
            filters.append(f"[vsub][logo]overlay={int(logo_x)}:{int(logo_y)}:format=auto[vout]")
            final_label = '[vout]'

        cmd += ['-filter_complex', ';'.join(filters), '-map', final_label, '-map', f'{audio_index}:a:0',
                '-c:v', 'libx264', '-preset', 'medium', '-crf', '19', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-b:a', '192k', '-af', f'apad=pad_dur={video_total:.3f}', '-t', f'{video_total:.3f}', '-movflags', '+faststart', ffpath(out_mp4)]

        if progress: progress(70, "FFmpeg 렌더링 중...")
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
        if p.returncode != 0:
            raise RuntimeError("FFmpeg 오류:\n" + p.stderr[-5000:])
        if progress: progress(100, "완료")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


class AnalyzeWorker(QThread):
    done = Signal(object)
    failed = Signal(str)
    status = Signal(int, str)
    def __init__(self, paths, target):
        super().__init__(); self.paths = paths; self.target = target
    def run(self):
        try:
            self.status.emit(10, "영상 길이와 장면 변화 분석 중...")
            segs = choose_timeline(self.paths, self.target)
            self.status.emit(100, "장면 선택 완료")
            self.done.emit(segs)
        except Exception:
            self.failed.emit(traceback.format_exc())


class RenderWorker(QThread):
    done = Signal(str)
    failed = Signal(str)
    status = Signal(int, str)
    def __init__(self, segs, out, api_key, voice_id, voice_rate, logo, lw, lx, ly, effects, banner):
        super().__init__(); self.args=(segs,out,api_key,voice_id,voice_rate,logo,lw,lx,ly,effects,banner)
    def run(self):
        try:
            render_video(*self.args, progress=lambda p,s:self.status.emit(p,s))
            self.done.emit(self.args[1])
        except Exception:
            self.failed.emit(traceback.format_exc())



class PreviewView(QGraphicsView):
    overlay_changed = Signal(int, int, int)
    def __init__(self):
        super().__init__()
        self.sc = QGraphicsScene(self); self.setScene(self.sc)
        self.setFixedSize(360, 640); self.setSceneRect(0,0,1080,1920)
        self.setStyleSheet("background:#111;border:1px solid #555")
        self.bg = QGraphicsPixmapItem(); self.sc.addItem(self.bg)
        self.logo_item = None
        self.caption = QGraphicsTextItem(); self.caption.setDefaultTextColor(QColor('white')); self.caption.setFont(QFont('Malgun Gothic', 68, QFont.Bold)); self.caption.setTextWidth(900); self.caption.setPos(90,1450); self.caption.setFlags(QGraphicsTextItem.ItemIsMovable|QGraphicsTextItem.ItemIsSelectable); self.caption.setZValue(20); self.sc.addItem(self.caption)
        self.banner1 = QGraphicsTextItem("고독사 | 혈흔 | 쓰레기집 | 특수청소 문의"); self.banner1.setDefaultTextColor(QColor('white')); self.banner1.setFont(QFont('Malgun Gothic',34,QFont.Bold)); self.banner1.setPos(55,45); self.sc.addItem(self.banner1)
        self.banner2 = QGraphicsTextItem("쓰리가이즈 특수청소 · 24시간 상담 · 서울·경기 수도권"); self.banner2.setDefaultTextColor(QColor('white')); self.banner2.setFont(QFont('Malgun Gothic',28,QFont.Bold)); self.banner2.setPos(55,110); self.sc.addItem(self.banner2)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def set_frame(self, frame):
        if frame is None: return
        frame=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB); h,w=frame.shape[:2]
        q=QImage(frame.data,w,h,frame.strides[0],QImage.Format_RGB888).copy()
        pm=QPixmap.fromImage(q).scaled(1080,1920,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation)
        if pm.width()>1080 or pm.height()>1920:
            pm=pm.copy(max(0,(pm.width()-1080)//2),max(0,(pm.height()-1920)//2),1080,1920)
        self.bg.setPixmap(pm); self.bg.setPos(0,0)

    def set_logo(self, path, width=320, x=720, y=210):
        if self.logo_item: self.sc.removeItem(self.logo_item); self.logo_item=None
        if not path: return
        pm=QPixmap(path)
        if pm.isNull(): return
        pm=pm.scaledToWidth(max(30,width),Qt.SmoothTransformation)
        self.logo_item=QGraphicsPixmapItem(pm); self.logo_item.setFlags(QGraphicsPixmapItem.ItemIsMovable|QGraphicsPixmapItem.ItemIsSelectable)
        self.logo_item.setPos(x,y); self.logo_item.setZValue(10); self.sc.addItem(self.logo_item)

    def wheelEvent(self, e):
        if self.caption.isSelected():
            factor=1.08 if e.angleDelta().y()>0 else 0.92
            self.caption.setScale(max(0.25,min(6.0,self.caption.scale()*factor)))
            e.accept(); return
        if self.logo_item and self.logo_item.isSelected():
            factor=1.08 if e.angleDelta().y()>0 else 0.92
            self.logo_item.setScale(max(0.05,min(12.0,self.logo_item.scale()*factor)))
            self.emit_overlay(); e.accept(); return
        super().wheelEvent(e)

    def mouseReleaseEvent(self,e):
        super().mouseReleaseEvent(e); self.emit_overlay()

    def emit_overlay(self):
        if self.logo_item:
            p=self.logo_item.pos(); w=int(self.logo_item.pixmap().width()*self.logo_item.scale())
            self.overlay_changed.emit(w,int(p.x()),int(p.y()))

    def set_caption(self,text): self.caption.setPlainText(text or "자막 미리보기")
    def set_banner(self,on): self.banner1.setVisible(on); self.banner2.setVisible(on)

class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 860)
        self.paths=[]; self.segs=[]; self.logo=""; self.voices=[]; self.settings=QSettings("ThreeGuys","ThreeGuysShorts"); self.preview_index=0; self.preview_timer=QTimer(self); self.preview_timer.timeout.connect(self.preview_next)
        root=QWidget(); self.setCentralWidget(root); lay=QVBoxLayout(root)

        title=QLabel("쓰리가이즈 쇼츠 자동제작")
        title.setStyleSheet("font-size:24px;font-weight:700")
        lay.addWidget(title)
        sub=QLabel("영상 분석 → 쓸 장면 자동 선택 → 타임라인 기반 대본 → Typecast TTS → 미리보기 → 자동 자막 → MP4")
        sub.setStyleSheet("color:#666")
        lay.addWidget(sub)

        row=QHBoxLayout(); lay.addLayout(row, 1)
        left=QVBoxLayout(); right=QVBoxLayout(); row.addLayout(left, 1); row.addLayout(right, 2)

        g1=QGroupBox("1) 원본 영상"); gl=QVBoxLayout(g1)
        self.list=QListWidget(); gl.addWidget(self.list)
        br=QHBoxLayout(); gl.addLayout(br)
        add=QPushButton("영상 여러 개 추가"); add.clicked.connect(self.add_videos); br.addWidget(add)
        rem=QPushButton("선택 삭제"); rem.clicked.connect(self.remove_video); br.addWidget(rem)
        left.addWidget(g1)

        g2=QGroupBox("2) 현장 설명 / 길이"); f=QFormLayout(g2)
        self.desc=QLineEdit(); self.desc.setPlaceholderText("예: 모텔 자살시도 후 생존 고객이 의뢰한 혈흔 특수청소, 혈흔 제거 약품 사용")
        f.addRow("현장 설명", self.desc)
        self.target=QComboBox(); self.target.addItems(["40", "50", "60"]); self.target.setCurrentText("50")
        f.addRow("목표 길이(초)", self.target)
        self.analyze=QPushButton("1. 영상 분석 + 쓸 장면 자동 선택")
        self.analyze.clicked.connect(self.do_analyze); f.addRow(self.analyze)
        left.addWidget(g2)

        g3=QGroupBox("3) Typecast TTS / 로고"); f3=QFormLayout(g3)
        self.api_key=QLineEdit(); self.api_key.setEchoMode(QLineEdit.Password); self.api_key.setText(self.settings.value("typecast_api_key", "")); self.api_key.setPlaceholderText("Typecast API 키 (이 PC에만 저장)"); f3.addRow("API 키",self.api_key)
        vr=QHBoxLayout(); self.voice=QComboBox(); loadv=QPushButton("음성 불러오기"); loadv.clicked.connect(self.load_typecast_voices); vr.addWidget(self.voice,1); vr.addWidget(loadv); f3.addRow("Typecast 음성",vr)
        self.rate=QSpinBox(); self.rate.setRange(-5,5); self.rate.setValue(0); f3.addRow("말하기 속도", self.rate)
        pv=QPushButton("선택 음성 미리듣기"); pv.clicked.connect(self.preview_voice); f3.addRow(pv)
        lr=QHBoxLayout(); self.logo_label=QLabel("없음"); lb=QPushButton("로고/이미지 선택"); lb.clicked.connect(self.pick_logo); lr.addWidget(lb); lr.addWidget(self.logo_label); f3.addRow(lr)
        self.lw=QSpinBox(); self.lw.setRange(30, 10000); self.lw.setValue(320); f3.addRow("로고 폭(px)", self.lw)
        self.lx=QSpinBox(); self.lx.setRange(-5000, 5000); self.lx.setValue(720); f3.addRow("로고 X", self.lx)
        self.ly=QSpinBox(); self.ly.setRange(-5000, 5000); self.ly.setValue(210); f3.addRow("로고 Y", self.ly)
        self.effects=QCheckBox("자동 펀치인 효과"); self.effects.setChecked(True); f3.addRow(self.effects)
        self.banner=QCheckBox("상단 고정 광고 배너"); self.banner.setChecked(True); self.banner.toggled.connect(lambda v:self.preview.set_banner(v) if hasattr(self,'preview') else None); f3.addRow(self.banner)
        left.addWidget(g3)

        gp=QGroupBox("최종 화면 미리보기 (로고: 드래그 이동 / 선택 후 마우스휠 크기조절)"); gpl=QVBoxLayout(gp)
        self.preview=PreviewView(); self.preview.overlay_changed.connect(self.overlay_changed); gpl.addWidget(self.preview,0,Qt.AlignHCenter)
        pr=QHBoxLayout(); self.play_btn=QPushButton("▶ 미리보기 재생"); self.play_btn.clicked.connect(self.toggle_preview); pr.addWidget(self.play_btn)
        self.preview_slider=QSlider(Qt.Horizontal); self.preview_slider.setRange(0,0); self.preview_slider.valueChanged.connect(self.preview_seek); pr.addWidget(self.preview_slider,1); gpl.addLayout(pr)
        right.addWidget(gp,2)

        g4=QGroupBox("선택된 타임라인 (분석 후 생성)"); gr=QVBoxLayout(g4)
        self.table=QTableWidget(0,6); self.table.setHorizontalHeaderLabels(["순서","파일","시작","끝","길이","대본"])
        self.table.horizontalHeader().setStretchLastSection(True); gr.addWidget(self.table)
        right.addWidget(g4, 2)

        g5=QGroupBox("타임라인 기반 대본"); gs=QVBoxLayout(g5)
        sr=QHBoxLayout(); sr.addWidget(QLabel("대본 스타일")); self.script_style=QComboBox(); self.script_style.addItems(["강한 자극형","자극적","스토리형","정보형","차분한 전문형"]); self.script_style.setCurrentText("자극적"); sr.addWidget(self.script_style,1); gs.addLayout(sr)
        cr=QHBoxLayout(); cr.addWidget(QLabel("자막 스타일")); self.caption_style=QComboBox(); self.caption_style.addItems(["쇼츠 굵은 흰색+검정외곽선","핵심어 노랑 강조","핵심어 빨강 강조","깔끔한 흰색","검정 박스형"]); cr.addWidget(self.caption_style,1); gs.addLayout(cr)
        hint=QLabel("자막도 미리보기에서 마우스로 드래그 이동 / 선택 후 휠로 크기조절"); hint.setStyleSheet("color:#666"); gs.addWidget(hint)
        self.script=QTextEdit(); self.script.setPlaceholderText("영상 분석 후 대본 만들기를 누르세요."); gs.addWidget(self.script)
        self.script_btn=QPushButton("2. 타임라인 기반 대본 만들기"); self.script_btn.setEnabled(False); self.script_btn.clicked.connect(self.do_script); gs.addWidget(self.script_btn)
        right.addWidget(g5,1)

        self.render=QPushButton("3. MP4 자동 제작")
        self.render.setEnabled(False); self.render.clicked.connect(self.do_render)
        self.render.setMinimumHeight(48); self.render.setStyleSheet("font-size:17px;font-weight:700")
        right.addWidget(self.render)

        self.prog=QProgressBar(); lay.addWidget(self.prog)
        self.status=QLabel("준비됨"); lay.addWidget(self.status)
        if self.api_key.text().strip(): self.load_typecast_voices(silent=True)

    def load_typecast_voices(self, silent=False):
        key=self.api_key.text().strip()
        if not key:
            if not silent: QMessageBox.warning(self,"Typecast","API 키를 입력하세요.")
            return
        try:
            self.settings.setValue("typecast_api_key",key)
            self.status.setText("Typecast 음성 목록 불러오는 중..."); QApplication.processEvents()
            voices=typecast_voices(key); self.voice.clear(); self.voices=[]
            for v in voices:
                vid=v.get("voice_id"); name=v.get("voice_name") or v.get("name") or vid
                if vid:
                    meta=f"{name} · {v.get('gender','')} · {v.get('age','')}".strip(' ·')
                    self.voice.addItem(meta,vid); self.voices.append(v)
            self.status.setText(f"Typecast 음성 {self.voice.count()}개 불러옴")
        except Exception as e:
            if not silent: QMessageBox.critical(self,"Typecast 연결 오류",str(e))

    def add_videos(self):
        files,_=QFileDialog.getOpenFileNames(self,"영상 선택","","Videos (*.mp4 *.mov *.m4v *.avi *.mkv *.webm)")
        for p in files:
            if p not in self.paths:
                self.paths.append(p); self.list.addItem(p)

    def remove_video(self):
        rows=sorted({i.row() for i in self.list.selectedIndexes()}, reverse=True)
        for r in rows:
            self.paths.pop(r); self.list.takeItem(r)

    def pick_logo(self):
        p,_=QFileDialog.getOpenFileName(self,"로고/이미지 선택","","Images (*.png *.jpg *.jpeg *.webp)")
        if p:
            self.logo=p; self.logo_label.setText(os.path.basename(p)); self.preview.set_logo(p,self.lw.value(),self.lx.value(),self.ly.value())

    def set_status(self,p,s):
        self.prog.setValue(p); self.status.setText(s)

    def do_analyze(self):
        if not self.paths:
            QMessageBox.warning(self,"확인","먼저 원본 영상을 추가하세요."); return
        self.script_btn.setEnabled(False); self.render.setEnabled(False); self.script.clear(); self.segs=[]
        self.analyze.setEnabled(False)
        self.worker=AnalyzeWorker(self.paths,int(self.target.currentText()))
        self.worker.status.connect(self.set_status)
        self.worker.done.connect(self.analyzed)
        self.worker.failed.connect(self.fail)
        self.worker.start()

    def analyzed(self,segs):
        self.analyze.setEnabled(True); self.segs=segs; self.refresh_table(); self.preview_slider.setRange(0,max(0,len(segs)-1)); self.preview_slider.setValue(0); self.show_preview_segment(0)
        self.script_btn.setEnabled(bool(segs)); self.set_status(100,f"{len(segs)}개 구간 선택 완료. 이제 대본을 만드세요.")
        if not segs: QMessageBox.warning(self,"분석 결과","사용 가능한 장면을 찾지 못했습니다.")

    def refresh_table(self):
        self.table.setRowCount(len(self.segs))
        for r,s in enumerate(self.segs):
            vals=[str(r+1),os.path.basename(s.path),f"{s.start:.1f}",f"{s.end:.1f}",f"{s.duration:.1f}",s.line]
            for c,v in enumerate(vals): self.table.setItem(r,c,QTableWidgetItem(v))

    def do_script(self):
        if not self.segs:
            QMessageBox.warning(self,"확인","영상 분석부터 해야 합니다."); return
        lines=make_script(self.desc.text(),self.segs,self.script_style.currentText())
        for s,line in zip(self.segs,lines): s.line=line
        self.script.setPlainText("\n".join(lines)); self.refresh_table(); self.render.setEnabled(True)
        self.status.setText("대본 생성 완료. 장면 순서에 맞춰 줄별로 연결했습니다."); self.show_preview_segment(self.preview_slider.value())

    def sync_script_from_box(self):
        lines=[x.strip() for x in self.script.toPlainText().splitlines() if x.strip()]
        if len(lines)!=len(self.segs):
            return False
        for s,l in zip(self.segs,lines): s.line=l
        return True

    def preview_voice(self):
        key=self.api_key.text().strip(); vid=self.voice.currentData()
        if not key or not vid:
            QMessageBox.warning(self,"Typecast","API 키를 입력하고 '음성 불러오기'를 먼저 누르세요."); return
        try:
            import winsound, hashlib
            # 같은 음성 미리듣기는 캐시해서 반복 크레딧 사용을 줄임
            cache=Path(tempfile.gettempdir())/("threeguys_tc_"+hashlib.md5((vid+str(self.rate.value())).encode()).hexdigest()+".wav")
            if not cache.exists(): synthesize_typecast("쓰리가이즈 특수청소입니다.",cache,key,vid,max(0.7,min(1.3,1.0+self.rate.value()*0.05)))
            winsound.PlaySound(str(cache),winsound.SND_FILENAME|winsound.SND_ASYNC)
        except Exception as e: QMessageBox.critical(self,"Typecast TTS 오류",str(e))

    def overlay_changed(self,w,x,y):
        self.lw.blockSignals(True); self.lx.blockSignals(True); self.ly.blockSignals(True)
        self.lw.setValue(w); self.lx.setValue(x); self.ly.setValue(y)
        self.lw.blockSignals(False); self.lx.blockSignals(False); self.ly.blockSignals(False)

    def show_preview_segment(self,idx):
        if not self.segs or idx<0 or idx>=len(self.segs): return
        s=self.segs[idx]; cap=cv2.VideoCapture(s.path); cap.set(cv2.CAP_PROP_POS_MSEC,s.start*1000); ok,frame=cap.read(); cap.release()
        if ok: self.preview.set_frame(frame)
        self.preview.set_caption(s.line or f"장면 {idx+1} 자막 미리보기")
        self.preview.set_banner(self.banner.isChecked())
        if self.logo and not self.preview.logo_item: self.preview.set_logo(self.logo,self.lw.value(),self.lx.value(),self.ly.value())

    def preview_seek(self,v): self.show_preview_segment(v)
    def toggle_preview(self):
        if not self.segs: return
        if self.preview_timer.isActive(): self.preview_timer.stop(); self.play_btn.setText("▶ 미리보기 재생")
        else: self.preview_timer.start(1100); self.play_btn.setText("■ 정지")
    def preview_next(self):
        if not self.segs: self.preview_timer.stop(); return
        n=self.preview_slider.value()+1
        if n>=len(self.segs): n=0
        self.preview_slider.setValue(n)

    def do_render(self):
        if not self.segs: return
        if not self.sync_script_from_box():
            QMessageBox.warning(self,"대본 줄 수","대본은 선택된 장면 수와 같은 줄 수여야 합니다. 한 장면당 한 줄로 맞춰주세요."); return
        out,_=QFileDialog.getSaveFileName(self,"완성 MP4 저장","ThreeGuys_Shorts.mp4","MP4 (*.mp4)")
        if not out: return
        self.render.setEnabled(False); self.analyze.setEnabled(False); self.script_btn.setEnabled(False)
        key=self.api_key.text().strip(); vid=self.voice.currentData()
        if not key or not vid:
            QMessageBox.warning(self,"Typecast","Typecast API 키와 음성을 선택하세요."); self.render.setEnabled(True); return
        self.settings.setValue("typecast_api_key",key)
        self.rworker=RenderWorker(self.segs,out,key,vid,self.rate.value(),self.logo,self.lw.value(),self.lx.value(),self.ly.value(),self.effects.isChecked(),self.banner.isChecked())
        self.rworker.status.connect(self.set_status); self.rworker.done.connect(self.render_done); self.rworker.failed.connect(self.fail)
        self.rworker.start()

    def render_done(self,out):
        self.render.setEnabled(True); self.analyze.setEnabled(True); self.script_btn.setEnabled(True); self.set_status(100,"MP4 제작 완료")
        QMessageBox.information(self,"완료",f"완성되었습니다.\n\n{out}")
        try: os.startfile(os.path.dirname(out))
        except Exception: pass

    def fail(self,msg):
        self.analyze.setEnabled(True); self.script_btn.setEnabled(bool(self.segs)); self.render.setEnabled(bool(self.segs and self.script.toPlainText().strip()))
        self.set_status(0,"오류 발생")
        QMessageBox.critical(self,"오류",msg[-7000:])

if __name__ == '__main__':
    app=QApplication(sys.argv)
    app.setStyle('Fusion')
    w=Main(); w.show()
    sys.exit(app.exec())
