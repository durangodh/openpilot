# CarrotNaver HUD13.8 marker sizing trial

Reported behavior: on the first boot, Naver's location marker can be much larger
than the surrounding map. Switching to TMAP and back makes it normal. No device
boot trace is available, so display-density caching is a working hypothesis,
not a confirmed diagnosis.

The verified HUD13.7 APK has the following relevant behavior:

- `NaviCarvatarIconManager` calculates its download scale once in the constructor.
  Its successful icon cache does not observe later display density changes.
- `NaverMapWrapper.t()` sets a `LocationOverlay` image without explicitly setting
  its pixel dimensions. Downloaded bitmap descriptors expose raw pixel sizes;
  resource descriptors resolve their intrinsic size using a context.

HUD13.8 remembers the density used when initializing the icon manager. The map
wrapper reapplies marker dimensions when setting an icon, updating position or
receiving its existing map-options/size callback. Downloaded marker dimensions
are multiplied by current map density / original density. Resource markers use
their dimensions from the current map context. Each calculation starts from the
original image, so successive display changes cannot compound the scale.

There is no startup delay, forced restart, polling loop or image redownload.
Untracked images retain SDK automatic sizing, and released maps are skipped.
HUD13.7 capture, traffic-signal, GPS and manifest payloads are preserved.

## Build and checks

`build_marker_size.py` accepts only the published HUD13.7 APKS SHA-256
`70664f27927f95a78918666669639f400b585b7e6c2eca47bc880e6807a80681`.
It changes only `classes6.dex` and `classes12.dex`, verifies every other
non-signature ZIP payload byte, and creates an unsigned APK. Compile-time SDK
stubs are excluded from the injected DEX.

`test_marker_size.py` runs 20 JVM checks including phone/virtual-display changes,
fractional density, repeated updates, independent maps, night/avatar image
replacement, resource fallbacks and invalid metrics. These do not emulate
Naver's native renderer or establish that the reported device issue is fixed.

The `build-naver-hud13.8.yml` workflow signs all splits with the existing release
key and verifies certificate SHA-256
`8864e2b9ad307f5f189de9c747697854275089fc81296d6fbb5b2a9576d23c06`.
It publishes a prerelease for device testing, preserving the stable HUD13.7.
The uploaded HUD1 APK has a different certificate and is not the upgrade baseline.

## Device verification still needed

Install over HUD13.7 with the existing split-APK installer. Cold boot with Naver
selected and check marker size before switching apps. Then switch TMAP -> Naver,
change day/night mode and verify the marker remains proportional. Compare map
startup time with HUD13.7. No artificial waiting is added; actual boot timing
and native map rendering have not been measured on the user's device.
