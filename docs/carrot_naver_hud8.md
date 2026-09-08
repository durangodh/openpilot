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
  returns false and the HUD7 car/phone capture paths run as before.

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

Verified signed HUD8 APKS SHA-256:

`aeb61bc8f4013c99c1ead173c8cd6d382af7ab5e755d7ac276eaf865f8f46e54`

The supplied unsigned base SHA-256 is
`d1163f1ec3504b3804c61ed036869b214347b5c446c7cda8ef83e04305752143`.
Its embedded `classes43.dex` exactly matches the separately supplied component,
SHA-256 `76866a60e87bc5666a894402feadc279c4238c7103150a8b378e2294b78fa299`.

## Validation and limits

The fake-Android/fake-SDK harness covers lifecycle order, navi options,
camera follow, route + progress, frame crop/throttle, stall restart and both
fallbacks. Only `classes43.dex` changes in the base APK (byte-for-byte check on
every other entry). Not validated: real GPU output of `MapSurface` while the
Naver app is in the background, and the visual tuning constants (zoom, tilt,
symbol scale, path width) — those are plain constants in
`CarrotOffscreenMap.java`.
