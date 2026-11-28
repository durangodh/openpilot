# 카메라 브리지 (단계 6)

콤마 camerad는 콤마 전용 센서·ISP를 직접 제어하므로 S20 FE에서는 쓸 수 없다.
안드로이드 쪽에서 Camera2로 프레임을 받아 chroot로 넘긴다.

## 구성
- `src/s20cam/` — 안드로이드 쪽 (Java, `app_process` 로 shell 사용자(uid 2000)로 실행, 앱 설치 없음)
  - `list`: 카메라별 방향·초점거리·센서 크기·내부 파라미터(intrinsics)·왜곡·YUV 크기/최대 fps 출력
  - `auto [road=0] [wide=2|none] [flip=1] [dry]`: 도로(0)·광각(2) 카메라를 콤마3 카메라처럼 만들어 링 파일로 기록
    (OIS·손떨림 보정·왜곡 보정 끔, 초점 무한대, AE fps 고정)
    - 도로: 센서 가운데 2234x1257 을 잘라 1920x1080 → 초점거리 2648 px (콤마 AR0231/OX03C10 도로와 같음)
    - 광각: 센서 전체 4000x3000 → 1440x1080, 초점거리 569.5 px (콤마 광각 567 px)
    - 둘 다 1928x1208 프레임 가운데에 놓고 테두리는 검정 → openpilot 카메라 설정·워프 모델 그대로 사용
## S20 FE 카메라 (2026-11-28 `list`)
| id | 방향 | 렌즈 | 센서 | intrinsics fx,fy,cx,cy | 비고 |
|---|---|---|---|---|---|
| 0 | 뒤 | 5.4 mm | 4032x3024, 7.26x5.44 mm | 3080.9, 3078.5, 2004.0, 1511.3 | 메인, AF, OIS, 목록에 있음 |
| 2 | 뒤 | 1.74 mm | 4000x3000, 4.48x3.36 mm | 1582.1, 1580.5, 2010.5, 1499.2 | 초광각, 고정초점, **목록에 없고 id로 열림** |
| 1,3,4 | 앞 | 3.72 mm | 3264x2448 / 2640x1980 | 4650 (추정값) | 전면 |
- 타임스탬프 소스 1 (BOOTTIME), 동시 사용 카메라 조합은 광고하지 않음(직접 시험 필요)
- `s20cam.jar` — 빌드 결과 (`build.sh`, JDK + android.jar(35) + d8 필요)
- `s20cam.sh` — 폰에서 루트로 실행하는 실행기 (`/data/s20pilot/share` 에 링 파일)
- chroot 쪽 읽기: `s20/camerad/ring.py` (링 형식은 `RingWriter.java` 주석 참고)
  - `python3 s20/camerad/ring.py /s20share/road.ring 5` → fps, 지연, 스냅샷(.ppm)

## 다음
- `s20camerad`: 링 → VisionIPC `VISION_STREAM_ROAD`/`WIDE_ROAD` + `roadCameraState`/`wideRoadCameraState`
- 카메라 내부 파라미터를 openpilot 카메라 설정에 추가, 해상도에 맞춘 워프 모델 컴파일
