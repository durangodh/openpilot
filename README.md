# 엠캔 게이트웨이 여분 판다 펌웨어

여분 블랙판다를 차량 엠캔과 순정 HUD 사이 게이트웨이로 쓰는 펌웨어입니다.
소스: `g_blinker` 브랜치의 `panda/board/safety/safety_mcan_gateway.h`, `panda/board/boards/black.h` (`MCAN_GATEWAY` 빌드).

## 파일

| 파일 | 내용 |
|---|---|
| `panda_mcan_gw_26pin.bin.signed` | 현재 버전 (커밋 4da04b2 소스). 하네스박스 **26핀만** 사용 |
| `wiring_26pin.png` | 배선도 |
| `panda_mcan_gw_8pin.bin.signed` | 이전 버전 (8핀 배선: HUD 쪽 8핀 8/7, 전원 8핀 1/4). 확인 코드 `1dec9f89b946912b` |

- 크기 35,340 바이트
- 확인 코드(서명 앞 8바이트): `687dc8f5d85f386a`
- SHA-256: `99cda97c6d4ac727e33f674e511378927d96d84353ba3e44269aac9c359a60df`

## 배선 (26핀)

| 26핀 | 연결 |
|---|---|
| 12 | 12V (운전석 풋램프 M63 1번 빨간선 → 1~2A 퓨즈) |
| 1 | GND (차체 접지) |
| 4 / 6 | CAN0 H / L → CANBridge-LS J5 H / L (차량 쪽) |
| 8 / 10 | CAN1 H / L → HUD 쪽 (18 / 20 도 같은 CAN1) |

이 펌웨어에서는 8핀 커넥터의 CAN3(8/7)로는 HUD 쪽이 동작하지 않습니다.

## 플래싱 (PC, 여분 판다만 USB 연결)

펌웨어 파일을 PC의 openpilot 폴더(panda 폴더가 있는 곳)에 넣고 그 폴더에서:

```
python -c "from panda import Panda; p=Panda(); p.flash('panda_mcan_gw_26pin.bin.signed')"
python -c "from panda import Panda; print(Panda().get_signature()[:8].hex())"
python -c "from panda import Panda; print(Panda().health()['safety_mode'])"
```

두 번째가 `687dc8f5d85f386a`, 세 번째가 `192`이면 정상입니다.
여분 판다를 EON에 꽂으면 일반 펌웨어로 자동으로 다시 써지니 EON에는 꽂지 마세요.

## 다시 빌드

```
(g_blinker 에서) cd panda/board && MCAN_GATEWAY=1 scons -f SConscript obj/panda_mcan_gw.bin.signed
```
