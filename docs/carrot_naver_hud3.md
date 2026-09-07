# CarrotNaver 6.9.1.3 HUD3

The map-only capture correction is maintained in
[`selfdrive/eon_cluster/naver_bridge`](../selfdrive/eon_cluster/naver_bridge/README.md).
Its unsigned build has been verified, but it is not a signed replacement release.

This document records the reproducible HUD bridge changes shipped in the `CarrotNaver_6.9.1.3_hud3.apks` release asset. The APK bundle is kept in GitHub Releases because its 138 MB size exceeds the normal repository file limit.

## Release

- Release: https://github.com/durangodh/openpilot/releases/tag/carrot-naver-6.9.1.3-hud1
- Asset: `CarrotNaver_6.9.1.3_hud3.apks`
- APKS SHA-256: `2975b6d43b1786de4864b126f44895b6d55e2c1fea448a39cc93ef15dbb64071`
- Signing certificate SHA-256: `f44cd44e862dcdc254efc6fff207b18d3c724ded139ecc1a1a72c00c894b3252`

## APK bridge changes

- Start `CarrotNaverBridge` from the fully initialized `NaviStore` constructor by calling `CarrotNaverBridge.update(store)`.
- Read Naver junction guidance through `NaviStore.R()` and obtain the junction bitmap through `JunctionData.d()` with `m()` as a fallback.
- Encode new junction bitmaps as JPEG (quality 65) and publish them as `crossroad_expanded`.
- Cache the last junction bitmap identity to avoid retransmitting an unchanged overlay, and publish a null item when the junction view is cleared.
- Keep `map_main` in JPEG format for compatibility with the current `selfdrive/carrot_navi_server.py` receiver.

## HUD3 packaging fix

- Correct the Smali exception-control flow that caused HUD2 to close immediately at launch.
- Preserve every original base APK resource and launcher-icon entry.
- Replace only `classes12.dex` and `classes43.dex`; all other non-signature ZIP entries retain their original size and CRC.

## Installation

All APK splits (`base`, `arm64-v8a`, and `xxhdpi`) use the same signing certificate. HUD3 uses the same signing certificate as HUD2 and can be installed over HUD2. H.264 map streaming and a separate `lane_ahead` data source are not included in this build.
