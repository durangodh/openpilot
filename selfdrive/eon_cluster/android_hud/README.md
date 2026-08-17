# Android remote HUD (experimental)

This optional companion moves the 1920x462 HUD render, JPEG compression and
TURZX `1cbe:0092` USB upload from the EON to an Android phone.  The EON sends a
small UDP JSON telemetry packet at 10 Hz. The already-compressed TMAP JPEG
received from the existing phone sender is forwarded unchanged over TCP; EON
does not decode, resize, composite, or re-encode it.

## EON

The manager starts `remote_hud` persistently, but it sleeps unless enabled:

```sh
python - <<'PY'
from common.params import Params
p = Params()
p.put_bool("EonClusterHud", True)
p.put("EonClusterHudOutputMode", "1")  # 0=EON direct, 1=S9 remote
PY
```

Keep both devices on the same hotspot/Wi-Fi network. UDP port 7210 and TCP port
7211 must be reachable. To return instantly to the direct EON HUD, keep
`EonClusterHud` true and set `EonClusterHudOutputMode` to `0`. The same choices
are exposed as **EON 직접** and **S9 원격** in the EON settings UI.

## Rooted Galaxy S9 TMAP sender

Build and install the `app` with Android Studio (minSdk 26; rooted Galaxy S9
running LineageOS 20 / Android 13 is the target). Root access is not
required, so it does not interfere with the existing Carrot/TMAP sender setup.
Connect the TURZX panel through a powered USB-C OTG adapter. Select **EON Remote
HUD** in E-Mirror's auto-launch list. The app starts its foreground service and
closes its activity immediately; it never requests screen-capture permission.
It receives the original TMAP JPEG back from EON, draws the driving and system
panels, encodes JPEG quality 55 at 8 FPS, and uploads it to the panel. Version
0.2 renders the perspective road, model path surface, lane lines, road edges,
two radar leads, a shaded 3D-style ego/lead vehicle, turn signals and BSD on the
Galaxy S9. The EON only publishes compact scene coordinates and does no HUD
rendering. Version 0.3 keeps the existing road-surface colors while bending the
road to the model edges, draws perspective-scaled dashed lane markings, rotates
PNG vehicle sprites with the path, and replaces the old BSD dot/triangle with a
rear-quarter vehicle sprite. JPEG writes are split into 16 KiB USB chunks for
older Galaxy S9 Android builds.

Version 0.4 resamples path, lane, and road-edge geometry at fixed forward
distances and applies five-frame-class EMA smoothing. Low-confidence geometry
is rejected while the last valid shape is held for 500 ms. Road edges are kept
ordered, total road width is clamped to 4.5-18.0 m, lane crossings are removed,
and lanes/path are constrained inside the stabilized road surface. These guards
prevent short model dropouts and sharp curves from lifting lane marks off the
road or collapsing the road polygon.

USB access may require one approval after initial installation. Selecting the
app as the default handler for `1cbe:0092` lets Android grant access and launch
it automatically on later USB attachments/reboots.

Vehicle control and CAN messages are never accepted from the phone.

## v0.19 (full 3D + USB freeze fix)

### 3D 주행씬 (`World3D.java`)

v0.18 까지의 `project()` 는 세로 `d/(13+d)`, 가로 `66/(1+d/17)` 이라는 서로
다른 근사식을 썼기 때문에 거리에 따른 도로 폭 / 차선 간격 / 차량 크기의 비율이
물리적으로 맞지 않았다. v0.19 는 단일 핀홀 카메라 하나만 쓴다.

```
depth = X + CAM_BACK                 (차 뒤 9.5 m)
u     = CX      - FOCAL * Y / depth  (f = 520 px)
v     = HORIZON + FOCAL * (CAM_H - Z) / depth   (노면 위 3.4 m, 지평선 y=249)
```

노면 · 차선 · 도로경계 · 건물 · 차량이 전부 같은 변환을 통과하므로 원근이
서로 어긋나지 않는다. 튜닝하려면 `World3D` 상단의 `FOCAL / CAM_H / CAM_BACK /
HORIZON` 네 개만 만지면 된다.

* 건물은 24 m 격자에 절대좌표로 고정돼 있고 누적 주행거리(`worldOdoM`)만큼
  뒤로 흘러간다. v0.18 은 거리 배열이 고정이라 달려도 제자리였다.
  앞면 + 안쪽 측면을 8꼭짓점 투영으로 그린다 (카메라가 지붕보다 낮으므로
  지붕면은 없는 게 맞다).
* 차선 두께는 도색폭 0.16 m 기준으로 거리에 따라 실제 비율대로 가늘어진다.
  주행차선(모델 index 1·2)은 노란색, 나머지는 흰색, 도로경계는 회색.
* 차량은 접지점 빌보드. 폭 1.9 m 가 `FOCAL/depth` 로 환산된다. 접지 그림자
  타원의 납작한 정도도 `CAM_H/depth` 로 계산한다.
* 패널이 237 px 로 납작해 높은 건물은 위가 잘리므로, 상단 46 px 헤이즈
  그라디언트로 배경에 녹아들게 처리했다.

### USB (`TurzxDisplay.java`)

* **매 프레임 `clearHalt()` 제거.** 주행 2~5 분 뒤 화면이 굳던 증상의 원인으로
  보인다. libusb 의 `clear_halt()` 는 커널 `usb_clear_halt()` 를 거쳐 호스트
  쪽 data toggle 까지 초기화하지만, 안드로이드 `UsbDeviceConnection` 에는 그
  ioctl 이 없어 우리가 보내는 `controlTransfer(0x02,0x01,...)` 는 장치 쪽
  toggle 만 DATA0 으로 만든다. 정상 상태에서 반복하면 양쪽 toggle 이 어긋나고,
  그 뒤로는 `bulkTransfer` 는 성공한 것처럼 보이는데 패널은 마지막 프레임을
  붙들고 있게 된다. 이제 `recoverAfterError()` — 즉 전송 실패 뒤에만 부른다.
* 패널 무응답 감시: `drainInput()` 이 읽은 패킷 수를 세어, 응답을 본 적 있는
  패널이 15 초 이상 조용하면 재초기화한다. 원래 조용한 패널에서는 감시가
  켜지지 않으므로 헛된 리셋이 없다.
* 프레임 중간에 쓰기가 실패하면 남은 길이를 0 으로 채워 패널의 수신 카운터를
  끝낸다. 안 그러면 패널이 프레임 대기 상태로 고착돼 재삽입 전까지 안 풀린다.

### 렌더 성능 (`HudService.java`)

* v0.18 은 프레임마다 1920x462 비트맵을 만들고 회전 복사본을 하나 더 만들었다
  (약 3.5 MB/프레임, 8 fps 면 28 MB/s). 이제 462x1920 세로 버퍼 하나를 잡아
  두고 캔버스 행렬로 회전/미러한다.
* `Paint` / `Path` / `RectF` / `ByteArrayOutputStream` 을 필드로 돌려 쓴다.
* 모델 경로를 프레임당 한 번만 배열로 풀고 선형보간한다. v0.18 의
  `pathCenterAt()` 은 호출마다 JSON 배열 전체를 훑는 최근접 탐색이었고
  프레임당 100 회 가까이 불렸다.
* EON UDP 가 3 초 이상 끊기면 마지막 상태로 얼지 않고 "EON 연결 끊김" 을
  표시한다. v0.18 은 속도 0 인 채 굳어 패널이 멈춘 것처럼 보였다.

### 건물 끄기

건물은 실제 주변 지형이 아니라 24 m 격자에 해시로 찍어내는 장식이다. 실제
데이터(도로 커브 · 차선 · 도로경계 · 앞차)와 헷갈리면 EON 에서 끌 수 있다.

```sh
python - <<'PY'
from common.params import Params
Params().put("EonClusterHudBuildings", "0")   # 0=끔, 1=켬(기본)
PY
```

패킷 키는 `hudBuildings` 이다. `remote_hud.py` 의 패킷 조립부에 아래 한 줄을
추가하면 위 Params 가 반영된다.

```python
"hudBuildings": int(self.params.get("EonClusterHudBuildings", encoding="utf8") or 1),
```

끄면 하늘 · 지면 · 노면 · 차선 · 차량만 남아 실제 인식 데이터만 보인다.

## v0.19.2 (5:4:1 레이아웃)

패널 폭 비율을 4:2:4 에서 **5:4:1** 로, 순서를 주행 → TMAP → SYSTEM 으로 바꿨다.

| 패널 | v0.19.1 | v0.19.2 |
|---|---|---|
| 주행 | 0~765 | **0~952** |
| SYSTEM | 776~1144 | 1728~1920 |
| TMAP | 1152~1920 | 960~1720 |

* 주행 패널의 요소 **크기는 그대로 두고 위치만** 넓어진 폭에 맞춰 벌렸다.
  속도 68pt, 핸들·SET·카메라 반지름 36, 카드 148x78 전부 v0.19.1 과 동일하다.
  3D 씬만 폭 952 로 넓어져 도로 양옆이 더 보인다.
* SYSTEM 은 폭이 192px 뿐이라 라벨을 위, 값을 아래로 쌓았다.
  코어별 사용률 줄은 자리가 없어 뺐고 ACCEL 은 두 줄로 남겼다.
* ATC 안내는 주행 패널에서 **TMAP 좌하단(962~1106 / 330~456)** 으로 옮겼다.
  크기는 이전과 같고, `remainDist > 0` 조건을 추가해 **경로가 살아 있을 때만**
  나온다. 목적지 없이 떠 있지 않는다.
* TBT 1행은 티맵 PNG 를 그대로 쓰되 폭을 480 -> 400 으로 줄였다.
  고가차도·복잡분기 아이콘이 살아 있어야 하므로 직접 그리지 않는다.
* TBT 2행(다음 회전)은 1행과 같은 녹색으로 앱이 직접 그린다 (폭 232).
  패킷의 `navi.next` 가 있어야 표시되며, EON 쪽 `apply_eon_v0192.py` 로 추가한다.
* 프레임 0 = 일시정지가 아니라 **패널 끄기**. 검은 프레임 한 장을 보내고
  밝기를 1 로 내린다. v0.19.1 까지는 전송만 멈춰 마지막 화면이 남았다.
* 건물 on/off 는 패킷 `hudBuildings` 로 제어된다 (기본 1). EON 쪽 키 등록과
  전달은 `apply_eon_v0192.py` 가 처리한다.

## v0.19.3 (TBT 확대 · 출력 모드)

* TBT 1행을 좌상단 기준으로 그리고 가로를 2배(400 -> 700)로 키웠다.
  `drawTbtImage()` 가 실제로 그린 높이를 돌려주므로 2행이 1행 **바로 아래에
  붙는다**. 이전에는 박스 안 세로 가운데 정렬이라 두 줄이 어긋나 보였다.
* 2행도 좌측을 1행과 같은 968 로 맞췄다.
* 새 파라미터 `EonClusterHudOutputMode` (패킷 `hudOutputMode`)
  * **1** — 주행 / 지도 / 시스템 (기본)
  * **2** — 시스템 자리에 실시간 디버그 (CPU·TEMP·SPEED·SET·GAP·LEAD·REL·FPS·JPEG)
* `EonClusterHudBuildings` 를 설정 UI 에 노출했다 (0 끔 / 1 켬).

## v0.19.4 (TBT 배너를 폰 화면과 같은 형태로)

티맵이 보내주는 `tbt_current_full.png` 는 "157m 교차로" 가 **한 줄로 붙은
가로형** 이라, 폰 화면처럼 거리 아래에 도로명을 넣을 수 없다. 이미지를 아무리
키우거나 줄여도 안쪽 배치는 못 바꾼다.

그래서 1행을 직접 그린다 (342x126). 패킷에 `turnType` / `turnDist` / `title`
이 이미 들어 있다.

```
 ┌──────────────────────────┐
 │  ↱   83 m                │   거리 46pt
 │      교차로              │   도로명 26pt
 └──────────────────────────┘
```

되돌리기용으로 PNG 를 그리는 `drawTbtImage()` 는 남겨 뒀다.
호출부 한 줄만 바꾸면 이전 방식으로 돌아간다.

**트레이드오프** — 직접 그리면 티맵의 정교한 회전 아이콘(고가차도, 복잡분기)
대신 앱의 단순 화살표가 나온다. 좌/우/유턴/직진은 문제없다.
