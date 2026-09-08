## HUD12 (append to selfdrive/eon_cluster/naver_bridge/README.md)

HUD12 sends the Android Auto navigation map itself: `MapProvider` hands its
NaverMap to `CarrotCarMapSnapshot`, which uses the SDK's `takeSnapshot` (GL
readback, not PixelCopy) at 2 fps and center-crops to 960x576. Phone capture
remains the fallback. `CarrotHudLog` writes `carrot_hud.log` under the app's
external files dir. See `docs/carrot_naver_hud12.md`, `build_car_snapshot.py`,
`test_car_snapshot.py`, `.github/workflows/build-naver-hud12.yml`.
