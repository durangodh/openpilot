# CarrotNaver HUD6

Release: https://github.com/durangodh/openpilot/releases/tag/carrot-naver-6.9.1.3-hud6

CarrotNaver HUD6 — 가로 지도 전체 표시 및 브리지 실시간 설정

## 한 번 필요한 업데이트
1. EON을 g_hud 최신 버전으로 업데이트합니다.
2. EON Remote HUD 1.17 APK를 업데이트합니다.
3. CarrotNaver_6.9.1.3_hud6.apks를 HUD4/HUD5 위에 덮어쓰기 설치합니다. 네이버 앱 삭제는 필요하지 않습니다.

## 기본 표시
- 네이버 Activity를 가로 방향으로 전환해 넓은 지도 영역을 렌더링합니다.
- 세로 지도 일부를 잘라 확대하던 방식 대신 지도 전체를 비율에 맞춰 표시합니다.
- 지도와 패널 비율이 다르면 여백이 생길 수 있습니다.
- 티맵 지도 디자인/자체 줌을 복제하는 것은 아니며, 네이버의 실제 가로 지도를 사용합니다.

## EON S9HUD 설정 → 네이버 브리지
- NAVER LANDSCAPE: 1 권장 (가로 지도). 0은 원래 앱 방향.
- NAVER MAP FIT: 1 권장 (전체 맞춤). 0은 잘라 채움.
- NAVER MAP SIZE: 100 기본. 50~100% 표시 크기, 줄이면 여백 증가. 지도 자체 줌은 변경하지 않습니다.
- NAVER MAP QUALITY: 90 기본, 60~95 JPEG 화질.
설정은 EON 파라미터 캐시 및 Remote HUD 전달 주기를 거쳐 약 1~2초 후 적용됩니다.
이 설정을 조정할 때는 네이버 APK를 다시 수정하거나 설치할 필요가 없습니다.
네이버 앱 내부 구조 변경이나 새로운 기능 추가는 별도 브리지 업데이트가 필요할 수 있습니다.

## 검증
실제 로컬 UDP 설정 전달, 값 검증, 티맵 격리, 전체 맞춤/자르기 비율, 캡처 회귀 테스트 통과.
Android API 35 컴파일, D8/Smali 빌드, 원본 APK 다른 항목 보존, 서명/정렬 검증 통과.
실제 S9의 가로 지도 구성 및 표시 범위는 설치 후 확인이 필요합니다.

- APKS SHA-256: 52e04217403f941b281aae71f3d8f0ccbd26accb6f1f3308cba196b3b764c3c0
- 네이버 인증서 SHA-256: b8b4093f1351346ae1642299d0f75fad33b8764c004058b85b744d514c23e6ad
- 네이버 내부 버전 6.9.1.3 유지, HUD6는 수정본 구분명입니다.
