# CarrotNaver 6.9.1.3 HUD2

This document records the reproducible HUD bridge changes shipped in the `CarrotNaver_6.9.1.3_hud2.apks` release asset. The APK bundle is kept in GitHub Releases because its 138 MB size exceeds the normal repository file limit.

## Release

- Release: https://github.com/durangodh/openpilot/releases/tag/carrot-naver-6.9.1.3-hud1
- Asset: `CarrotNaver_6.9.1.3_hud2.apks`
- APKS SHA-256: `997dfdbee1102a81c1ddf0e3329cece7ffb2b89d60fd07861f99c2f88d2e0f9b`
- Signing certificate SHA-256: `f44cd44e862dcdc254efc6fff207b18d3c724ded139ecc1a1a72c00c894b3252`

## APK bridge changes

- Start `CarrotNaverBridge` from the fully initialized `NaviStore` constructor by calling `CarrotNaverBridge.update(store)`.
- Read Naver junction guidance through `NaviStore.R()` and obtain the junction bitmap through `JunctionData.d()` with `m()` as a fallback.
- Encode new junction bitmaps as JPEG (quality 65) and publish them as `crossroad_expanded`.
- Cache the last junction bitmap identity to avoid retransmitting an unchanged overlay, and publish a null item when the junction view is cleared.
- Keep `map_main` in JPEG format for compatibility with the current `selfdrive/carrot_navi_server.py` receiver.

## Installation

All APK splits (`base`, `arm64-v8a`, and `xxhdpi`) use the same signing certificate. Because the signing certificate differs from HUD1, uninstall the previous package and install HUD2 with SAI. H.264 map streaming and a separate `lane_ahead` data source are not included in this build.
