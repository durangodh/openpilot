# 카메라 화각 보정

모델은 콤마 카메라의 내부 파라미터(intrinsics)를 전제로 학습됐다.
S20 FE 카메라는 초점거리·화각이 다르므로 영상을 콤마 카메라 기준으로 변환해야 한다.

## 측정할 값 (도로 / 광각 각각)
- 해상도, 초점거리 fx·fy(px), 주점 cx·cy
- 렌즈 왜곡 계수 (특히 초광각)

## 방법
1. Camera2 `LENS_INTRINSIC_CALIBRATION`, `LENS_DISTORTION` 값 읽기
2. 체커보드 촬영으로 검증 (OpenCV calibrateCamera)
3. 결과를 `s20_intrinsics.json`으로 저장 → s20camerad 또는 모델 변환 행렬에 반영
