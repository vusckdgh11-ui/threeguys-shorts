# ThreeGuys Shorts V3

이번 버전 핵심 수정:
- 영상 컷 1개 = 대본 1줄 = TTS 1개 구조로 변경
- TTS 실제 길이를 측정해 각 영상 컷 길이와 자막 시간을 맞춤
- 미리보기 영역을 가운데 고정 9:16 패널로 분리해 UI가 밀리지 않도록 변경
- 로고 선택 후 모서리 핸들을 마우스로 드래그해 크기 조절, 본체 드래그로 이동
- TTS 공급자 분리: Windows 기본 음성 / Google 무료(gTTS) / Typecast
- Typecast API 키는 Windows DPAPI로 암호화해 로컬 설정에 저장
- 자막 스타일 선택 및 위치/크기 조절 유지

GitHub 저장소에서 app.py, requirements.txt를 덮어쓴 뒤 Actions가 자동으로 Windows EXE를 빌드합니다.
