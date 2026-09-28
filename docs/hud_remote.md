# S9 key remote → EON

Implemented on `g_remote` for the rooted S9 / Lineage GSI Android 16 setup.
Reference: [carrot-wip 962d4484](https://github.com/ajouatom/openpilot/tree/962d4484e737bcc98fcbffdfdfd1697600685962/openpilot/selfdrive/carrot/bluetooth),
particularly `model.py` and `daemon.py`. This is an adaptation, not a complete
BlueZ or Yiser J6 port. EON continues its existing vehicle control logic.

## 설치 및 설정

1. EON을 이 커밋으로 업데이트하고 S9 Remote HUD 1.38 이상을 설치합니다.
2. 시동을 끈 상태에서 EON 설정의 **REMOTE PAIR**을 눌러 연결 키를 확인합니다.
3. S9 Bluetooth 설정에서 리모컨을 연결합니다. HUD 앱 → **무선 리모컨 설정**에서
   접근성 **EON 리모컨 입력**을 켜고 연결된 입력 장치를 선택합니다.
4. EON에 표시된 32자리 연결 키를 입력·저장합니다. 연결 키는 개인별로 생성되며
   텔레메트리에 포함되거나 로그로 출력되지 않습니다.
5. 리모컨 버튼을 누르면 해당 키 코드가 표시됩니다. 짧게/길게/두 번 누르기 동작을
   각각 지정한 후 **리모컨 조작 사용**을 켜고 **설정 완료**를 누릅니다.
   설정 화면과 그 화면에서 연 Bluetooth/접근성 설정에서는 차량 명령이 차단됩니다.
6. 사용을 중지하려면 S9에서 사용 스위치를 끕니다. EON **REMOTE UNPAIR**는
   연결 키를 삭제합니다. 다시 사용할 때 새 키를 등록해야 합니다.

Android가 접근성 설정을 차단하면 앱 정보 → ⋮ → 제한된 설정 허용 후 다시 시도합니다.
입력 확인이 안 되면 다른 접근성 서비스의 키 필터 충돌 또는 제품 입력 방식을 확인합니다.
특히 nMirror가 먼저 키를 처리하는지는 실제 S9에서 확인해야 합니다. 기존 nMirror 설정을
자동으로 변경하거나 해제하지 않습니다.

| 선택할 동작 | EON 동작 |
| --- | --- |
| RES / 속도 +1 | 기존 RES 짧게 누르기와 같은 이벤트; 상태에 따라 ACC 재개 |
| SET / 속도 −1 | 기존 SET 짧게 누르기와 같은 이벤트; 상태에 따라 ACC 재개 |
| 차간거리 | 기존 GAP 짧게 누르기 |
| ACC 취소 | 기존 CANCEL; 이 브랜치에서는 조향은 유지 |
| 티맵 ↔ 네이버 | 기존 내비 선택 파라미터 전환 |
| 좌/우 차선변경 요청 | 기존 가상 깜빡이 차선변경 경로; 실제 방향지시등은 켜지지 않음 |

기본값은 사용 안 함이며, 버튼도 자동 배정하지 않습니다. 연결할 기기를 바꾸면
기존 키 배정을 지우고 다시 학습합니다. 저장한 기기가 일시적으로 연결되지 않아도
장치 선택과 버튼 설정은 유지됩니다. 짧게 누르기는 손을 뗄 때 실행합니다.
길게는 0.7초, 두 번 누르기는 0.35초 기준입니다. 두 번 누르기를 지정한 키만
짧게 누르기가 최대 0.35초 지연됩니다. 긴 속도 버튼은 0.5초마다 **1단계** 반복합니다.
기존 차량 핸들 버튼의 ±10 동작은 변경하지 않았습니다. 차선변경 긴 버튼은 누르는 동안
0.15초마다 요청을 갱신하고, 수신 요청은 0.3초 뒤 만료됩니다. 최대 유지 시간은 10초입니다.

## 지원 범위

- S9가 외부 입력 장치로 식별하고 Android KeyEvent를 전달하는 Bluetooth/USB 리모컨.
- 선택한 장치의 지정된 키만 소비합니다. S9 본체 버튼이나 다른 장치 키는 소비하지 않습니다.
- 화면 내용을 읽지 않으며 음악 MediaSession을 생성하지 않습니다. 따라서 음악 세션을
  가장해 벅스 제어권을 가져오지 않습니다. 같은 리모컨 키에 차량 명령을 배정하면
  그 키는 음악 앱으로 전달되지 않습니다.
- KEY1/KEY2 배선 수신기형, AVRCP만으로 키 이벤트가 노출되지 않는 제품,
  Yiser J6의 터치/스와이프 전용 입력은 이 Android 구현에서 지원하지 않습니다.
- carrot의 LFA, paddleDecel, carrotCruise, Long ±10 전용 명령, BlueZ 페어링 웹 UI,
  USB 방향지시등 릴레이는 이식하지 않았습니다.
- 제품 사진이나 판매자의 “Android 지원”만으로 실제 호환을 보장하지 않습니다.

## Implementation and validation

`RemoteKeyService` filters only the selected external device using Android's
key-filter accessibility capability; it never requests window content. Configuration
and disconnects cancel held gestures. `RemoteKeyEngine` implements gesture timing
and bounded repeats without catching up after scheduler stalls.

`HUDCMD2 <session> <request> <ticket> <command> <HMAC-SHA256>` travels over the
existing HUD reply socket. Both sides must have the same private pairing key.
The server issues a random ticket with a 400 ms monotonic lifetime; no clock
synchronization with S9 is needed. The sender keeps one request for at most
400 ms, retries with the same ID, and drops pending requests on session changes.
A duplicate request is acknowledged at most once for execution; changing its
command is rejected. The old unauthenticated HUDCMD1 is rejected.

A bounded command journal prevents fast distinct commands overwriting each other.
Consumers reject old, future-dated and pre-startup entries. Vehicle commands need
fresh valid CAN state and an onroad device; RES/SET/GAP/lane also require drive
gear and no brake/gas. The Hyundai consumer rechecks pedals, gear and physical
button events before emitting a speed/gap press. CANCEL retains its existing
branch meaning. Existing lane-change speed, road-edge, BSD and torque checks remain.

Checks:

```sh
python3 selfdrive/eon_cluster/test_hud_remote.py
# From android_hud:
javac -d /tmp/hud-remote-check app/src/main/java/ai/comma/remotehud/RemoteKeyEngine.java app/src/main/java/ai/comma/remotehud/RemoteCommandProtocol.java tests/RemoteInputCheck.java
java -cp /tmp/hud-remote-check ai.comma.remotehud.RemoteInputCheck
```

The HUD Actions workflow runs these checks, existing HUD/map regression checks,
APK compilation and the existing signing-certificate check before updating the
`remote-hud-auto` asset. Host checks do not establish Bluetooth delivery with
nMirror, EON UI compilation, installed-device behavior or vehicle validation.
