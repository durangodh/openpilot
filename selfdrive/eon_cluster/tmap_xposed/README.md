# TMAP HUD Xposed 모듈 (1차)

플레이스토어 원본 티맵(`com.skt.tmap.ku`)에 LSPosed 모듈로 붙어, 캐롯 패치판
(CarrotNavi v11.2.3.3740)이 EON `carrot_navi_server`(7714)로 보내던 데이터를
**원본 APK 수정·재서명 없이** 보낸다. 원본을 그대로 쓰므로 플레이스토어 업데이트와
티맵 로그인/결제가 그대로 유지된다.

## 확인 기준 버전

- 티맵 **11.8.3.4061** (versionCode 4003, `TmapHudModule.VERIFIED_VERSION`)
- 분석한 XAPK(apkcombo) SHA-256
  `850f5ac03058dbfec32e7e0013e6b6ad5044b9339a7086bb1bc42240e36a2358`
- base/split APK 서명 인증서 SHA-256(v2·v3 동일)
  `90351f2e6ce0c95c3696dde9c090e803c0a0d8e3d64ce53375c9c6003cf0f8e2`
  (2014-06-02 발급, 주체 필드는 모두 `Unknown`)

## 루팅·후킹 탐지 점검(2단계)

11.8.3.4061 전체 디컴파일(jadx)과 arm64 네이티브 라이브러리 문자열 기준:

- 티맵 코드에는 실행을 막는 루팅/Xposed/Frida 탐지가 **없다**.
- `SplashViewModel.sendDeviceTamperedLog`는 원격 설정
  `united_json_send_device_tampered_detected_log`가 켜져 있을 때만 su 파일·
  에뮬레이터 여부를 검사해 `is_rooted`/`is_emulator`를 티맵 로그
  (`tmap_goldeneye:/device_tampered`)로 **보내기만** 한다. 앱 동작은 막지 않는다.
- Xposed 문자열 검사는 광고 SDK(AppLovin) 리포트용뿐이다. 서명 검사·
  `/proc/self/maps`·ptrace 검사는 티맵 코드에 없다. 상용 보안 솔루션도 없다.

## 후킹(전부 읽기 전용, 난독화되지 않은 이름)

| 후킹 | 얻는 데이터 |
| --- | --- |
| `NavigationManager.setLastRGData(RGData)` | 위치가 처리될 때마다 최종 RGData: 차량 위치·방위·속도, 현재/다음 TBT, 차로, 제한속도, SDI 배열, 구간단속, 남은 거리/시간, 이탈 |
| `TrafficSignalInfoRepository.onSignalInfoChanged(TrafficSignalStateInfo)` | C-ITS·V2V 신호(방향별 색·잔여초·거리). 두 경로가 모두 이 함수로 모인다 |
| `VSMMapView` 생성자 | 지도 캡처 대상 뷰 |

`RGData` 필드는 네이티브 엔진이 이름으로 채우므로 버전이 바뀌어도 이름이 유지될
가능성이 높다. 그 밖에 `NavigationManager`의 공개 getter(주행 모드, 경로, 재탐색 중,
도착, 경로 요약)와 `TmapNavigationEngineInterface.getVertexArray()`(경로 좌표)를
호출만 한다. 후킹 콜백에서는 참조만 저장하고, JSON 생성·전송은 전용 스레드에서 한다.

## 보내는 항목(1차)

경로: `/api/navi/ws/v2/json/tmap/state`, `/api/navi/ws/v2/render/tmap/map_main`,
`/api/navi/ws/v2/image/tmap/traffic_signal`. 서버는 경로에 `/kakao/`·`/naver/`가
없으면 티맵 소스로 받는다(EON 설정 `EonClusterHudNavApp=1`).

| 항목 | 출처 |
| --- | --- |
| `vehicle` | `vpPosPointLat/Lon`, `nPosAngle`, `nPosSpeed`, `szPosRoadName`, `eVirtualGps` |
| `guidance_current` | `stGuidePoint`(`nTBTTurnType` 티맵 코드 그대로, `nTBTDist`, `szTBTMainText` …) |
| `guidance_next` | `stGuidePointNext`. 거리는 티맵 상단 TBT처럼 구간 거리 `nSvcLinkDist`(없으면 두 안내 거리 차) |
| `lane_current` | `nLaneCount`, `nCurrentLane`, `nLaneTurnInfo`, `nLaneAvailable`, `nLaneEtcInfo`, `nLaneDist`, `roadcate`; `show`는 티맵 `ObservableLaneData.getShowLane()` |
| `speed` | `nRoadLimitSpeed`, `sdiInfo[0..1]`(`nSdiType` 티맵 코드 그대로), `sectionSpeedInfo`(구간단속) |
| `traffic_signal` | 직진·좌·우·유턴 신호(보행자·자전거·버스 제외), 잔여초는 수신 후 경과초만큼 감소, 거리는 저장소가 매초 줄이는 `currentScheduledDistance` |
| `route` | `nTotalDist`, `nTotalTime`, 경로 총거리, 차량 위치부터 목적지까지 좌표(최대 500점, 5초마다 갱신) |
| `navigation_status` | 주행 모드, 경로 유무, 도착, 이탈(`eRgStatus == 5` 또는 재탐색 중) |
| `map_main` | `VSMMapView`의 GL 표면을 `PixelCopy`로 640×384 JPEG(q65, 최대 5fps), CNV2 format 2 |
| 신호등 PNG | 카카오/네이버 모듈과 같은 원형 신호등 그림, CNV2 + PNG |

값이 바뀌었을 때와 1초 하트비트로만 보낸다. `route`는 최대 1Hz다. RGData가 3초 넘게
오지 않으면 주행 종료로 보고 안내 항목을 지운 뒤 하트비트를 멈춘다.

## 지도

티맵에는 네이버 `takeSnapshot`이나 카카오 `KNMMapCapturer` 같은 오프스크린 캡처
API가 없다. 대신 지도 엔진이 그리는 SurfaceView(또는 TextureView)만 `PixelCopy`로
복사한다. 경로선과 차량 아이콘은 같은 GL 화면에 그려지므로 함께 찍히고, 안내 배너
등 안드로이드 UI는 빠진다. 지도 엔진의 화면 중심(`MapEngine.getScreenCenter`,
주행 중 차량 위치)을 기준으로 5:3 영역을 잘라 낸다.

**제약**: 티맵 지도가 화면에 보일 때만 프레임이 나온다. 티맵이 백그라운드로 가면
2초 뒤 CNV2 clear를 한 번 보내 EON에 옛 지도가 남지 않게 한다. HUD 지도의
확대 수준·주야간은 폰 화면을 그대로 따른다.

## 버전 정책

하드 버전 게이트는 없다. 앱 시작 시 로그에 `TMAP <버전> verified|UNVERIFIED`를 남긴다.
다른 버전에서도 읽기 전용 후킹은 켜고, 이름을 못 찾은 후킹만 빠진다
(`read-only hooks installed = N/3`, `field missing: …`, `method missing: …`).
앱 동작을 바꾸는 후킹은 `behaviorHooksAllowed`(확인 버전에서만 true)일 때만
설치한다. 1차 구현에는 그런 후킹이 없다. 티맵 자동 업데이트는 꺼 두고,
새 버전은 로그를 확인한 뒤 쓰는 것을 권장한다.

## 로그

`/sdcard/Android/data/com.skt.tmap.ku/files/tmap_hud.log`(루트 없이 파일 탐색기로
꺼낼 수 있다). 첫 RGData·첫 신호·경로 좌표 수·첫 지도 프레임이 기록된다.

## 확인 상태

- 호스트: `TmapJsonCheck` 통과. Java 전송 클라이언트로 실제 `carrot_navi_server.py`에
  JSON·CNV2 지도·신호등을 보내, EON 파일과 `navigation_route.py` 해석(회전 종류·
  거리, 다음 안내, 신호 위상, 구간단속)과 clear 처리를 확인했다.
- 호스트: 티맵과 같은 이름의 가짜 클래스(RGData, NavigationManager,
  TrafficSignalInfoRepository …)로 `TmapBridge`를 돌려 리플렉션 경로, 경로 좌표
  자르기, 신호 거리, RGData 끊김 시 안내 지우기를 확인했다(저장소에는 넣지 않음).
- 모듈이 부르는 티맵 이름은 jadx가 바꾼 이름이 아닌지 확인했다. 예:
  `ObservableTrafficSignalData`의 getter 실제 이름은 `isTrafficSignalVisible()`이라
  같은 이름의 필드를 직접 읽는다.
- 모듈 APK는 `gradle :app:assembleDebug`로 빌드된다.
- **실기 미확인**: 후킹 동작, 지도 PixelCopy 화면 중심 좌표계, `nCurrentLane`이
  1부터 시작하는지(EON HUD는 1..n을 기대), SDI·구간단속 실제 값, 신호 상태 코드.

## 2차(예정): 티맵 자체 그림

`tbt_*`, `lane_*`, `safety_*`, `crossroad_*`. 후보 출처:
`TmapNavigation.getCrossImageBuffer`/`getDirImageBuffer`(네이티브 교차로·방면 이미지),
`ObservableTBTData`/`ObservableLaneData`/`ObservableSDIData` LiveData를 그리는
티맵 UI 뷰.

## 빌드

`.github/workflows/build-tmap-hud.yml`이 `g_remote`/`g_hud`/`g_abcd` 푸시 때
호스트 검사 → 디버그 빌드 → Remote HUD 키 서명 후 릴리스 `tmap-hud-auto`에
`TmapHud-latest.apk`로 올린다.
