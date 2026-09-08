# CarrotNaver HUD7: Android Auto map surface capture

HUD6 captures only a map View in the phone's MainActivity. Naver also renders
navigation on an Android Auto MapSurface, owned by MapProvider. That Surface
is not part of the phone Activity view tree. Navigation JSON can therefore
continue while the HUD reports that no map image is available.

HUD7 adds a second image source: the existing Android Auto map Surface. It
copies that Surface with PixelCopy and sends the result through the existing
map_main stream. It does not create a second route or renderer, alter Naver's
navigation behavior, launch an Activity, or release the host-owned Surface.

- Hooks the verified HUD6 MapProvider surface-available/resize and destruction
  callbacks. No reflective search of other apps' windows is used.
- Tries the car Surface before the bridge's Activity null check.
- Suppresses phone map captures/clear messages while the car Surface is valid.
- Returns to phone capture when the car Surface is destroyed or invalid.
- Discards captures from replaced surfaces, bounds allocations to 2048 pixels
  on the long edge, and permits one car copy at a time.
- Retries copy failures on subsequent bridge ticks. Persistent failures clear
  stale imagery; throttled `CarrotCarMap` log messages record the copy status.
- Keeps HUD6 JPEG dimensions (960 x 576), fit/crop/scale/quality settings, and
  all existing guidance and transport behavior.

## Installation and signing

Install `CarrotNaver_6.9.1.3_hud7.apks` over HUD4/HUD5/HUD6 using the same split
APK installer. There is no need to uninstall a copy signed with the certificate
below. An official Naver app or HUD3 has a different certificate.

Base and both configuration splits were verified against the existing HUD6
certificate and recovered signing key:

`b8b4093f1351346ae1642299d0f75fad33b8764c004058b85b744d514c23e6ad`

APKS SHA-256:

`522d18db01dc26192b0d50145a69899402f51abae497db64f1c43e99bd0640e3`

The original signed splits are byte-for-byte unchanged. The base manifest and
version remain 6.9.1.3. Only classes5.dex (surface hooks), classes43.dex (bridge),
ZIP alignment, and APK signatures are rebuilt. Private keys are not included.

Use EON g_hud commit 6d975c7 or later for the separate TMAP idle-turn fix. HUD7's
map stream is compatible with the current Remote HUD app; this capture change
does not require re-signing/reinstalling Remote HUD, TMAP, or nMirror.

## Validation and limits

Phone capture and actual production car capture classes pass the fake Android
Surface lifecycle harness, including no Activity, source replacement, delayed
callbacks, single-flight operation, copy failure/retry, bitmap recycling, and
phone fallback. Live UDP settings relay tests also pass. Android API 35 compile,
D8 compilation, both DEX assemblies, unchanged APK entry checks, apksigner
verification on all three APKs, and 16 KiB ZIP alignment checks passed.

No physical S9/car was available for validation. This fixes the missing Android
Auto Surface capture path; it is not proof that every reported map dropout has
that cause. If the car does not provide a Naver MapSurface (for example, a pure
phone-mirroring session), HUD7 still uses the existing phone capture path.

## Reproduce unsigned base

```sh
python selfdrive/eon_cluster/naver_bridge/test_car_capture.py \
  --java-home /path/to/jdk17 --work /new/test-dir
python selfdrive/eon_cluster/naver_bridge/build_car_capture.py \
  --input CarrotNaver_6.9.1.3_hud6.apks --java-home /path/to/jdk17 \
  --sdk /path/to/android-sdk --apktool /path/to/apktool_3.0.3.jar \
  --work /new/build-dir --output /path/to/HUD7-unsigned.apk
```

The builder validates the input hash and hook ABIs. Sign and align the resulting
base with the original HUD6 key before combining it with the untouched splits.
