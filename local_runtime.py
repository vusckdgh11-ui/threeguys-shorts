"""Dedicated local Ollama process; large data stays in the configured E: folder."""
import os
from pathlib import Path
import subprocess
import threading
import time
import requests

_lock=threading.Lock()

def ensure_server():
    from ai_editor import AIError
    with _lock:
        session=requests.Session(); session.trust_env=False
        try:
            r=session.get('http://127.0.0.1:11435/api/version',timeout=2)
            if r.status_code==200: return
        except requests.RequestException: pass
        root=Path(os.environ.get('THREEGUYS_ROOT','E:/Codex'))
        executable=root/'Tools/Ollama/ollama.exe'
        if not executable.is_file():
            raise AIError('E:\\Codex\\Tools\\Ollama에 로컬 AI 도구가 설치되어 있지 않습니다.')
        temporary=root/'Temp'; temporary.mkdir(parents=True,exist_ok=True)
        models=root/'Models'; models.mkdir(parents=True,exist_ok=True)
        env=os.environ.copy()
        env.update(OLLAMA_MODELS=str(models),OLLAMA_HOST='127.0.0.1:11435',
                   OLLAMA_NO_CLOUD='1',OLLAMA_NUM_PARALLEL='1',OLLAMA_MAX_LOADED_MODELS='1',
                   TEMP=str(temporary),TMP=str(temporary))
        with (temporary/'ollama-server.log').open('ab') as log:
            subprocess.Popen([str(executable),'serve'],env=env,cwd=str(executable.parent),
                             stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for _ in range(60):
            try:
                if session.get('http://127.0.0.1:11435/api/version',timeout=2).status_code==200: return
            except requests.RequestException: pass
            time.sleep(0.5)
        raise AIError('PC AI를 시작하지 못했습니다. E:\\Codex\\Temp\\ollama-server.log를 확인하세요.')
