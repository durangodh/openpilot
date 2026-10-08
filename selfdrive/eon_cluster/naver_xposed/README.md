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
  polling and map snapshots); the marker-size and voice-button hooks, which
  change app behaviour through obfuscated names, stay off. The log records
  `Naver <version> verified|UNVERIFIED` and, after 20 guiding ticks, a
  `health:` line showing which obfuscated getters returned values. Turning
  off Play Store auto-update for Naver Map is still recommended so a new
  version can be checked before driving.

## Map source: phone map snapshots

The HUD map is a snapshot of the phone map (guidance map, else the visible
MapView), as the old bridge app sent. An offscreen engine render was tried
and removed: it competed with the HUD app on the same S9 and made the HUD
map stutter.

## Lane strip (lane_bottom)

`NaviLaneControlView.a(NaviLaneItem, boolean)` is hooked. While the item has
lanes, the phone's own lane view (arrows and distance) is drawn to a bitmap
every 500 ms and sent as PNG on `/api/navi/ws/v2/image/naver/lane_bottom` when
it changes (and every 5 s). The HUD shows it below the map, where TMAP's lane
strip goes. An empty item sends a CNV2 clear.

## Traffic signal (traffic_signal)

`NaviTrafficSignalView.a(boolean, TrafficSignalInfo)` is hooked the same way:
the phone's own signal view (per-direction lamps and countdown) is drawn and
sent on `/api/navi/ws/v2/image/naver/traffic_signal`, so the HUD shows the
same signals as the phone and hides them when the phone does. Until that view
has been updated once, the module falls back to drawing its own lamp from the
straight (or only) signal.

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
