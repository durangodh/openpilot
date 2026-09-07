# Naver map-only HUD capture

This patch targets the verified `CarrotNaver_6.9.1.3_hud3.apks` release.
It keeps the TMAP-compatible `map_main` JPEG transport (960×576, quality 90)
and the existing HUD guidance/ETA overlay pipeline. It does not implement
TMAP's separate offscreen map renderer inside Naver.

HUD3 found a map SurfaceView/TextureView rectangle but copied the **Window**,
which can omit independently composited map buffers. If no map was found,
it copied the whole Activity, including the start page. The replacement:

- Captures TextureView pixels using `getBitmap`, or a SurfaceView buffer using
  `PixelCopy.request(SurfaceView, ...)`.
- Accepts only the verified VGX renderer hierarchy or a render view belonging
  to Naver's `MapView`; unrelated advertisement/video views are rejected.
- Sends a null `map_main` update when the map is absent/hidden/unavailable,
  using the receiver's existing map-clear protocol.
- Keeps JPEG encoding/network work on a worker and permits one capture in flight.

## HUD5 text clarity correction

HUD4 copied any source aspect ratio into 640×384, which flattened portrait-map
labels and enlarged low-resolution JPEG text. HUD5 first reads back the map at
its own aspect ratio (long edge capped at 2048), then crops to a 5:3 landscape
viewport with equal scaling on both axes. The crop favors the lower map area;
it removes some map coverage rather than squeezing the whole portrait screen.
The transmitted bitmap is 960×576 with JPEG quality 90. The encoded metadata
matches those dimensions. TMAP is unchanged.

Aspect-ratio tests cover portrait, landscape, large and small inputs, crop
bounds, final dimensions, and equal horizontal/vertical scaling. HUD5 is signed with the recovered HUD4 key and supports an in-place update.
On-device text verification remains. See [HUD5 release notes](../../../docs/carrot_naver_hud5.md).

## HUD6 live bridge settings

HUD6 requests a real landscape Activity layout and defaults to fitting the entire
native map into the output. It does not crop the portrait map to fill the panel.
Margins can remain where the phone/map aspect ratio differs from the HUD panel.
This preserves map coverage and reduces oversized labels compared with HUD5's crop.
It is still Naver's own map renderer/zoom, not TMAP's dedicated offscreen renderer.

Update EON, Remote HUD 1.17 (CI uses an increasing versionCode), and CarrotNaver HUD6 once. Thereafter
these EON S9HUD settings reach the Naver bridge without rebuilding the Naver APK:

| Parameter | Default | Meaning |
| --- | --- | --- |
| EonClusterHudNaverLandscape | 1 | 1: landscape Activity; 0: original orientation |
| EonClusterHudNaverMapFit | 1 | 1: whole-map fit; 0: crop to fill |
| EonClusterHudNaverMapScale | 100 | 50–100%, image display size; smaller adds margins |
| EonClusterHudNaverMapQuality | 90 | JPEG quality 60–95 |

The EON sends these bounded values in HUD telemetry. Remote HUD relays them at
most once per second to `127.0.0.1:28992` using the versioned `NHUD1` message.
The bridge validates the whole message and atomically applies a settings snapshot.
No setting changes navigation routes, driving controls, or the native map's zoom.
Values are refreshed while connected; after a Naver process restart defaults apply
until the next relay packet arrives. Older Remote HUD builds can use HUD6 defaults,
but cannot relay EON adjustments. Landscape selection can cause Activity recreation.

Tests exercise the real UDP relay/receiver, malformed and out-of-range messages,
TMAP isolation, fit/crop geometry, surface routing and bitmap recycling.

## Reproduce

Requires JDK 17, Android platform 35/build-tools 35.0.0, Apktool 3.0.3, Python 3,
and the original HUD3 APKS from the repository's GitHub Release.

```sh
python selfdrive/eon_cluster/naver_bridge/test_capture.py \
  --java-home /path/to/jdk --work /new/test-directory

python selfdrive/eon_cluster/naver_bridge/build_patch.py \
  --input CarrotNaver_6.9.1.3_hud3.apks \
  --java-home /path/to/jdk --sdk /path/to/android-sdk \
  --apktool /path/to/apktool_3.0.3.jar \
  --work /new/build-directory --output CarrotNaver_HUD6_base_UNSIGNED.apk
```

The input SHA-256 is checked before patching. The script assembles only the bridge
DEX and verifies that every other non-signature ZIP entry is byte-for-byte
unchanged. The APK manifest/version code is preserved; HUD4 identifies this patch
revision, not a changed upstream Naver version.

## Signing/install status

A signed HUD4 package is now published with a new certificate:
`b8b4093f1351346ae1642299d0f75fad33b8764c004058b85b744d514c23e6ad`.
It requires uninstalling HUD3 first. See [release notes](../../../docs/carrot_naver_hud4.md).
The build script itself still produces an unsigned intermediate as described below.


The build output is **unsigned and cannot be installed as delivered**.
Zipalign it and sign it using the original HUD3 signing key. Verify the resulting
certificate SHA-256 is
`f44cd44e862dcdc254efc6fff207b18d3c724ded139ecc1a1a72c00c894b3252`
before packaging it with the original arm64-v8a and xxhdpi splits. Do not replace
the release with an unsigned bundle or mix different signing certificates.

## Validation

Production Java capture routing was exercised against a fake Android API:
no map, unrelated video/ad views, both texture/surface paths, hidden/unavailable
maps, failed PixelCopy followed by recovery, VGX identification, bitmap recycling.
Java compilation against Android API 35, D8 conversion, Smali assembly, and ZIP
payload preservation checks passed. These checks do not validate GPU pixels,
actual phone lifecycle/concurrency, or a running S9; device verification remains.
