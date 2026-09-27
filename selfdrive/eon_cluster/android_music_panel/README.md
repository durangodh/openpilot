# Navi Music — nMirror용 음악 패널

Lineage GSI **Android 16**에서 지도 옆에 현재 음악 정보를 표시하는 별도 Android 앱입니다. 패키지는 `ai.comma.nmirrorpanel`이며 기존 HUD 앱과 별도로 설치됩니다. 최소 Android 12L(API 32), 대상 API 36입니다.

## 사용 순서

1. nMirror를 실행하는 **같은 Android 기기**에 APK를 설치합니다.
2. Navi Music → **설정 → 음악 연결 권한**에서 알림 접근을 허용합니다. 설치 출처에 따른 Android의 제한된 설정 안내가 나오면 앱 정보의 제한된 설정 허용 후 다시 시도합니다.
3. 같은 기기의 음악 앱에서 노래를 재생합니다. 앱이 Android 미디어 세션을 제공해야 합니다.
4. Navi Music에서 **지도와 함께 열기**를 누릅니다. 기본 지도는 네이버 지도이며 설정에서 TMAP을 고를 수 있습니다.
5. nMirror로 기본 화면을 미러링합니다. 시작 앱을 지정할 수 있다면 Navi Music을 선택합니다. 확인 후 **시작할 때 지도 함께 열기**를 켜면 편리합니다.
6. 지도와 패널 순서, 비율은 Android 분할 화면의 구분선/최근 앱 화면에서 조절합니다. 자동 분할이 되지 않으면 최근 앱에서 Navi Music과 지도를 수동 분할합니다.

초기 설정은 주차 상태에서 진행하세요.

## 표시와 제어

- 곡명, 아티스트, 앨범 이미지, 재생 위치와 길이
- 소스 앱이 지원하는 이전 곡·재생/일시정지·다음 곡
- 기본값은 재생 중인 미디어 세션 우선. 설정에서 특정 음악 앱을 우선할 수 있습니다.
- 네이버 지도와 TMAP의 안내 세션은 음악 후보에서 제외합니다.
- 앱이 화면에 보일 때만 세션과 진행 상태를 관찰합니다. 분할 화면에서도 계속 갱신됩니다.
- 앨범 이미지를 제공하지 않는 앱은 기본 배경으로 표시됩니다. URL 이미지를 외부에서 다운로드하지 않습니다.

## 범위와 제한

이 앱은 Android의 표준 분할 화면과 `MediaSessionManager`를 사용합니다. nMirror 내부 코드를 수정하거나 독자 통신 API를 사용하지 않습니다. nMirror가 기본 디스플레이 전체를 미러링하는 구성을 전제로 하며, 실제 S9의 GSI·차량 조합에서는 확인이 필요합니다. 단일 앱 화면만 캡처하는 모드에서는 양쪽 화면이 함께 나오지 않을 수 있습니다.

OS가 분할 지원, 좌우 위치, 실행 앱 크기를 결정합니다. 앱에서 지도 화면을 내부에 넣거나 강제로 오른쪽 위치를 보장하지 않습니다. 지도 앱도 분할 화면을 지원해야 합니다. 다른 휴대폰의 Bluetooth 음악 정보는 이 앱의 미디어 세션에서 직접 읽을 수 없습니다.

차량 음성 버튼 → 네이버 음성 검색은 nMirror와 지도 앱의 별도 연동입니다. 이 앱은 음성 버튼을 가로채거나 음성 인식 화면을 자동 클릭하지 않습니다.

## 개인정보와 권한

알림 접근 권한은 Android가 미디어 세션 조회에 요구하는 권한입니다. 서비스는 알림 본문을 읽거나 저장하는 콜백을 구현하지 않습니다. 네트워크 권한, 분석 SDK, 서버, 루트, 접근성 자동 조작은 사용하지 않습니다. 지도/음악 선택과 자동 실행 여부만 기기 내부에 저장합니다. 권한은 Android 설정에서 언제든 해제할 수 있습니다.

## 빌드와 검증

JDK 17, Gradle 8.11.1, Android SDK 36, AGP 8.10.1:

```sh
cd selfdrive/eon_cluster/android_music_panel
gradle testDebugUnitTest assembleDebug
# API 36 에뮬레이터 연결 후:
gradle connectedDebugAndroidTest
```

GitHub Actions `Navi Music Android 16`이 단위 테스트, API 36의 실제 미디어 세션 수신·일시정지 전달·분할 화면 진입·세션 종료 갱신을 검사하고, 기존 저장소의 서명 시크릿으로 별도 APK를 빌드합니다. 테스트용 곡/아트는 androidTest에만 포함되며 배포 앱에는 포함되지 않습니다. Actions의 테스트 성공이 S9·nMirror 실기기 호환성 검증을 뜻하지는 않습니다.

참고: [Android 멀티윈도](https://developer.android.com/develop/ui/views/layout/support-multi-window-mode), [MediaSessionManager](https://developer.android.com/reference/android/media/session/MediaSessionManager), [Android 16 동작 변경](https://developer.android.com/about/versions/16/behavior-changes-16).
