import os, sys, math, json, wave, tempfile, subprocess, traceback, re
from pathlib import Path
from dataclasses import dataclass
from typing import List

import cv2
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QListWidget, QFileDialog, QLabel, QLineEdit, QComboBox, QSpinBox, QTextEdit,
    QTableWidget, QTableWidgetItem, QMessageBox, QProgressBar, QGroupBox,
    QFormLayout, QCheckBox
)

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


def make_script(description: str, segs: List[Segment]):
    desc = description.strip() or "특수청소 현장"
    templates = [
        f"{desc}. 현장에 도착하자마자 분위기부터 심상치 않았습니다.",
        "겉으로 보이는 부분만 닦는다고 끝나는 상황이 아니었습니다.",
        "오염이 어디까지 번졌는지 먼저 하나씩 확인합니다.",
        "눈에 잘 안 보이는 곳까지 확인하지 않으면 냄새와 오염이 다시 남을 수 있습니다.",
        "그래서 오염 상태에 맞는 전용 약품으로 반응을 확인하면서 작업합니다.",
        "한 번 닦고 끝내는 게 아니라, 남아 있는 부분을 다시 확인하고 반복 처리합니다.",
        "가구 주변과 틈새처럼 놓치기 쉬운 곳도 그냥 지나가지 않습니다.",
        "이런 현장은 빠르게 치우는 것보다 제대로 확인하고 처리하는 게 더 중요합니다.",
        "직접 하기 어려운 현장일수록 전문 장비와 절차가 필요한 이유입니다.",
        "이런 곳까지 누가 청소하냐고요? 저희가 합니다.",
        "쓰리가이즈 특수청소. 서울·경기 수도권 24시간 상담 가능합니다."
    ]
    lines = []
    n = len(segs)
    for i in range(n):
        if i == 0:
            line = templates[0]
        elif i == n - 1:
            line = templates[-1]
        else:
            line = templates[1 + ((i - 1) % (len(templates)-2))]
        lines.append(line)
    return lines


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


def render_video(segs: List[Segment], out_mp4, voice_idx, voice_rate, logo_path="", logo_w=300, logo_x=740, logo_y=220, effects=True, banner=True, progress=None):
    tmp = Path(tempfile.mkdtemp(prefix="threeguys_"))
    try:
        # TTS: 전체 대본을 한 번에 합성
        tts_wav = tmp / "voice.wav"
        full_text = " ".join(s.line for s in segs if s.line.strip())
        synthesize_sapi(full_text, tts_wav, voice_idx, voice_rate)
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
                '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', ffpath(out_mp4)]

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
    def __init__(self, segs, out, voice_idx, voice_rate, logo, lw, lx, ly, effects, banner):
        super().__init__(); self.args=(segs,out,voice_idx,voice_rate,logo,lw,lx,ly,effects,banner)
    def run(self):
        try:
            render_video(*self.args, progress=lambda p,s:self.status.emit(p,s))
            self.done.emit(self.args[1])
        except Exception:
            self.failed.emit(traceback.format_exc())


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 860)
        self.paths=[]; self.segs=[]; self.logo=""; self.voices=[]
        root=QWidget(); self.setCentralWidget(root); lay=QVBoxLayout(root)

        title=QLabel("쓰리가이즈 쇼츠 자동제작")
        title.setStyleSheet("font-size:24px;font-weight:700")
        lay.addWidget(title)
        sub=QLabel("영상 분석 → 쓸 장면 자동 선택 → 타임라인 기반 대본 → Windows TTS → 자동 자막 → MP4")
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

        g3=QGroupBox("3) TTS / 로고"); f3=QFormLayout(g3)
        self.voice=QComboBox(); f3.addRow("Windows TTS 음성", self.voice)
        self.rate=QSpinBox(); self.rate.setRange(-5,5); self.rate.setValue(0); f3.addRow("말하기 속도", self.rate)
        pv=QPushButton("음성 미리듣기"); pv.clicked.connect(self.preview_voice); f3.addRow(pv)
        lr=QHBoxLayout(); self.logo_label=QLabel("없음"); lb=QPushButton("로고/이미지 선택"); lb.clicked.connect(self.pick_logo); lr.addWidget(lb); lr.addWidget(self.logo_label); f3.addRow(lr)
        self.lw=QSpinBox(); self.lw.setRange(30, 2500); self.lw.setValue(320); f3.addRow("로고 폭(px)", self.lw)
        self.lx=QSpinBox(); self.lx.setRange(-1500, 2500); self.lx.setValue(720); f3.addRow("로고 X", self.lx)
        self.ly=QSpinBox(); self.ly.setRange(-1500, 3000); self.ly.setValue(210); f3.addRow("로고 Y", self.ly)
        self.effects=QCheckBox("자동 펀치인 효과"); self.effects.setChecked(True); f3.addRow(self.effects)
        self.banner=QCheckBox("상단 고정 광고 배너"); self.banner.setChecked(True); f3.addRow(self.banner)
        left.addWidget(g3)

        g4=QGroupBox("선택된 타임라인 (분석 후 생성)"); gr=QVBoxLayout(g4)
        self.table=QTableWidget(0,6); self.table.setHorizontalHeaderLabels(["순서","파일","시작","끝","길이","대본"])
        self.table.horizontalHeader().setStretchLastSection(True); gr.addWidget(self.table)
        right.addWidget(g4, 2)

        g5=QGroupBox("타임라인 기반 대본"); gs=QVBoxLayout(g5)
        self.script=QTextEdit(); self.script.setPlaceholderText("영상 분석 후 대본 만들기를 누르세요."); gs.addWidget(self.script)
        self.script_btn=QPushButton("2. 타임라인 기반 대본 만들기"); self.script_btn.setEnabled(False); self.script_btn.clicked.connect(self.do_script); gs.addWidget(self.script_btn)
        right.addWidget(g5,1)

        self.render=QPushButton("3. MP4 자동 제작")
        self.render.setEnabled(False); self.render.clicked.connect(self.do_render)
        self.render.setMinimumHeight(48); self.render.setStyleSheet("font-size:17px;font-weight:700")
        right.addWidget(self.render)

        self.prog=QProgressBar(); lay.addWidget(self.prog)
        self.status=QLabel("준비됨"); lay.addWidget(self.status)
        self.load_voices()

    def load_voices(self):
        try:
            self.voices=sapi_voices()
            for name, idx in self.voices: self.voice.addItem(name, idx)
        except Exception:
            self.voice.addItem("Windows 기본 음성", 0)

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
            self.logo=p; self.logo_label.setText(os.path.basename(p))

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
        self.analyze.setEnabled(True); self.segs=segs; self.refresh_table()
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
        lines=make_script(self.desc.text(),self.segs)
        for s,line in zip(self.segs,lines): s.line=line
        self.script.setPlainText("\n".join(lines)); self.refresh_table(); self.render.setEnabled(True)
        self.status.setText("대본 생성 완료. 장면 순서에 맞춰 줄별로 연결했습니다.")

    def sync_script_from_box(self):
        lines=[x.strip() for x in self.script.toPlainText().splitlines() if x.strip()]
        if len(lines)!=len(self.segs):
            return False
        for s,l in zip(self.segs,lines): s.line=l
        return True

    def preview_voice(self):
        if os.name != 'nt':
            QMessageBox.information(self,"TTS","Windows 실행 파일에서 사용할 수 있습니다."); return
        try:
            import winsound
            p=Path(tempfile.gettempdir())/'threeguys_tts_preview.wav'
            idx=self.voice.currentData() or 0
            synthesize_sapi("쓰리가이즈 특수청소. 음성 미리듣기입니다.",p,idx,self.rate.value())
            winsound.PlaySound(str(p),winsound.SND_FILENAME|winsound.SND_ASYNC)
        except Exception as e:
            QMessageBox.critical(self,"TTS 오류",str(e))

    def do_render(self):
        if not self.segs: return
        if not self.sync_script_from_box():
            QMessageBox.warning(self,"대본 줄 수","대본은 선택된 장면 수와 같은 줄 수여야 합니다. 한 장면당 한 줄로 맞춰주세요."); return
        out,_=QFileDialog.getSaveFileName(self,"완성 MP4 저장","ThreeGuys_Shorts.mp4","MP4 (*.mp4)")
        if not out: return
        self.render.setEnabled(False); self.analyze.setEnabled(False); self.script_btn.setEnabled(False)
        self.rworker=RenderWorker(self.segs,out,self.voice.currentData() or 0,self.rate.value(),self.logo,self.lw.value(),self.lx.value(),self.ly.value(),self.effects.isChecked(),self.banner.isChecked())
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
