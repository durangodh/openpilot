# s20pilot — Galaxy S20 FE(스냅드래곤 865) 포팅

콤마 공식 master(0.11.4, af90646)를 갤럭시 S20 FE 5G에서 돌리기 위한 브랜치.
구조: LineageOS(r8q) + Magisk 루팅 → 안드로이드 최소화 → 우분투 24.04 arm64 chroot 안에서 openpilot 실행.

## 기기 조건
- **SM-G781N / SM-G781B** (S20 FE 5G, 스냅드래곤 865, Adreno 650)
- SM-G780F(엑시노스)는 불가
- tinygrad QCOM 백엔드는 Adreno 6xx(7.3 미만)만 허용 → 650 통과

## 구조
```
[안드로이드 (LineageOS, root)]
  ├─ 카메라 브리지 앱 (Camera2: 도로 + 광각) ──┐ 공유메모리
  ├─ GPS / 센서 전달                            │
  └─ 화면 표시                                  ▼
[우분투 chroot]  /data/s20pilot/rootfs
  ├─ openpilot (master)
  ├─ modeld → tinygrad QCOM (/dev/kgsl-3d0, Mesa IR3)
  └─ pandad → libusb (/dev/bus/usb, OTG)
```

## 폴더
| 폴더 | 내용 |
|---|---|
| `chroot/` | chroot 설치·진입 스크립트 |
| `tests/` | 단계별 검증 스크립트 |
| `android/` | 카메라 브리지 앱 (Camera2 → VisionIPC) |
| `calib/` | 카메라 화각(intrinsics) 보정값 |

## 단계 (각 단계 통과 못 하면 멈춤)
| 단계 | 내용 | 통과 기준 | 스크립트 |
|---|---|---|---|
| 1 | LineageOS + Magisk | 루트 쉘 | — |
| 2 | 우분투 chroot | chroot 진입, apt 동작 | `chroot/setup_chroot.sh` |
| 3 | **GPU** | tinygrad `DEV=QCOM` 연산 성공, gpu_id 6.5.x | `tests/stage3_gpu.sh` |
| 4 | 판다 | USB 인식, CAN 수신 | `tests/stage4_panda.sh` |
| 5 | 빌드 | `scons` 완료, 모델 컴파일 | — |
| 6 | 카메라 브리지 | 도로·광각 20fps | `android/` |
| 7 | 화면·GPS·센서 | UI 표시, 위치 수신 | — |
| 8 | 주행 | — | — |

## 원칙
- master 코드 수정은 최소화하고 포팅 코드는 `s20/`에 모은다.
- master 코드를 고친 곳은 아래 목록에 기록한다 (업스트림 갱신 시 충돌 확인용).

### master 수정 목록
(아직 없음)

## 업스트림 갱신
이 브랜치는 콤마 master 스냅샷(이력 압축) 위에 있다. 갱신할 때:
1. 새 master를 받아 스냅샷 커밋을 새로 만든다.
2. `s20/` 커밋들을 그 위로 옮기고, 위 "master 수정 목록"의 파일만 충돌을 확인한다.
