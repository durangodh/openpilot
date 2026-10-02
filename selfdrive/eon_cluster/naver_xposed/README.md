# Naver Maps 6.10.0.16 direct LSPosed HUD module (experimental)

This module targets the **unmodified, NAVER-signed** Naver Map 6.10.0.16
(`com.nhn.android.nmap`, version code 61000004). It hooks `NaviStore`,
`MainActivity`, and `MapProvider` to read the app's live navigation state and
rendered NaverMap. `NaverBridge`, `NaverMapCapture`, and `NaverNaviClient`
implement extraction, capture, discovery, and WebSocket delivery in this
module's own Java source. There is no bundled bridge DEX or runtime dependency
on the former patched Naver app. Naver Maps' APK is not modified or re-signed.
Other app versions run in read-only mode (see Version policy).

The 6.10.0.16 arm64/xxhdpi XAPK used for static analysis has SHA-256
`9cb184e4827d6bb3b4a6d2a71bf9fc769a0fdfdc4cbd6d540d7207b71123345b2`.
Its base APK verifies with NAVER's certificate SHA-256
`0b8b8523bb4aeffa346e4bdd4fbf7d193450569aa14aaad4adfd94a3f7b227bb`.
The former custom-signed app had a different certificate and could not be
updated in place with the original package.

## Status

- Source and package structure verified against 6.10.0.16: `NaviStore` and
  `MainActivity` remain present, as do the `NaviStore` constructor and `R()`.
- The standalone module builds and is installed on the owner's S9. A local
  WebSocket receiver confirmed live guidance, route state with 220 path
  points, and JPEG map frames from the original Naver app without the old
  bridge DEX. No EON receiver was available, so HUD rendering and real-road
  safety-camera delivery remain unverified.
- The 6.10.0.16 voice hook captures the Clova `Function0` passed from
  `NaviClovaButtonComponent.Q()` to `MapButtonKt.E()` and exposes that exact
  callback on the component's ComposeView as
  `com.nhn.android.nmap:id/btn_speech_recognition` (`0x7f0b016e`). This is
  the resource ID nMirror's navigation accessibility service searches for.
  On the S9 guidance screen, a UI hierarchy dump confirms that this node is
  exposed with content description `클로바 음성인식`, `clickable=true`, and
  `enabled=true`. The vehicle button has not yet been tested in the car.
  Normal on-screen microphone behavior should remain unchanged; a live
  accessibility click is still required to confirm it.
- Local builds are debug-signed; the `naver-hud-auto` release workflow signs
  the standalone module with the permanent Remote HUD key. The former app
  and app data were backed up before replacing the app. Moving from a local
  debug-signed module to the release-signed module requires uninstalling only
  `ai.comma.naverhud` first, then installing the release APK and re-enabling
  its LSPosed scope. Do not uninstall the original Naver Maps app. Subsequent
  release APKs use the same signing key and increasing version codes.
- Version policy: there is no hard version gate. On 6.10.0.16 every feature
  is enabled. On any other version only read-only features run (NaviStore
  polling and map snapshots); the marker-size and voice-button hooks and the
  map render engine, which change app behaviour through obfuscated names,
  stay off. The log records
  `Naver <version> verified|UNVERIFIED` and, after 20 guiding ticks, a
  `health:` line showing which obfuscated getters returned values. Turning
  off Play Store auto-update for Naver Map is still recommended so a new
  version can be checked before driving.

## Map render engine (6.10.0.16 only)

On the verified version `map_main` no longer comes from `NaverMap` snapshots.
Snapshots were requested on the main thread every 200 ms, returned a
full-screen bitmap that then had to be cropped, scaled and encoded, and could
time out while the UI was busy, which showed up as a HUD map that paused and
then jumped.

`NaverMapRender` instead does what the patched TMAP did with its own map
engine:

- It creates a `MapSurface` (the Naver Map SDK class that Android Auto uses to
  draw into a car surface without a View) with the app's own map options
  (`NaverMapOptionsUtilsKt` + `AppInfo.getInstance()`), and draws it into a
  640x384 `ImageReader`. The lifecycle order follows `MapProvider`: onCreate,
  getMapAsync, onStart, onResume, then surfaceCreated/surfaceChanged.
- When the map is ready it builds a second `NaverNaviUI` (Naver Navi SDK) on
  that map with the same `GuidanceControl` as the phone guidance UI and the
  rendering configuration from `NaviSettingManagerKt`. `NaverNaviUI`
  subscribes to the guidance session events, so it draws the route line,
  the vehicle and the following camera on its own.
- It never calls `NaviStore.w1`/`NaviEngine.q`: `NaviStore` keeps a single
  guidance UI, and attaching another map there would take the route and the
  vehicle away from the phone screen.
- Every 500 ms it copies the phone guidance UI's rendering mode, view mode and
  carvatar settings, and the phone map's map type and night mode.
- If the engine draws nothing new (map at rest), the last JPEG is re-sent at
  the frame rate so EON does not clear the map as stale.
- First in-car test (S9, 6.10.0.16): the HUD showed the SDK default camera
  (Seoul City Hall) with no route or vehicle, so the guidance renderer did
  not take the camera. Since then the camera starts at the phone map's
  camera; if the route renderer (`NaverNaviUI.f466446h`) is missing 1.5 s
  after attach, `t(currentSession)` is called once, and if it is still
  missing after 6 s, or the map never becomes ready within 8 s, it falls back
  to snapshots. If the camera stays more than 300 m from the phone map's
  camera for 2 s, it copies the phone camera (`NaverMap.M()` →
  `CameraUpdate.x()` → `NaverMap.Y0()`) every 250 ms from then on.
- The b8 build still showed Seoul in the car, which points at the snapshot
  fallback: it preferred the Android Auto `MapProvider` map, which sits at the
  default camera when guidance is not drawn on it. Snapshots now take the
  map that `NaviStore`'s `NaverNaviUI` draws guidance on first, then the
  `MapProvider` map, then a visible phone `MapView`.
- Root cause of the Seoul map (found from `naver_hud.log` of the b8 drive:
  `guidance UI attached`, `first rendered map frame sent`, then nothing):
  `MapSurface` creates its `NaverMap` only after the surface exists, so the
  `onStart` (`n`) called before that never reached the map and
  `NativeMapView.nativeStart` was never called. The engine drew one frame at
  the SDK default camera and stopped; the repeat loop kept re-sending it.
  `NaverMap.d1()` (onStart) is now called in `onMapReady`.
- It starts once the phone guidance UI exists (during guidance). If it fails
  it falls back to snapshots and retries after 30 s, at most three times.
  These steps are also written to the LSPosed log (`NaverHud: map render ...`),
  so `adb logcat | findstr NaverHud` shows them without pulling the file.

Not yet verified on a device: whether two guidance renderers run side by side
without side effects, and whether the map style matches the phone screen.

## Junction view (crossroad_expanded)

The phone's enlarged junction popup comes from the guidance session:
`GuidanceSession.getJunction().getInfo().getImage()` (public, unobfuscated
API; `NaviStore` builds its popup `JunctionData` from the same `Bitmap` and
clears it when `getJunction()` is null). `NaverJunction` sends it as a JPEG
CNV2 frame on `/api/navi/ws/v2/image/naver/crossroad_expanded` when the
bitmap changes, re-sends it every 5 s while shown, and sends a clear when it
disappears or guidance stops.

`carrot_navi_server.py` used to route every NAVER/Kakao binary frame to
`map_main`, so named overlay sockets (this one and `traffic_signal`) never
reached their overlay files. Overlay socket names are now checked first for
every source. Not yet verified in the car.

## Render camera follows the phone map (like TMAP/Kakao)
The HUD render used to let its own NaverNaviUI move the camera (and only
copied the phone camera once it drifted 300 m away), so it looked different
from the snapshot. Now carSync is turned off on the HUD NaverNaviUI
(`C(false)`, what Naver does when the map is dragged), so it only draws the
route and vehicle, and every 50 ms the camera is set to the area the
snapshot crop shows: target = `Projection.b()` (fromScreenLocation) of the
crop centre on the phone map (`L0()` x `f0()` px), same tilt/bearing, zoom
+ log2(HUD width / crop width).
