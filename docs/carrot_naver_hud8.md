# CarrotNaver HUD8: offscreen HUD map (TMAP CarrotMapRenderStream equivalent)

HUD3–HUD7 produced `map_main` by copying pixels the phone was already showing
(the map View in the Activity, or the Android Auto MapSurface). Every one of
those paths depends on something outside the bridge: a foreground Activity,
a visible/`isShown` map view, the requested orientation being applied, or a
live car session. When any of them is missing the bridge sends a map-clear and
the HUD map panel goes blank, which is what users saw while TMAP kept working.

TMAP does not capture anything. Its `CarrotMapRenderStream` creates a second
`NaviMapEngine`, binds it to an `ImageReader`, copies the camera from the main
map and streams frames regardless of the phone screen. HUD8 does the same with
Naver's own SDK.

## What HUD8 adds

`com.naver.map.carrot.CarrotOffscreenMap` (new, classes43.dex):

- Creates a private `com.naver.maps.map.MapSurface` (the SDK renderer Naver uses
  for Android Auto) on the main thread and binds it to a 960 x 576
  `ImageReader`. No Activity, window, orientation change or car session.
- `NaverMapOptions.mapType(Navi)`, symbol scale 1.25, building height 0,
  day mode, GL fps limit 12, content padding so the vehicle sits in the lower
  third of the frame.
- Camera follows the navigation store: position / heading / speed from the
  same obfuscated accessors the bridge already uses (`P` -> getLocation,
  getHeading, getSpeedKmPerHour). Linear 240 ms animation, zoom 17 at
  <= 30 km/h down to 15.2 at >= 110 km/h, tilt 42. Below 3 km/h the last
  bearing is kept.
- The active route (`K.e.h.getPathPoints`) is drawn as a `PathOverlay`
  (blue, at most 2000 vertices); the passed part is greyed with
  `setProgress` from the nearest route vertex. The overlay is removed when the
  route disappears.
- Frames are throttled to 5 fps and handed to the existing
  `CarrotNaverBridge.sendBitmap()`, so JPEG quality still follows the live
  `NaverHudSettings` relay and the EON receiver is unchanged.
- Recovery: if no GL frame arrives for 20 s the surface is rebuilt (up to 3
  times); after that, or if the SDK cannot be constructed at all, `capture()`
  returns false. The HUD7 car/phone capture paths remain active until the first
  real offscreen frame arrives, so initialization alone cannot hold the HUD on
  `WAITING FOR MAP`.

`CarrotNaverBridge.captureMap()` gets one hook before the HUD7 car-capture
hook: `if (CarrotOffscreenMap.capture(this)) return;`. While the offscreen
renderer is alive the phone map is never captured and the Activity orientation
is never forced.

## Verified SDK contract (CarrotNaver 6.9.1.3, R8 names)

| Use | Member |
| --- | --- |
| lifecycle | `MapSurface.h(Bundle)` onCreate, `n()` onStart, `l()` onResume, `k()` onPause, `o()` onStop, `i()` onDestroy |
| surface | `MapSurface.r(Surface)`, `q(Surface,int,int)`, `s()`, `f(OnMapReadyCallback)` -> `OnMapReadyCallback.s(NaverMap)` |
| options | `NaverMapOptions.U0(MapType)`, `q1(float)` symbol scale |
| map | `NaverMap.A1(float)` -> nativeSetBuildingHeight, `b2(boolean)` -> nativeSetNightModeEnabled, `D1(int x4)` content padding, `N1(int)` -> MapViewDelegate.A fps limit, `Y0(CameraUpdate)`, `C1(CameraPosition)`, `m0()` LocationOverlay |
| camera | `CameraUpdate.x(CameraPosition)`, `b(CameraAnimation,long)`, `CameraPosition(LatLng,zoom,tilt,bearing)` |
| overlay | `Overlay.o(NaverMap)` setMap, `PathOverlay.setCoords/setProgress/...`, `LocationOverlay.setPosition/setBearing` (not obfuscated) |

All access is reflective; a missing member logs a warning and falls back.

## Build / install

```sh
python selfdrive/eon_cluster/naver_bridge/test_offscreen_map.py \
  --java-home /path/to/jdk17 --work /new/test-dir
python selfdrive/eon_cluster/naver_bridge/build_offscreen_map.py \
  --input CarrotNaver_6.9.1.3_hud7.apks --java-home /path/to/jdk17 \
  --sdk /path/to/android-sdk --apktool /path/to/apktool_3.0.3.jar \
  --work /new/build-dir --output /path/to/HUD8-unsigned.apk
```

Zipalign and sign the base with the existing HUD key
(`b8b4093f1351346ae1642299d0f75fad33b8764c004058b85b744d514c23e6ad`),
repackage it with the untouched HUD7 `split_config.arm64_v8a.apk` and
`split_config.xxhdpi.apk`, and install over HUD4–HUD7 without uninstalling.
No EON, Remote HUD, TMAP or nMirror change is required; the EON
`EonClusterHudNaverLandscape/MapFit/MapScale` settings no longer matter while
the offscreen renderer is active (quality still applies).

Published HUD8 bundle checksums:

- `CarrotNaver_6.9.1.3_hud8.apks` SHA-256:
  `7752e59949f0d57cfbefe95f136805ea1accc122d4876f1c7248ad50a914976c`
- unsigned base SHA-256:
  `288a5e534a01ecaaaeadad3ad6e3ffe3424a8ce1d5ca83a2fcaa0d75fd8a3c02`
- embedded `classes43.dex` SHA-256:
  `3022e54801f825ca734e44ad0880347549ff095ba5ec605262c7af54280e61cf`

## Validation and limits

The fake-Android/fake-SDK harness covers lifecycle order, navi options,
camera follow, route + progress, frame crop/throttle, stall restart and both
fallbacks. Only `classes43.dex` changes in the base APK (byte-for-byte check on
every other entry). Not validated: real GPU output of `MapSurface` while the
Naver app is in the background, and the visual tuning constants (zoom, tilt,
symbol scale, path width) — those are plain constants in
`CarrotOffscreenMap.java`.

## TMAP code parity (CarrotNaverCodes)

`CarrotNaverCodes` replaces the bridge's `turnType` / `laneTurn` / `safetyJson`
so a Naver route feeds NOO / ATC / speed control and the HUD banner with the
same codes a TMAP route does.

| Naver `TurnPointType` (value) | TMAP TBT |
| --- | --- |
| Straight 1, StraightAtTurn 111, LaneChangeStraight 112, *Straight access/exit 50–56/75/78, DivideAndJoin 83, Rest/Shelter/Via 85–87, tollgates 121–123, tunnel/bridge/ferry 43–49 | 11 |
| Left 2, UnsafeLeft 8, Direction9 12 | 12 |
| Right 3, Direction3 15 | 13 |
| UTurn 6, PTurn 7 | 14 |
| Direction8 11 (8 o'clock) | 16 |
| LeftDirection 4, Direction11 13, AccessLeft 41, *Left access/exit/side 57–65, car-only left 76/79, JoinLeft 81 | 17 |
| RightDirection 5, Direction1 14, AccessRight 42, *Right access/exit/side 66–74, car-only right 77/80, JoinRight 82 | 18 |
| Direction4 16 (4 o'clock) | 19 |
| LaneChangeLeftDirection 113 / RightDirection 114 | 20 / 21 |
| Rotary 21–34, Roundabout 91–104 | 131–142 |
| Goal 88 | 2 |

EON `navigation_route.py`: 18 (2 o'clock / keep right) is now in `FORK_RIGHT`,
matching 17 in `FORK_LEFT`; previously it classified as "none".

| Naver `SafetyCode` (value) | TMAP SDI | limit passed to EON |
| --- | --- | --- |
| SpeedCam 1, BoxSpeedCam 6, VariableSpeedCam 21, MoveSpeedCam 23, SchoolZone 131, SilverZone 132 | 1 | yes |
| SpeedSignalCam 2, VariableSpeedSignalCam 22 | 0 | yes |
| StartSectionSpeedCam 12, VariableSectionStart 14 | 2 | yes |
| EndSectionSpeedCam 13, VariableSectionEnd 15 | 3 | yes |
| BusCam 4 | 4 | no |
| Traffic/Parking/Overload/LaneIntrusion/SideLane/Tunnel/Tailing/BadLoad/Green 5–25 | 5 | no |
| SpeedBump 104 | 22 | bump speed (EON) |
| everything else (curves, accident areas, zones, ...) | 99 | no |

Only speed-enforcing types keep `speed_limit_kph`, so an informational
"dangerous curve" code with a posted limit no longer triggers camera
deceleration.  Lane `LaneDirection` sets map to 11 / 12 / 13 / 14 / 20 / 21 / 22.

EON `cruise_helper.py`: `EonClusterHudNavApp` is now read once per second
instead of on every 100 Hz control frame.
