# CarrotNaver HUD13: Android Auto navigation map via NaverMap.takeSnapshot

## Why every capture so far failed on the S9

The S9 runs Naver on the car's Android Auto screen (photo, 2026-09-08). In that
mode Naver's navigation map is rendered by `com.naver.map.core.auto.map.MapProvider`
through a `MapSurface` bound to the vehicle Surface. Nothing map-like exists in
the phone Activity view tree, so:

- HUD6/HUD10/HUD11 phone capture (`CarrotMapCapture`) finds no drawable map View.
- HUD7 vehicle-Surface capture (`CarrotCarMapCapture`) uses `PixelCopy` on the AA
  Surface; that Surface is a video-encoder producer and reads back black.
- HUD8 offscreen `MapSurface` (`CarrotOffscreenMap`) never proved a GL frame.

Guidance JSON was always fine — only `map_main` was missing, so the HUD kept the
previous TMAP frame (or showed black when HUD7 sent black frames).

## What HUD13 does

The SDK can read its own framebuffer. `NaverMap.takeSnapshot(showControls,
callback)` — `p2(Z, NaverMap$SnapshotReadyCallback)` in 6.9.1.3 — schedules a
render on the renderer thread, does `glReadPixels`, and delivers a Bitmap to
`SnapshotReadyCallback.a(Bitmap)` on the main thread. This works for the AA
`MapSurface` because it is the renderer itself, not the compositor, doing the read.

- `classes5.dex`: `MapProvider.<init>` ends with
  `CarrotCarMapSnapshot.provider(this)`.
- `classes43.dex`: `CarrotCarMapSnapshot.capture(bridge)` runs first in
  `CarrotNaverBridge.captureMap()`. It calls `MapProvider.i()` (the ready
  NaverMap). While a map exists it requests one snapshot at a time (3 s timeout),
  center-crops the bitmap to 960x576, and hands it to the existing
  `sendBitmap()` (JPEG quality from the live HUD settings). If no bitmap arrives
  for 12 s (AA disconnected) it releases map_main and the HUD6 phone capture runs.
- `CarrotHudLog` writes bridge events and a 5 s status line to
  `/sdcard/Android/data/com.nhn.android.nmap/files/carrot_hud.log` (also logcat).

The snapshot is the real AA navigation map: route, car marker, camera, night
mode — the same picture the car screen shows, minus the host's TBT card.

## Build

`.github/workflows/build-naver-hud13.yml` (input HUD11 APKS, same signing
secrets as HUD9–HUD11) or locally:

```sh
python selfdrive/eon_cluster/naver_bridge/test_car_snapshot.py --java-home JDK --work /new/dir
python selfdrive/eon_cluster/naver_bridge/build_car_snapshot.py --input CarrotNaver_6.9.1.3_hud11.apks \
  --java-home JDK --sdk SDK --apktool apktool_3.0.3.jar --work /new/dir --output HUD13-unsigned.apk
```

## If it still shows no map

Read `carrot_hud.log`:

| line | meaning |
|---|---|
| `MapProvider created` | AA session started, hook works |
| `AA NaverMap available` | map ready; snapshots start |
| `first snapshot WxH` | frames flowing — HUD must show the map |
| `takeSnapshot failed: …` | SDK method mismatch (report the line) |
| `status … lastBitmapAgeMs=-1` for >10 s | renderer never answers; report |
| no `MapProvider created` at all | Naver was not on Android Auto; phone capture path applies |

## HUD13 addendum: the car screen is nMirror, not Android Auto

HUD12.1 field log (2026-09-09, connected in the car, Naver navigating) only ever
showed `no Android Auto MapProvider yet`. The manifest declares a single
CarAppService in the main process and `MapProvider` is the only class that
creates a `MapSurface`, so the Naver car screen is **not** an Android Auto
session: nMirror runs the ordinary Naver Activity on a virtual display and
streams it to the head unit. The map therefore lives in `com.naver.maps.map.MapView`
inside `CarrotNaverBridge.activity`; PixelCopy from a window on a virtual
display is what the phone-capture path was failing on.

`CarrotCarMapSnapshot.phoneMap()` walks the Activity decor view for a
`MapView` (subclasses included), reads `MapView.a0` (MapViewDelegate) and
`f()` (NaverMap), then uses the same `takeSnapshot` GL readback. Portrait
snapshots are cropped to a 5:3 band centred at 62 % height so the vehicle
marker stays in frame. Status line: `NaverMap available from phone MapView`.

## HUD13.1: two MainActivity instances under nMirror

The last build that showed the Naver map on the HUD was HUD6 at commit 923215b
(2026-09-07), before the HUD app's navigation-selection button existed. That
button runs `am start -n com.nhn.android.nmap/com.naver.map.MainActivity` on
the phone display while nMirror already hosts a MainActivity on its virtual
display. `MainActivity.onResume` -> `CarrotNaverBridge.setActivity()` keeps
only the most recently resumed instance, i.e. the phone-display copy, whose
map is not showing — so both the HUD6 capture and HUD13 `takeSnapshot` targeted
the wrong window. `setActivity` now also calls
`CarrotCarMapSnapshot.registerActivity()`, and `phoneMap()` picks the live
activity whose `MapView.isShown()`; the hidden copy is only a last resort.

## HUD13.2: NetworkOnMainThreadException

HUD13.1 in the car: `NaverMap available from phone MapView`, `first snapshot
1034x720`, ~1.4 fps, `sent=69` — yet EON had `carrot_navi_guide.json` updating
and no `carrot_navi_map.jpg`, with one healthy 7714 connection. The SDK
delivers `SnapshotReadyCallback` on the main thread; `sendBitmap()` wrote to
the socket there, Android threw `NetworkOnMainThreadException`, and the
bridge's catch-all swallowed it. HUD13.2 crops on the main thread and hands
the bitmap to a `HandlerThread` for JPEG encode + send; `sent` now counts
completed sends and failures log `sendBitmap failed`.

## HUD13.3: TMAP-sized frames and EON CPU

With the map finally flowing, EON CPU was far above TMAP's. Per frame Naver
sent 960x576 at JPEG quality 90 as Base64 JSON (300-400 KB) and
`carrot_navi_server.recv_frame` unmasked it with a per-byte Python loop.
HUD13.3 sends 640x384 like TMAP at JPEG quality 65 (encoded in
`CarrotCarMapSnapshot.sendJpeg`, since the NHUD1 quality relay was removed in
9475e5c), and EON `recv_frame` unmasks with numpy (int fallback). Apparent map
size on the HUD is unchanged.


## HUD13.4: accept mock-provider locations (nMirror car GPS)

Naver navigation froze at one spot while TMAP kept working with nMirror's
"차량 GPS 사용". Naver checks `Location.isMock()/isFromMockProvider()` in two
places — `com.naver.map.core.common.location.LocationManager$Companion.b`
(tags the provider as mock) and `com.naver.maps.navi.mapmatching.LocationExtensionsKt.a`
(used by `LocationController` and `AvnStopFilter`) — and the map-matcher
ignores tagged fixes, falling back to the phone's own GPS, which has no sky
view in the console box. HUD13.4 patches both to treat every fix as a real
provider fix. `classes.dex` is now the third changed entry.

## HUD13.5: keep polling while the renderer is paused

At a long red light the HUD Naver map froze and never resumed although Naver
itself kept navigating. `capture()` declared the renderer dead after 12 s
without a bitmap and stopped issuing `takeSnapshot` requests, so when the
virtual display woke up nothing asked for frames again. HUD13.5 keeps
requesting every 3 s while dead (map_main is released to the phone-capture
fallback meanwhile) and logs `renderer not answering` / `renderer answering
again`. The HUD GPS badge's `지도 Ns` (gpsInfo.mapAge, already in g_hud) shows
this state directly.

## HUD13.6: remaining time is milliseconds

`RoutePosition.duration()` returns a raw `TimeInterval` (its class exposes
`getMilliseconds` / `seconds-impl`), i.e. milliseconds. The bridge guessed the
unit from magnitude and treated values ≤ 200,000 as seconds, so within the
last ~3 minutes of a trip the HUD showed thousands of minutes (143,280 ms →
"2388분"). `CarrotNaverCodes.remainTimeSec` now always divides by 1000; the
bridge's `remainTimeSec` delegates to it.
