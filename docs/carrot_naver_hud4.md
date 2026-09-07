# CarrotNaver HUD4

Release: https://github.com/durangodh/openpilot/releases/tag/carrot-naver-6.9.1.3-hud4

CarrotNaver 6.9.1.3 HUD4 설치 파일입니다.

## 설치
기존 HUD3와 서명이 다릅니다. 기존 네이버/CarrotNaver 앱을 삭제한 후 이 APKS를 새로 설치하세요. 앱 삭제 시 앱 내부 설정과 데이터가 지워질 수 있습니다.
base, arm64-v8a, xxhdpi APK는 모두 동일한 새 서명키로 서명했습니다.

## 수정
- 네이버 지도 렌더링 표면을 직접 캡처해 Window 캡처에서 지도 배경이 빠지는 경로 수정
- 지도 진입 전 화면, 일반 영상/광고 뷰의 지도 전송 차단
- 지도가 없거나 표시 불가하면 HUD 지도 이미지 지우기
- 티맵과 동일한 map_main JPEG 전송 형식 및 HUD 안내/도착정보 합성 유지

## 검증
Java 회귀 테스트, Android API 35 컴파일, D8/Smali 빌드, APK 서명 및 정렬 검증 통과.
실제 S9 지도 표시 확인은 아직 수행하지 않았습니다.

- 파일: CarrotNaver_6.9.1.3_hud4.apks
- APKS SHA-256: c241efc70c41cf9120070eb8dc9aef983ddf3bc2c81cdfd196d6bab2b0641a9e
- 서명 인증서 SHA-256: b8b4093f1351346ae1642299d0f75fad33b8764c004058b85b744d514c23e6ad
- 네이버 앱 내부 버전은 6.9.1.3을 유지합니다. HUD4는 수정본 구분명입니다.
