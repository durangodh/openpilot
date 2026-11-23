# 카메라 브리지 (단계 6)

콤마 camerad는 콤마 전용 센서·ISP를 직접 제어하므로 S20 FE에서는 쓸 수 없다.
대신 안드로이드 쪽에서 Camera2로 프레임을 받아 chroot의 VisionIPC로 넘긴다.

## 계획
- 안드로이드: Camera2 NDK 서비스 (루트 실행)
  - 도로 카메라 = 메인(광각 26mm), 광각 카메라 = 초광각(13mm)
  - 출력 YUV_420_888 → NV12, 20fps, 노출·초점 고정
  - 프레임을 `/data/s20pilot/share`의 공유메모리 링버퍼에 기록
- chroot: `s20camerad` (camerad 대체)
  - 링버퍼에서 읽어 VisionIPC `VISION_STREAM_ROAD` / `VISION_STREAM_WIDE_ROAD`로 발행
  - `roadCameraState` / `wideRoadCameraState` 메시지 발행
- 해상도는 콤마 카메라(1928x1208) 기준으로 맞추고, 화각 차이는 `calib/`의 intrinsics로 보정

## 확인할 것
- 리니지 r8q 카메라 HAL에서 메인과 초광각 동시 스트리밍 가능 여부
- 프레임 타임스탬프(SOF)를 chroot 쪽 monotonic 시계와 맞추는 방법
