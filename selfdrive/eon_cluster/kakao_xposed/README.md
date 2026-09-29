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
카카오 SDK 내장 **오프스크린 캡처러 KNMMapCapturer** 를 모듈이 직접 생성한다.
전용 스레드 2fps 로 KATEC 카메라 이동 → capture(720×432) → JPEG q72 →
7714 `/kakao/` 바이너리 전송. 화면/nMirror 가상화면에 의존하지 않는다.
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
