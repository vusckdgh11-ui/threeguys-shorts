import os, sys, re, math, wave, tempfile, subprocess, traceback, requests, hashlib, base64, shutil, textwrap
from pathlib import Path
from dataclasses import dataclass, replace
from typing import List
import ai_editor

import cv2
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSettings, QPointF, QRectF
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QListWidget, QFileDialog, QLabel, QLineEdit, QComboBox, QSpinBox, QTextEdit,
    QTableWidget, QTableWidgetItem, QMessageBox, QProgressBar, QGroupBox,
    QFormLayout, QCheckBox, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsTextItem, QSlider, QSplitter, QScrollArea, QAbstractItemView, QGraphicsRectItem
)
from PySide6.QtGui import QImage, QPixmap, QFont, QColor, QPen, QBrush, QPainter, QFontDatabase

APP_NAME = "ThreeGuys Shorts V4.1 · 현장 이야기 AI 편집"
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
    visual_description: str = ""
    selection_reason: str = ""
    visual_evidence: str = ""
    scene_id: str = ""

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


def timeline_ranges(segs):
    """One ordered clock shared by the table and burned-in captions."""
    ranges=[]; position=0.0
    for s in segs:
        end=position+s.duration
        ranges.append((position,end)); position=end
    return ranges


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
    for s,(t,end) in zip(segs,timeline_ranges(segs)):
        wrapped='\n'.join(textwrap.wrap(s.line, width=max(6,int(900 / max(1,cap_size))), break_long_words=True))
        txt = emphasize_ass(wrapped, caption_style)
        events.append(f"Dialogue: 0,{ass_time(t)},{ass_time(end)},Caption,,0,0,0,,{{\\an5\\pos({int(cap_x)},{int(cap_y)})}}{txt}")
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





def ai_segments(paths, target, key, model, brief, style, step, progress):
    client=ai_editor.VisionClient(key,model)
    scenes=ai_editor.analyze_sources(paths,client,brief,step,progress)
    progress(75,'AI가 내용과 중복을 고려해 타임라인을 정리하는 중...')
    cuts=ai_editor.plan_timeline(scenes,target,client,brief)
    cuts=ai_editor.refine_timeline(cuts,client,lambda n,total:progress(76+round(8*n/total),f'선택 컷 동작·시각 정밀 확인 {n}/{total}'))
    progress(85,'선택한 컷의 실제 프레임을 다시 보고 대본 작성 중...')
    lines=ai_editor.write_script(cuts,client,brief,style)
    return [Segment(c.path,c.start,c.end,c.confidence*100,line['text'],
                    visual_description=c.description,selection_reason=c.reason,
                    visual_evidence=line['visual_evidence'],scene_id=c.scene_id)
            for c,line in zip(cuts,lines)]


class AIEditWorker(QThread):
    done=Signal(object); failed=Signal(str); status=Signal(int,str)
    def __init__(self, options, render_options=None):
        super().__init__(); self.options=options; self.render_options=render_options
    def run(self):
        try:
            scale=0.5 if self.render_options else 1
            segs=ai_segments(*self.options,progress=lambda p,s:self.status.emit(int(p*scale),s))
            if self.render_options:
                render_video(segs,**self.render_options,progress=lambda p,s:self.status.emit(50+int(p*0.5),s))
            self.done.emit(segs)
        except Exception as exc:
            # Do not log request objects, frames, headers or keys.
            self.failed.emit(str(exc))
        finally:
            self.options=None; self.render_options=None


class AIScriptWorker(QThread):
    done=Signal(object); failed=Signal(str)
    def __init__(self,segs,key,model,brief,style):
        super().__init__(); self.segs=[replace(s) for s in segs]
        self.key,self.model,self.brief,self.style=key,model,brief,style
    def run(self):
        try:
            cuts=[ai_editor.Scene(s.scene_id,s.path,s.start,s.end,s.visual_description,'','',s.selection_reason,s.score/100) for s in self.segs]
            lines=ai_editor.write_script(cuts,ai_editor.VisionClient(self.key,self.model),self.brief,self.style)
            for s,line in zip(self.segs,lines):
                s.line=line['text']; s.visual_evidence=line['visual_evidence']; s.voice_duration=s.play_duration=0
            self.done.emit(self.segs)
        except Exception as exc: self.failed.emit(str(exc))
        finally: self.key=''


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


class LogoResizeHandle(QGraphicsRectItem):
    """A screen-sized hit target, including on transparent image corners."""
    def __init__(self, owner, corner, pos):
        super().__init__(-7,-7,14,14,owner)
        self.owner,self.corner=owner,corner
        self.old_rect=None
        self.setPos(pos)
        self.setFlag(QGraphicsRectItem.ItemIgnoresTransformations,True)
        self.setBrush(QBrush(QColor('white')))
        self.setPen(QPen(QColor('#008cff'),2))
        self.setCursor(Qt.SizeFDiagCursor if corner in ('tl','br') else Qt.SizeBDiagCursor)
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.setZValue(100)

    def mousePressEvent(self,event):
        self.owner.setSelected(True)
        self.old_rect=self.owner.mapRectToScene(QRectF(self.owner.pixmap().rect()))
        event.accept()

    def mouseMoveEvent(self,event):
        if self.old_rect is not None:
            self.owner.resize_to(self.corner,self.old_rect,event.scenePos())
        event.accept()

    def mouseReleaseEvent(self,event):
        if self.old_rect is not None:
            self.owner.resize_to(self.corner,self.old_rect,event.scenePos())
        self.old_rect=None
        event.accept()


class ResizePixmapItem(QGraphicsPixmapItem):
    def __init__(self,pixmap,changed_cb=None):
        super().__init__(pixmap)
        self.changed_cb=changed_cb
        self.orig_w=max(1,pixmap.width())
        self.setShapeMode(QGraphicsPixmapItem.BoundingRectShape)
        self.setFlags(QGraphicsPixmapItem.ItemIsMovable | QGraphicsPixmapItem.ItemIsSelectable)
        self.setZValue(20)
        r=QRectF(pixmap.rect())
        self.handles={name:LogoResizeHandle(self,name,point) for name,point in
                      [('tl',r.topLeft()),('tr',r.topRight()),('bl',r.bottomLeft()),('br',r.bottomRight())]}

    def resize_to(self,corner,r,point):
        anchor_x=r.right() if corner in ('tl','bl') else r.left()
        anchor_y=r.bottom() if corner in ('tl','tr') else r.top()
        sx=-1 if corner in ('tl','bl') else 1
        sy=-1 if corner in ('tl','tr') else 1
        dx,dy=point.x()-anchor_x,point.y()-anchor_y
        factor=(dx*sx*r.width()+dy*sy*r.height())/(r.width()**2+r.height()**2)
        width=max(30.0,min(10000.0,r.width()*factor))
        self.setScale(width/self.orig_w)
        height=self.pixmap().height()*self.scale()
        self.setPos(anchor_x-width if sx<0 else anchor_x,anchor_y-height if sy<0 else anchor_y)
        if self.changed_cb: self.changed_cb()

    def mouseMoveEvent(self,event):
        super().mouseMoveEvent(event)
        if self.changed_cb: self.changed_cb()

    def mouseReleaseEvent(self,event):
        super().mouseReleaseEvent(event)
        if self.changed_cb: self.changed_cb()



class CaptionItem(QGraphicsTextItem):
    def __init__(self, changed_cb=None):
        super().__init__(); self.changed_cb = changed_cb
        self.setFlags(QGraphicsTextItem.ItemIsMovable | QGraphicsTextItem.ItemIsSelectable)
        self.setDefaultTextColor(QColor('white'))
        font=QFont('Malgun Gothic',weight=QFont.Bold); font.setPixelSize(74); self.setFont(font)
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
        center=self.caption.boundingRect().center()
        cx = int(p.x() + center.x() * self.caption.scale())
        cy = int(p.y() + center.y() * self.caption.scale())
        self.caption_changed.emit(cx, cy, size)

    def set_caption(self, text):
        center=self.caption.mapToScene(self.caption.boundingRect().center())
        safe = (text or "자막 미리보기").replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        self.caption.setHtml(f"<div align='center'>{safe}</div>")
        self.set_caption_geometry(center.x(),center.y(),74*self.caption.scale())

    def set_caption_geometry(self, cx, cy, size):
        scale = max(0.35, min(3.5, size / 74.0))
        self.caption.setScale(scale)
        center=self.caption.boundingRect().center()
        self.caption.setPos(cx - center.x()*scale, cy - center.y()*scale)

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
        limitation=QLabel("AI가 시간별 영상 프레임을 인식 → 필요한 컷 정리 → 선택 컷을 다시 보고 대본 작성 → 컷별 TTS·자막·MP4")
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
        self.desc=QTextEdit(); self.desc.setFixedHeight(100)
        self.desc.setPlaceholderText("현장 배경, 확인된 사실, 결과, 원하는 이야기 분위기를 적어주세요.\n예: 자살시도 후 생존 확인. 남은 현장을 정리한 작업. 안도감 있는 이야기.")
        f.addRow("현장 설명",self.desc)
        self.target=QComboBox(); self.target.addItems(["40","50","60"]); self.target.setCurrentText("50"); f.addRow("목표 길이",self.target)
        self.analyze=QPushButton("1. AI 영상 인식 + 컷·대본 만들기"); self.analyze.clicked.connect(self.do_analyze); f.addRow(self.analyze); left.addWidget(g2)

        ga=QGroupBox("영상 인식 AI 연결"); fa=QFormLayout(ga)
        self.ai_key=QLineEdit(); self.ai_key.setEchoMode(QLineEdit.Password)
        self.ai_key.setPlaceholderText("OpenAI API 키 (Typecast 키와 별개)")
        self.ai_key.setText(unprotect_secret(self.settings.value('vision_key_dpapi','')))
        fa.addRow("AI API 키",self.ai_key)
        self.ai_model=QLineEdit(self.settings.value('vision_model',ai_editor.DEFAULT_MODEL)); fa.addRow("AI 모델",self.ai_model)
        self.sample_step=QComboBox(); self.sample_step.addItem('정밀 · 0.5초마다',0.5); self.sample_step.addItem('기본 · 1초마다',1.0); self.sample_step.addItem('절약 · 2초마다',2.0); self.sample_step.setCurrentIndex(1)
        fa.addRow("분석 간격",self.sample_step)
        ai_note=QLabel("분석 버튼을 누르면 영상 프레임과 설명이 OpenAI API로 전송되며 API 사용료가 발생합니다. 원본 음성은 분석하지 않습니다. 짧은 동작은 놓칠 수 있습니다.")
        ai_note.setWordWrap(True); fa.addRow(ai_note)
        self.ai_key_status=QLabel('키는 Windows 암호화 저장만 사용합니다.'); self.ai_key_status.setWordWrap(True); fa.addRow(self.ai_key_status)
        forget=QPushButton('AI 키 저장 삭제'); forget.clicked.connect(self.forget_ai_key); fa.addRow(forget); left.addWidget(ga)

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
        self.table=QTableWidget(0,10); self.table.setHorizontalHeaderLabels(["#","파일","원본 시작","원본 끝","출력 시작","출력 끝","TTS","AI 인식 내용","선택 이유","대본"]); self.table.horizontalHeader().setStretchLastSection(True); self.table.setMinimumHeight(300); self.table.setSelectionBehavior(QAbstractItemView.SelectRows); gr.addWidget(self.table); right.addWidget(g4)
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
        self.script_btn=QPushButton("2. 현재 컷을 AI가 다시 보고 대본 작성"); self.script_btn.setEnabled(False); self.script_btn.clicked.connect(self.do_script); gs.addWidget(self.script_btn)
        right.addWidget(g5)

        self.render=QPushButton("3. 컷별 TTS 동기화 후 MP4 자동 제작"); self.render.setEnabled(False); self.render.clicked.connect(self.do_render); self.render.setMinimumHeight(50); self.render.setStyleSheet("font-size:17px;font-weight:700"); right.addWidget(self.render); right.addStretch(1)
        self.auto_btn=QPushButton("AI 영상 인식부터 MP4까지 한 번에"); self.auto_btn.setMinimumHeight(52)
        self.auto_btn.clicked.connect(self.do_auto); outer.addWidget(self.auto_btn)

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
                   for name in ('worker','rworker','voice_worker','ai_worker','script_worker'))

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
            if not (0 <= start < end <= get_video_duration(s.path)) or end-start<1.2: raise ValueError()
        except ValueError:
            QMessageBox.warning(self,"컷 범위","원본 안에서 1.2초 이상의 시작초, 끝초를 입력하세요."); return
        s.start,s.end=start,end; s.play_duration=0; s.voice_duration=0
        s.line=''; s.visual_evidence=''; self.script.setPlainText('\n'.join(s.line for s in self.segs)); self.render.setEnabled(False)
        self.status.setText('컷 범위가 바뀌었습니다. AI 대본을 다시 작성하세요.')
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
        key=self.get_ai_key()
        if not key: return
        self.start_ai(key)

    def get_ai_key(self):
        key=self.ai_key.text().strip()
        if not key:
            QMessageBox.warning(self,'영상 인식 AI','OpenAI API 키가 필요합니다. 채팅이 아닌 이 프로그램의 AI API 키 칸에 입력하세요.'); return ''
        encrypted=protect_secret(key)
        self.settings.remove('vision_key_dpapi')
        if encrypted:
            self.settings.setValue('vision_key_dpapi',encrypted); self.ai_key_status.setText('AI 키를 Windows 암호화 저장했습니다.')
        else: self.ai_key_status.setText('이 환경에서는 암호화 저장이 불가하여 이번 실행 메모리에서만 사용합니다.')
        self.settings.setValue('vision_model',self.ai_model.text().strip()); self.settings.sync()
        return key

    def forget_ai_key(self):
        self.ai_key.clear(); self.settings.remove('vision_key_dpapi'); self.settings.sync(); self.ai_key_status.setText('저장된 AI 키를 삭제했습니다.')

    def set_edit_busy(self,on):
        self.analyze.setEnabled(not on); self.auto_btn.setEnabled(not on)
        self.script_btn.setEnabled(not on and bool(self.segs)); self.render.setEnabled(not on and bool(self.segs) and all(s.line for s in self.segs))
        self.script.setReadOnly(on)

    def start_ai(self,key,render_options=None):
        self.invalidate_timeline(); self.set_edit_busy(True)
        self.auto_output=render_options['out_mp4'] if render_options else ''
        options=(list(self.paths),int(self.target.currentText()),key,self.ai_model.text().strip(),self.desc.toPlainText(),self.script_style.currentText(),self.sample_step.currentData())
        self.ai_worker=AIEditWorker(options,render_options)
        self.ai_worker.status.connect(self.set_status); self.ai_worker.done.connect(self.ai_finished); self.ai_worker.failed.connect(self.fail); self.ai_worker.start()

    def ai_finished(self,segs):
        self.segs=segs; self.script.setPlainText('\n'.join(s.line for s in segs)); self.set_edit_busy(False)
        self.refresh_table(); self.preview_slider.setRange(0,max(0,len(segs)-1)); self.show_preview_segment(0)
        self.set_status(100,f'AI 영상 인식·컷 정리·장면 근거 대본 완료 — {len(segs)}개 컷')
        if self.auto_output:
            QMessageBox.information(self,'AI 자동 제작 완료',f'MP4를 저장했습니다.\n{self.auto_output}')

    def do_auto(self):
        if self.busy(): return
        if not self.paths: QMessageBox.warning(self,'영상','원본 영상을 먼저 추가하세요.'); return
        key=self.get_ai_key()
        if not key: return
        if not self.voice.count(): QMessageBox.warning(self,'TTS','TTS 음성을 먼저 선택하세요.'); return
        if self.provider.currentText()=='Typecast' and not self.api_key.text().strip():
            QMessageBox.warning(self,'TTS','Typecast API 키를 입력하세요.'); return
        out,_=QFileDialog.getSaveFileName(self,'AI 완성 MP4 저장','ThreeGuys_AI_Shorts.mp4','MP4 (*.mp4)')
        if not out: return
        options=dict(out_mp4=out,provider=self.provider.currentText(),voice_data=self.voice.currentData(),api_key=self.api_key.text().strip(),voice_rate=self.rate.value(),
            logo_path=self.logo,logo_w=self.lw.value(),logo_x=self.lx.value(),logo_y=self.ly.value(),effects=self.effects.isChecked(),banner=self.banner.isChecked(),
            caption_style=self.caption_style.currentText(),cap_x=self.cap_x,cap_y=self.cap_y,cap_size=self.cap_size)
        self.start_ai(key,options)

    def analyzed(self,segs):
        self.analyze.setEnabled(True); self.segs=segs; self.refresh_table(); self.preview_slider.setRange(0,max(0,len(segs)-1)); self.preview_slider.setValue(0); self.show_preview_segment(0); self.script_btn.setEnabled(bool(segs)); self.set_status(100,f"{len(segs)}개 컷 선택 완료. 이제 컷 기준 대본을 만드세요.")
        if not segs: QMessageBox.warning(self,"분석 결과","사용 가능한 장면을 찾지 못했습니다.")

    def refresh_table(self):
        self.length_info.setText(f"선택 {sum(s.source_duration for s in self.segs):.2f}초 / 목표 {self.target.currentText()}초 · "
                                 + (f"TTS 동기화 {sum(s.duration for s in self.segs):.2f}초" if any(s.voice_duration for s in self.segs) else "최종 길이는 TTS 합성 후 확정"))
        self.table.setRowCount(len(self.segs))
        for r,(s,(start,end)) in enumerate(zip(self.segs,timeline_ranges(self.segs))):
            vals=[str(r+1),os.path.basename(s.path),f"{s.start:.2f}",f"{s.end:.2f}",f"{start:.2f}",f"{end:.2f}",f"{s.voice_duration:.2f}s" if s.voice_duration else "-",s.visual_description,s.selection_reason,s.line]
            for c,v in enumerate(vals): self.table.setItem(r,c,QTableWidgetItem(v))
        self.table.resizeColumnsToContents(); self.table.horizontalHeader().setStretchLastSection(True)
        for col in (7,8,9): self.table.setColumnWidth(col,220)
        for row,s in enumerate(self.segs):
            self.table.item(row,9).setToolTip('대본 근거 (영상/현장 설명): '+s.visual_evidence)

    def do_script(self):
        if self.busy(): return
        if not self.segs: QMessageBox.warning(self,"확인","영상 분석부터 해야 합니다."); return
        key=self.get_ai_key()
        if not key: return
        self.set_edit_busy(True); self.status.setText('현재 컷의 실제 프레임을 AI가 다시 확인하는 중...')
        self.script_worker=AIScriptWorker(self.segs,key,self.ai_model.text().strip(),self.desc.toPlainText(),self.script_style.currentText())
        self.script_worker.done.connect(self.ai_script_finished); self.script_worker.failed.connect(self.fail); self.script_worker.start()

    def ai_script_finished(self,segs):
        self.segs=segs; self.script.setPlainText('\n'.join(s.line for s in segs)); self.set_edit_busy(False); self.refresh_table(); self.show_preview_segment(self.preview_slider.value())
        self.status.setText('실제 컷 프레임을 근거로 AI 대본을 다시 작성했습니다.')

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
        self.set_edit_busy(False)
        self.script.setReadOnly(False)
        self.analyze.setEnabled(True); self.script_btn.setEnabled(bool(self.segs)); self.render.setEnabled(bool(self.segs and self.script.toPlainText().strip())); self.set_status(0,"오류 발생"); QMessageBox.critical(self,"오류",msg[-7000:])


if __name__=='__main__':
    if '--self-test' in sys.argv:
        from smoke_test import run_checks
        run_checks(sys.modules[__name__],sys.argv[sys.argv.index('--self-test')+1])
        sys.exit(0)
    app=QApplication(sys.argv); app.setStyle('Fusion'); w=Main(); w.show(); sys.exit(app.exec())
