# Kakao HUD Xposed 모듈

카카오내비(com.locnall.KimGiSa)를 티맵/네이버처럼 EON 외부 HUD 와 연동한다.

## 왜 Xposed 인가
카카오내비는 Stealien AppSuit 무결성 검사가 있어 **재서명(smali 편집)이 막힌다**.
그래서 티맵/네이버가 재서명으로 붙였던 지점(SDK→앱 안내 콜백)을 **LSPosed 로
후킹**해서 재서명 없이 같은 데이터를 얻는다. 카카오내비는 플레이스토어 원본을
그대로 쓰므로 앱 자동 업데이트가 유지된다.

## 후킹 지점
`KNUSDKRepository` 내부 리스너 구현체(인자 타입으로 탐색):
- 위치안내: 인자 `KNGuide_Location` → 위치(KATEC)/방위/도로명
- 경로안내: 인자 `KNGuide_Route` → 회전코드(KNRGCode)/거리
- 안전: 인자 `List` → 카메라(2차 로그로 필드 확정 예정)

SDK 안내 객체의 getter 이름은 유지된다(getLocation/getCurDirection/getRgCode…).

## 좌표계
SDK 위치는 전부 **KATEC**(예: 309840, 552483). 지도 캡처엔 KATEC 를 그대로 쓰고,
EON vehicle 스트림엔 `KNMCoordinateSystem.INSTANCE.katecToWGS84(x,y)` 로 변환한
WGS84 lat/lon 을 싣는다.

## 지도
### 지도 엔진 직접 렌더(KNMMapSurface, 기본)
티맵(TmapMapRender)·네이버(NaverMapRender)와 같은 방식이다. 안드로이드 오토 지도
(`NPMapSurfaceV2`)가 쓰는 `KNMMapSurface(Surface, Context, KNMScene)` 를 720×432
`ImageReader` 표면에 하나 더 만들고 `init(KNMPoint, KNMTheme?, Float?, Function1)` 으로
초기화한다. 엔진이 자기 렌더 스레드에서 계속 그리고, 모듈은 메인 스레드 100ms 마다
카메라(`moveCamera(update, false)`)·경로(`setRoutes`)·테마(`setTheme`)만 넣는다. 들어온
프레임은 최대 5fps 로 JPEG 전송하고, 지도가 멈춰 새 프레임이 없으면 마지막 프레임을
다시 보낸다. 카메라·경로·테마·차량 화살표 계산은 아래 캡처러 방식과 같다.
4.51.1 디컴파일로 이름을 확인했다(실기 미검증). 생성 실패, 8초 안에 init 콜백이 없거나
첫 프레임이 없으면 아래 캡처러로 자동 전환한다. 단계는 LSPosed 로그에도
`KakaoHud: map render ...` 로 남는다.

### 캡처러(KNMMapCapturer, 예비)
카카오 SDK 내장 **오프스크린 캡처러 KNMMapCapturer** 를 모듈이 직접 생성한다.
카카오 4.51.0의 주행 화면에 연결된 `KNUCameraPositionState`에서 실제 카메라의
위치·확대·방향·기울기를 읽어 캡처러에 우선 적용한다. 화면 카메라가 없거나 차량
위치에서 멀리 떨어진 다른 지도를 보고 있으면 기존 위치·속도 기반 추정값으로
돌아간다. 전용 스레드 최대 5fps 로 capture(720×432) → JPEG q65 →
7714 `/kakao/` 바이너리 전송. 화면/nMirror 가상화면에 의존하지 않는다.
지도 전송은 최신 프레임 하나만 유지하는 별도 작업자로 처리해, 네트워크 지연 시
오래된 프레임이 쌓여 뒤늦게 표시되지 않도록 한다.
KNMSDK 초기화 전에는 capture 가 null 을 주므로 조용히 재시도한다(로그로 상태 남김).

## 전송
carrot_navi_server(7714)로 티맵과 같은 JSON 스트림 + 바이너리 지도를 보낸다.
서버는 경로 `/kakao/` 로 소스를 구분(EON 설정 EonClusterHudNavApp=3).

## 미검증(실기 로그로 확인)
- 지도: 앱 밖 KNMMapCapturer 가 실제 GL 프레임을 내주는지 (로그 `first map frame sent`)
- 좌표: katecToWGS84 결과가 실제 위경도인지 (로그 `LOC shape … wgs=…`)
- 회전: KNRGCode getValue 로 raw 값이 잡히는지 (로그 `ROUTE shape … curRgRaw=…`)
- 안전: 카메라 객체 필드 (로그 `SAFETY shape …`) → 확인 후 SDI 변환 완성

## 설치
1. LSPosed 에서 모듈 켜고 스코프를 카카오내비로(scope.xml 기본 지정).
2. 카카오내비(플레이스토어 4.51.0) 강제 종료 후 재실행.
3. 안내 시작 후 로그 확인/공유:
   `/sdcard/Android/data/com.locnall.KimGiSa/files/kakao_hud.log`

## 교차로 확대 이미지(crossroad_expanded)
폰 주행 화면의 JC 팝업과 같은 그림이다(4.51.1 디컴파일, 실기 미검증).
`KNUJCViewModel` 생성자를 후킹해 잡아 두고, 250ms 마다 `getJcUIState().getValue()` 가
`KNUJCUIState.Data`(`navi.drive.core.feature.jc.c$a`)이면 그 안의 `KNUJC`(`knmsdk.hi0.a`,
필드 `a`)의 `a()` Bitmap 을 JPEG CNV2 프레임으로
`/api/navi/ws/v2/image/kakao/crossroad_expanded` 에 보낸다. 바뀔 때 보내고 떠 있는 동안
5초마다 다시 보내며, 닫히면 clear 를 보낸다. EON `carrot_navi_server.py` 는 오버레이
소켓 이름을 소스와 관계없이 먼저 확인한다.

## 차로 안내(lane_current)
폰 주행 화면의 차로 표시와 같은 값이다(4.51.1 디컴파일, 실기 미검증).
`KNULaneViewModel` 생성자를 후킹해 잡아 두고, 250ms 마다 `getLaneUIState().getValue()` 가
`Data`(`navi.drive.core.feature.lane.e$a`)이면 `KNULane`(`knmsdk.ji0.a`: 필드 a=차로 목록,
b=남은 거리)과 각 `KNULaneInfo`(`knmsdk.ji0.m`: 필드 a=`KNULaneTurnType`, f=추천)로
티맵·네이버와 같은 형식(count/current_lane/distance_m/lanes/turn_info/available)을 만든다.
회전 종류는 이름(STRAIGHT/TURNLEFT/BEARRIGHT/UTURN 조합)을 네이버와 같은 HUD 코드로 바꾼다.
바뀔 때와 1초마다 보내고, 사라지면 null 을 보낸다.
