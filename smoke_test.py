"""Offline integration check, also run from the packaged Windows executable."""
import json
import math
import tempfile
import wave
from pathlib import Path
from unittest.mock import patch


def run_checks(app, report_path):
    import numpy as np
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QPixmap, QColor
    import ai_editor
    qt = QApplication.instance() or QApplication([])
    app.load_fonts()
    report = {"status": "failed"}
    try:
        with tempfile.TemporaryDirectory(prefix="threeguys_check_") as folder:
            root = Path(folder)
            ff = app.ffmpeg_exe()
            source = root / "source.mp4"
            app.run_ffmpeg([ff,"-y","-f","lavfi","-i","color=c=red:s=160x284:r=30:d=1",
                            "-f","lavfi","-i","color=c=blue:s=160x284:r=30:d=1",
                            "-filter_complex","[0:v][1:v]concat=n=2:v=1:a=0[v]",
                            "-map","[v]","-c:v","libx264","-pix_fmt","yuv420p",str(source)])
            logo = QPixmap(120,80); logo.fill(QColor("lime"))
            logo_path=root / "logo.png"; logo.save(str(logo_path))
            view=app.PreviewView(); view.set_logo(str(logo_path),120,50,200)
            changed=[]; view.logo_changed.connect(lambda *args: changed.append(args)); view.emit_logo()
            assert changed[-1] == (120,50,200), changed
            assert view.logo_item.mapRectToScene(app.QRectF(view.logo_item.pixmap().rect())).width() == 120

            durations={"첫 컷":0.6,"두 번째 컷":2.15,"마지막 컷":0.3}
            frequencies={"첫 컷":440,"두 번째 컷":880,"마지막 컷":1320}
            def fake_tts(provider,text,path,*args):
                t=np.arange(round(48000*durations[text]))/48000
                samples=(np.sin(2*np.pi*frequencies[text]*t)*12000).astype('<i2')
                with wave.open(str(path),'wb') as wav:
                    wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(48000); wav.writeframes(samples.tobytes())
            segs=[app.Segment(str(source),0,0.8,100,line) for line in durations]
            output=root / "output.mp4"
            with patch.object(app,"synthesize_line",fake_tts):
                app.render_video(segs,str(output),"test",None,"",0,str(logo_path),120,50,200,True,True)
            expected=sum(s.duration for s in segs)
            assert abs(app.get_video_duration(str(output))-expected)<0.04
            cap=app.cv2.VideoCapture(str(output))
            assert (int(cap.get(app.cv2.CAP_PROP_FRAME_WIDTH)),int(cap.get(app.cv2.CAP_PROP_FRAME_HEIGHT))) == (1080,1920)
            # Long narration must never reveal the blue frames outside the chosen source interval.
            cap.set(app.cv2.CAP_PROP_POS_MSEC,(segs[0].duration+1.7)*1000)
            ok,frame=cap.read(); cap.release(); assert ok
            b,g,r=frame[1000,500]; assert r>180 and b<60,(int(b),int(g),int(r))
            b,g,r=frame[230,80]; assert g>180 and r<60,(int(b),int(g),int(r))
            audio=root / "decoded.wav"
            app.run_ffmpeg([ff,"-y","-i",str(output),"-vn","-ar","48000","-ac","1","-c:a","pcm_s16le",str(audio)])
            with wave.open(str(audio),'rb') as wav:
                samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2').astype(float)
            start=0
            for s in segs:
                chunk=samples[round((start+0.05)*48000):round((start+0.2)*48000)]
                spectrum=np.abs(np.fft.rfft(chunk)); frequency=np.fft.rfftfreq(len(chunk),1/48000)[spectrum.argmax()]
                assert abs(frequency-frequencies[s.line])<15,frequency
                start+=s.duration
            silence=samples[round(0.85*48000):round(1.2*48000)]
            assert np.sqrt(np.mean(silence**2))<100
            assert app.ass_time(59.999)=="0:01:00.00"
            # Verify the Windows-only modules survived packaging and DPAPI round-trips.
            if app.os.name == 'nt':
                import pythoncom, win32com.client, win32crypt
                secret=app.protect_secret("local-self-test")
                assert secret and app.unprotect_secret(secret)=="local-self-test"
            report={"status":"passed","duration_seconds":expected,"resolution":"1080x1920",
                    "ai_module_import":"passed","live_ai_api":"not tested: user API key required",
                    "checks":["selected source bounds","three TTS boundaries","short audio padding",
                              "burn-in render","logo geometry and overlay","bundled FFmpeg","DPAPI"]}
    except Exception as exc:
        report["error"]=str(exc)
        raise
    finally:
        Path(report_path).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report


if __name__ == '__main__':
    import app
    import sys
    run_checks(app,sys.argv[1] if len(sys.argv)>1 else "smoke-result.json")
