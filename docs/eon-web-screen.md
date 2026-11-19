# 이온웹 전체 화면 캡처

기존 `/api/screen.jpg`는 Android `/system/bin/screencap -p`만 실행했다.
명령 실패·시간 초과·이미지 변환 실패는 모두 503과 일반적인 대기 문구로 숨겨졌다.
Android compositor 캡처만으로 Qt UI의 표면을 얻는다고 가정하지 않는다.

이제 `MainWindow`의 `WebScreenCapture`가 창과 자식 위젯을 직접 캡처한다.
QWidget/QPixmap 접근은 GUI 스레드에서, 축소·이미지 인코딩·원자적 파일 교체는
별도 스레드에서 실행한다. 최대 960×540, 1초에 한 번이다. JPEG를 우선 사용하고
Qt JPEG 플러그인이 없으면 PNG를 사용하며 웹 응답 MIME도 맞춘다.

웹이 열려 있는 동안에만 `/dev/shm/eon-web-screen.request`에 monotonic 시간을
갱신한다. 요청이 3초 동안 없으면 캡처를 멈춘다. 파일 저장은 메모리 파일시스템의
단일 최신 프레임으로 제한하며, 과거 파일은 영상 기록으로 남기지 않는다.

## 업데이트 후

이번 변경에는 **Qt UI 네이티브 재빌드·재시작**이 필요하다. `g_blinker`를 업데이트한
뒤 재부팅하고 UI 빌드가 완료될 때까지 기다린다. 일반 업데이트에서 UI가 다시
빌드되지 않았다면 이온웹 도구의 `재부팅하여 전체 재빌드`를 사용한다.
폰의 이온웹 앱·이온링크 APK를 바꾸는 작업은 필요하지 않다.

화면 보기에서 실패하면 이제 UI 재빌드/재시작 대기, 숨겨진 UI 창, 이미지 인코딩,
파일 쓰기 등의 원인을 표시한다. 3초 이상 오래되거나 잘못된 프레임을 성공한
화면처럼 계속 표시하지 않는다. Android `screencap` 서브프로세스는 호출하지 않는다.

## 검증

- Python: `python -m pytest -q selfdrive/test/test_eon_web_screen.py`.
- Qt: SCons의 `--test` 빌드에 `selfdrive/ui/tests/test_web_screen`을 추가했다.
  `QT_QPA_PLATFORM=offscreen selfdrive/ui/tests/test_web_screen`으로 위젯·인코딩·
  이벤트 루프 응답·요청 만료를 확인한다.
- OpenGL 자식 위젯도 검사하려면 GL 컨텍스트를 지원하는 X/가상 X 환경에서
  `TEST_WEB_SCREEN_GL=1 selfdrive/ui/tests/test_web_screen`을 실행한다.
- 호스트 Qt 5.15에서 일반 위젯과 OpenGL 위젯의 실제 캡처를 검증했고, 생성된
  바이너리 프레임을 Python `ScreenPreview`로 읽는 것까지 확인했다.
- 전체 EON UI 링크 빌드와 실제 EON 그래픽 드라이버에서의 동작 확인은 남아 있다.
  호스트 검증 결과를 실기기 화면 출력의 확인으로 대신하지 않는다.
