# S20 화면 (단계 7b)

- `s20uid.py` (chroot): openpilot 상태 + 모델 경로·차선·도로 경계·앞차를 도로 카메라 프레임(1928x1208) 좌표로
  투영해 `/s20share/ui.json` 에 초당 약 20번 기록
- `app/` (안드로이드 앱 `ai.s20.ui`, "s20 openpilot") — 폰의 **홈 화면(런처)** 으로 쓰여 c3처럼 동작:
  - 시작 중: ui.json 이 없거나 3초 넘게 안 바뀌면 콤마 로고 + 회전 링 스피너 ("openpilot 시작 중")
  - 오프로드: 홈 화면 — 왼쪽 상태 카드(온도·판다·카메라), 상태 줄(판다 연결/시동 대기), 오프로드 알림(Offroad_* 파라미터),
    버전·브랜치·저장공간. 카메라 GL 은 꺼서 GPU 를 비움. **탭: 카메라 미리보기, 길게 누르기: 안드로이드 설정**
  - 주행(onroad = deviceState.started 또는 모델 동작 중): 카메라 + 경로(인게이지 시 초록)·차선·도로 경계(빨강)·앞차·
    현재/설정 속도·경고·상태 테두리
- 설정 (홈 화면 사이드바의 **⚙ 설정**, 또는 길게 누르기) — 콤마 3X 설정 패널 구성:
  - 장치: Dongle ID, 시리얼, 차량, 캘리브레이션 초기화(현재 각도 표시), 재부팅/전원 끄기
  - 토글: openpilot 사용, 가속 페달로 해제, 주행 성향(공격적/표준/편안함), 초기 최대 속도, 차선 이탈 경고, 미터법
  - 소프트웨어: 버전·브랜치·커밋 / 개발자: UI 디버그, 조이스틱·종방향·횡방향 기동 모드, 종방향 알파
  - S20: 실행 모드(manager/demo), openpilot 다시 시작, 안드로이드 설정, 홈 앱 변경
  - 오파 설정값은 앱이 `/data/s20pilot/share/cmd/*.json` 에 명령을 쓰면 s20uid 가 `settings_bridge.py` 로 반영
    (허용된 키만, 인게이지 중엔 재시작 필요한 토글·캘리브레이션 초기화 막음 — 3X 와 같은 규칙). 재부팅·모드 전환은 앱이 root(Magisk) 사용
- `bootanim/`: c3 같은 부팅 로고(콤마 + 회전 링) `bootanimation.zip`, `make_bootanim.py` 로 생성.
  `android/autostart.sh install` 이 Magisk 모듈로 설치 (다음 부팅부터)
  - 빌드: `app/build.sh` (Gradle 없이 aapt2 + d8 + apksigner), 결과 `app/s20ui.apk`
  - 앱은 `/data/s20pilot/share` 를 읽으므로 SELinux permissive 필요 (enter_chroot.sh 가 설정)

설치·실행 (PC):
```
adb shell su -c "cp /data/s20pilot/rootfs/root/openpilot/s20/ui/app/s20ui.apk /data/local/tmp/ && pm install -r /data/local/tmp/s20ui.apk"
adb shell am start -n ai.s20.ui/.MainActivity
```
