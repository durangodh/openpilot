# 카메라 브리지 (단계 6)

콤마 camerad는 콤마 전용 센서·ISP를 직접 제어하므로 S20 FE에서는 쓸 수 없다.
안드로이드 쪽에서 Camera2로 프레임을 받아 chroot로 넘긴다.

## 구성
- `src/s20cam/` — 안드로이드 쪽 (Java, `app_process` 로 shell 사용자(uid 2000)로 실행, 앱 설치 없음)
  - `list`: 카메라별 방향·초점거리·센서 크기·내부 파라미터(intrinsics)·왜곡·YUV 크기/최대 fps 출력
  - `stream <id>:<w>x<h>:<파일> [...] [fps=20]`: 카메라 1~2대를 NV12 프레임 링 파일로 기록
    (OIS·손떨림 보정·왜곡 보정 끔, 초점 무한대, AE fps 고정)
- `s20cam.jar` — 빌드 결과 (`build.sh`, JDK + android.jar(35) + d8 필요)
- `s20cam.sh` — 폰에서 루트로 실행하는 실행기 (`/data/s20pilot/share` 에 링 파일)
- chroot 쪽 읽기: `s20/camerad/ring.py` (링 형식은 `RingWriter.java` 주석 참고)
  - `python3 s20/camerad/ring.py /s20share/road.ring 5` → fps, 지연, 스냅샷(.ppm)

## 다음
- `s20camerad`: 링 → VisionIPC `VISION_STREAM_ROAD`/`WIDE_ROAD` + `roadCameraState`/`wideRoadCameraState`
- 카메라 내부 파라미터를 openpilot 카메라 설정에 추가, 해상도에 맞춘 워프 모델 컴파일
