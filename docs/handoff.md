# 인수인계 (g_blinker) — 2026-11-26

새 세션은 이 문서를 먼저 읽고 시작합니다. 답변은 **한국어**로 합니다.
앞 세션: "일반 CAN 차량 깜박이 코드 테스트". 지금 주제: **갤럭시 S20 FE(865)로 오파 구동 검토**.

## 1. 차량과 기기
- 차량: 제네시스 DH (현대 HKG, 레이더 있음).
- 오파 본체: **EON(C2급)**, 이 저장소 `durangodh/openpilot`의 **g_blinker** 브랜치.
  - openpilot 0.8.x 계열입니다. 주행 모델은 `models/supercombo.onnx/.dlc`(SNPE)이고, UI는 Qt `selfdrive/ui`입니다.
- **차에 매립된 S20**: 핫스팟 "G80"(2.4GHz) + 네이버지도(LSPosed 모듈) + S20 HUD 앱. EON도 이 핫스팟을 씁니다.
  - 매립돼 있어서 폰 로그를 직접 보기 어렵습니다.
- **ESP32**(Waveshare ESP32-S3-RS485-CAN) 자동 깜빡이: B-CAN 0x138 원터치.
  - 배선: M01 1번 녹색 H / 2번 주황 L. 보드 CAN 점퍼 **NC**, 변환기 없이 직결, IGN 전원.
  - EON → ESP32: UDP 7290. 상태 브로드캐스트: UDP 7291.
  - 문서: `tools/dh_bcan_blinker/README.ko.md`.

## 2. 규칙 (계속 지킬 것)
- 커밋과 푸시는 **g_blinker**(와 지정된 세션 브랜치)에만 합니다. 다른 브랜치(g_web 등)는 사용자가 명시할 때만.
- PR은 요청이 있을 때만 만듭니다.
- 핫스팟 비밀번호는 채팅이나 코드에 쓰지 않습니다(펌웨어 설정 화면에서 사용자가 직접 입력).
- 네이버 APK는 저장소에 커밋하지 않습니다.
- 사용자 개인정보(이메일 등)는 외부로 보내지 않습니다.
- 새 기능은 가능하면 **토글(기본 꺼짐)** 로 넣고, 테스트를 추가합니다.
- 기존 테스트 실패(무관): `test_device_ui_suppression` 2건, controls 일부.

## 3. g_blinker에 들어간 주요 기능
- **NOO**:
  - 설정에 **NOO 탭**. MODE 1 = 차선변경만, 회전감속 없음.
  - 네이버 경로 조향 `NooRouteSteer`(0/1/2), 고속도로 출구 준비 `NooHighwayPrepare`.
  - 다음 교차로 차로 준비.
- **네이버 6.12.2.4 호환** (`selfdrive/eon_cluster/naver_xposed`):
  - 신호 거리, 클로바 음성 버튼(안심주행 포함).
  - 차로 띠를 직접 그림: `NaverLaneStrip`.
- **S20 HUD**(구 S9 HUD): 학습 상자, 깜빡이 상태, E2E 사유 배지.
- **리모컨**: NOO 켜기/끄기(`noo_toggle`) 포함 12개 동작.
- **ConditionalE2E**(`selfdrive/controls/lib/conditional_e2e.py`):
  - TrafficStopMode 0~3, C-ITS 신호 보조.
  - **FCW → E2E BRAKE ASSIST**(`FcwBlended`, 기본 꺼짐): MPC가 충돌을 예측하면 ACC→blended로 전환, 0.5초 유지, 정차 중 제외. 배지 "FCW". ACC 모드에서도 동작.
- **학습**(`selfdrive/locationd/autotuned.py`):
  - 기준을 채운 뒤에도 계속 학습합니다(보관량은 최근 기록으로 교체, 값은 조금씩 이동).
  - 롱컨 습관 학습은 **SCC 꺼짐 + 롱컨 꺼짐 + D단**일 때만 됩니다. 조향만 인게이지해도 학습됩니다.

## 4. 실차 확인이 남은 것
- 네이버 차로 띠 모양, nMirror 음성 버튼, 다음 교차로 차로 준비.
- 경로 조향(`NooRouteSteer=1`부터), 고속도로 출구 준비, 리모컨 NOO 토글.
- FCW 전환 체감.
- ESP32 직결의 장기 영향(방전, DTC).

## 5. OPTECH-KR/openpilot `optech-spst-dev` 비교 결과
- EON/C2용 legacypilot(dragonpilot 기반, 0.8.16)입니다.
- **주행 모델 비교**:
  - OPTECH 기본 `hybrid_modeld`의 supercombo는 g_blinker와 **같은 파일**입니다(sha256 50c7fc85…, 95,167,481 B).
  - "옛 주행 모델(0.8.13.1)" 토글(`dp_0813`)을 켜면 `legacy_modeld`(다른 모델) + 옛 플래너(E2E 없음)를 씁니다.
- 차이는 모델이 아니라 플래너와 E2E 전환 쪽입니다. DEC 중 FCW 전환만 g_blinker로 이식했습니다.

## 6. 새 주제: S20 FE 오파 구동 (사용자가 일반 채팅에서 정리한 내용)
865 계열(Adreno 650)이 실제로 돌려 본 사례가 가장 많고, 8 Gen 1 이상은 불가하다는 이야기가 있습니다.

| 기종 | AP | 비고 |
|---|---|---|
| 갤럭시 S20 FE 5G (G781N) | 865 | **보유 중**, LineageOS 지원(확인 필요) |
| 노트20 / 울트라 (국내판) | 865+ | 국내판도 스냅드래곤 |
| Z 폴드2 / Z 플립 5G | 865+ | 차량 거치 불리 |
| 탭 S7 / S7+ | 865+ | 카메라 아쉬움 |
| 원플러스 8 / 8 Pro / 8T | 865 | 언락 쉬움, LineageOS 공식, 실증 영상 있음(미검증) |
| 원플러스 9R, 포코 F3 | 870 | |
| 샤오미 Mi 10 / Pro, 포코 F2 Pro | 865 | |
| 엑스페리아 1 II / 5 II, ROG폰 3, Find X2 | 865/865+ | |

tinygrad 기준으로는 Adreno 6xx면 이론상 가능하지만, 실증은 865뿐입니다. 카메라 화각 보정은 기종마다 따로 해야 합니다.

**앞 세션에서 정리한 주의점:**
- g_blinker(EON, SNPE)는 폰에서 그대로 돌지 않습니다. 폰 오파는 보통 tinygrad 기반이라 뼈대가 달라서, 기능을 옮겨야 합니다.
  - 옮길 기능: NOO, 네이버 경로, ConditionalE2E/FCW, ESP32 깜빡이, 리모컨, S20 HUD, 학습.
- 매립된 S20은 핫스팟, 네이버, HUD 역할과 충돌하고 발열도 문제입니다. **별도 공기계 S20 FE**로 시험하는 것을 권했습니다.
- 판다 연결(USB-C OTG, 전원), DH 차종 지원, M-CAN HUD 연동을 다시 확인해야 합니다.

**다음 할 일:**
- 사용자에게 카페나 디스코드에서 쓰는 **폰용 오파 저장소 주소**를 받습니다.
- 그 저장소를 받아 세 가지를 확인합니다.
  1. DH 지원 여부
  2. EON 대비 달라지는 점
  3. g_blinker 기능을 옮길 수 있는 범위
- 결과를 정리해 보고합니다. 코드 변경은 사용자 확인 후에 합니다.
