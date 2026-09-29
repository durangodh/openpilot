# Kakao HUD Xposed 모듈

카카오내비(com.locnall.KimGiSa)를 티맵/네이버처럼 EON 외부 HUD 와 연동한다.

## 왜 Xposed 인가
카카오내비는 Stealien AppSuit 무결성 검사가 있어 **재서명(smali 편집)이 막힌다**.
그래서 티맵/네이버가 재서명으로 붙였던 지점(SDK→앱 안내 콜백)을 **LSPosed 로
후킹**해서 재서명 없이 같은 데이터를 얻는다.

## 후킹 지점
`KNUSDKRepository` 내부 리스너 구현체(디컴파일 기준 $c/$d):
- 위치안내: 인자 `KNGuide_Location` → 위치/방위/도로명
- 경로안내: 인자 `KNGuide_Route` → 회전코드/거리

클래스명이 카카오 업데이트로 바뀌어도, **인자 타입 시그니처**로 콜백을 찾으므로
견딘다. SDK 안내 객체의 getter 이름(getLocation/getCurDirection/getRgCode 등)은
유지된다.

## 전송
carrot_navi_server(7714)로 티맵과 같은 JSON 스트림을 보낸다. 서버는 경로
`/kakao/` 로 소스를 구분한다(EON 설정 EonClusterHudNavApp=3).

## 1차본(0.1-recon)의 목적 = 정찰 겸 동작
- 세 콜백 인자의 실제 값을 로그로 남긴다:
  `/sdcard/Android/data/com.locnall.KimGiSa/files/kakao_hud.log`
- 동시에 확인된 getter 로 vehicle/guidance 스트림 전송을 시도한다.
- 좌표계(KATEC vs WGS84)는 로그의 x/y 범위로 확정한다(로그의 LOC shape 줄).
- 지도(KNMMapCapturer 오프스크린)와 안전/카메라 변환은 2차에서 로그 확인 후 완성.

## 설치
1. LSPosed 에서 이 모듈을 켜고, 스코프를 카카오내비로 지정(scope.xml 로 기본 지정됨).
2. 카카오내비 강제 종료 후 재실행.
3. 안내 시작 후 위 로그 파일을 공유.
