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
| `first snapshot WxH` | SDK capture callback works; this does not prove WebSocket delivery |
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
