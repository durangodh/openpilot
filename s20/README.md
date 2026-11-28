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
| 1 | LineageOS + Magisk | 루트 쉘 | `root/README.md` — **통과 2026-11-28** (23.2-20261114-NIGHTLY-r8q, kgsl-3d0 확인) |
| 2 | 우분투 chroot | chroot 진입, apt 동작 | `chroot/setup_chroot.sh` — **통과 2026-11-28** |
| 3 | **GPU** | **통과 2026-11-28** (gpu_id 6.5.0, IR3, matmul 512² 4.58 ms) — tinygrad `DEV=QCOM:IR3` 연산 성공, gpu_id 6.5.x (QCOMCL 컴파일러는 a630 전용이라 Mesa ir3 사용) | `tests/stage3_gpu.sh` |
| 4 | 판다 | USB 인식, CAN 수신 | `tests/stage4_panda.sh` |
| 5 | 빌드 | `scons` 완료, 모델 컴파일 — `build.sh` **통과 2026-11-28** | `tests/stage5_model.sh` — 2026-11-28 주행 모델 컴파일·실행 통과: FLOAT16+NOLOCALS+JIT, **IMAGE=0 주행 54~56 ms/회, 운전자 모니터링 42~44 ms/회** (IMAGE=1 도 돌지만 커널 다수가 512 B spill + 48 레지스터 → NOOPT, ~100 ms. 기본값 IMAGE=0) |
| 6 | 카메라 브리지 | 도로·광각 20fps | `android/`, `camerad/` — 2026-11-28 도로 카메라 30fps 수신(지연 ~3 ms), 20Hz로 골라 VisionIPC 전송. 광각은 동시 열기 불가 |
| 7 | 화면·GPS·센서 | UI 표시, 위치 수신 | — |
| 8 | 주행 | — | — |

## 원칙
- master 코드 수정은 최소화하고 포팅 코드는 `s20/`에 모은다.
- master 코드를 고친 곳은 아래 목록에 기록한다 (업스트림 갱신 시 충돌 확인용).

### master 수정 목록
- `openpilot/system/manager/process_config.py`: 환경변수 `S20` 이 있으면 camerad 대신 `s20.camerad.s20camerad` 실행
- `openpilot/selfdrive/modeld/SConscript`: `S20` 이면 모델 컴파일 플래그 `DEV=QCOM:IR3 IMAGE=0 FLOAT16=1 NOLOCALS=1 ...`
- `tinygrad_repo` (서브모듈): `patches/tinygrad_a650.patch` — `s20/apply_patches.sh` 로 적용
  - `compiler_mesa.py`: IR3 컴파일러 a630 전용 제한에 a650 추가 (Mesa 장치 정보는 gpu_id 650 으로 조회)
  - `ops_qcom.py`: 개인 메모리(스택) 크기를 SP당 fiber 수(a630 2048, a650 4096)에 맞춤
  - `ops_qcom.py`: MERGEDREGS·PERWAVEMEMLAYOUT·wave 크기(THREAD128)를 컴파일 결과대로, a650 레지스터 64 vec4,
    셰이더 미리 올리기 127 단위, 스택 오프셋 2 KiB 단위 (S20_* 환경변수로 실험 가능)
  - `codegen/__init__.py`, `opt/heuristic.py`: a650 IR3 셰이더가 레지스터를 개인 메모리로 넘치면(spill)
    upcast 한도를 16→8→4→2→1, 마지막엔 NOOPT 로 다시 컴파일. 개인 메모리 쓰는 커널에서 a650 이 멈추기 때문
    (2026-11-28: 주행 모델 16번째 커널 r_16_32_3_3_25_8, pvt 1 KiB 에서 GPU hang, 레지스터 설정 8조합 모두 실패)

## 업스트림 갱신
이 브랜치는 콤마 master 스냅샷(이력 압축) 위에 있다. 갱신할 때:
1. 새 master를 받아 스냅샷 커밋을 새로 만든다.
2. `s20/` 커밋들을 그 위로 옮기고, 위 "master 수정 목록"의 파일만 충돌을 확인한다.
