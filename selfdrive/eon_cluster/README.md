# S9 Remote TURZX HUD

This branch uses one external-HUD path only:

- EON publishes compact driving telemetry over UDP 7210.
- EON forwards native compressed TMAP assets over TCP 7211.
- The Galaxy S9 renders the dashboard, creates the HUD JPEG, and sends it to the TURZX USB display.
- The legacy direct-EON renderer and USB output process have been removed.

Enable it with the **S9 외부 HUD 사용** toggle. The following Params are sent to the S9 at runtime:

- `EonClusterHudFps`: 0 pauses S9 rendering; 1-15 FPS otherwise (7 recommended).
- `EonClusterHudMapFps`: 2-5 FPS for the native TMAP map stream (3 recommended).
- `EonClusterHudBrightness`: 0 automatic; 1-100 fixed.
- `EonClusterHudJpegQuality`: 20-95 (55 recommended).
- `EonClusterHudScreenMode`: 1 automatic, 2 live debug, 3 fixed trip report.
- `EonClusterHudTheme`: 0 automatic, 1 dark, 2 light.
- `EonClusterHudOrientation`: 0 normal, 2 rotated 180 degrees.
- `EonClusterHudMirror`: 0 normal, 1 mirrored.
- `EonClusterHudLanguage`: 0 Korean, 1 English.
- `EonClusterHudRadarInfo`: 0 hidden; 1/3 relative speed; 2/4 distance and relative speed.
- `EonClusterHudLayoutMode`: 1 driving/TMAP/system; 2 driving/TMAP only.

The display layout is selected with `EonClusterHudLayoutMode`: mode 1 shows driving, TMAP, and system information; mode 2 hides the system panel and expands TMAP to fill the remaining half of the screen. The driving panel uses model lane/path and radar data, while the TMAP panel uses the original map frame and selected native guidance assets.

`EonClusterHudConnected` reports only the recent S9 UDP acknowledgement. It is useful as a network status indicator but is not treated as proof that the TURZX USB display is working. The EON driving UI therefore remains complete and independent of S9/USB status.

TMAP render files are written atomically under `/dev/shm`. Only validated JPEG frames refresh the map watchdog. A stale map is removed after five seconds even if the TMAP control socket disconnects, and a later valid frame recreates it automatically.

The S9 application must support telemetry protocol v6 and the TCP asset tags `MAP1`, `TBT1`, `TBT2`, and `LANE`.

Protocol v6 optionally publishes `mapPose` (TMAP vehicle latitude, longitude,
heading) for the S9-local display context. It is never consumed by vehicle
control. If `hud_map.sqlite` is absent, rendering remains model-only.

The optional database is stored at the app external-files path:

```text
/sdcard/Android/data/ai.comma.remotehud/files/hud_map.sqlite
```

When that file is missing, the S9 app downloads `hud_map.sqlite` from the
repository's `hud-map-v1` Release, validates its format and z16 tile table, and
atomically installs it. Rendering continues without local map context while
the background download is in progress.

`tools/build_hud_map_db.py` packs VWorld/NGII SHP ZIPs into z16 JSON tiles.
Buildings and road centerlines use the `b` and `r` payloads. A previously built
database can be extended with flat park/green (`g`) and river/lake (`w`)
polygons without rebuilding the building and road layers:

```bash
python3 selfdrive/eon_cluster/tools/build_hud_map_db.py \
  --base-db hud_map.sqlite \
  --park-shp-zip C_UQ153.zip \
  --water-shp-zip N3A_E0032111.zip \
  --water-shp-zip N3A_E0052114.zip \
  --output hud_map_with_land_water.sqlite
```

The converter crops the nationwide inputs to the base database tile bounds,
keeps park and green-facility classes from `C_UQ153`, simplifies large rings,
and preserves bounded S9 runtime work. Water and green areas are drawn below
local roads, model lanes, route, and vehicles; the right-side TMAP frame is
unchanged.
