# Naver HUD LSPosed bridge (experimental)

This module targets the **unmodified, NAVER-signed** Naver Map 6.10.0.16
(`com.nhn.android.nmap`, version code 61000004). It hooks `NaviStore` after
construction and `MainActivity.onResume`, then starts the existing Naver HUD
bridge in the app process. Naver Map's APK is not modified or re-signed.

The bridge DEX in `app/src/main/assets/naver_bridge.dex` contains only the
`com.naver.map.carrot` bridge classes extracted from this owner's currently
installed, custom-signed 6.9.1.3 HUD APK (`classes43.dex`, SHA-256
`55505563329a8cd8cfdc927cdbea97503f425421ed354bc826d03124c9025a01`).
It is loaded in memory with the Naver app's class loader as its parent. This
reuse is a *porting baseline*, not proof that every reflective call still
works against 6.10.0.16. The hook deliberately does nothing on other versions.

The 6.10.0.16 arm64/xxhdpi XAPK used for static analysis has SHA-256
`9cb184e4827d6bb3b4a6d2a71bf9fc769a0fdfdc4cbd6d540d7207b71123345b2`.
Its base APK verifies with NAVER's certificate SHA-256
`0b8b8523bb4aeffa346e4bdd4fbf7d193450569aa14aaad4adfd94a3f7b227bb`.
The currently installed 6.9.1.3 patch has a different certificate and cannot
be updated in place with the original package.

## Status

- Source and package structure verified against 6.10.0.16: `NaviStore` and
  `MainActivity` remain present, as do the `NaviStore` constructor and `R()`.
- Module debug APK builds and loads in Naver 6.10.0.16 on the owner's S9.
  LSPosed logs `NaverHudModule: ready for Naver 6.10.0.16` and the bridge logs
  `CarrotCarMapSnapshot: activity resumed`. With guidance started, the Naver
  process attempts a TCP connection to the HUD receiver on port 7714. No EON
  receiver was reachable in this test (the socket remained `SYN-SENT`), so
  guidance, camera, route, map frames, and EON reception have **not** yet been
  verified.
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
- This is an experimental debug-signed module, not a release. The previous
  custom-signed Naver 6.9.1.3 APK and app data were backed up before replacing
  the app. A different module signing key requires uninstalling the module
  before reinstalling it; keep a stable key for future updates.
- The module has an exact 6.10.0.16 version gate. A later Play Store update
  will leave the Naver app untouched but disable the bridge until retested
  and updated for that version.

## Build

With Android SDK 35 and Gradle 8.9 available:

```sh
gradle :app:assembleDebug
```

Enable the module in LSPosed with Naver Map as its scope. Installing the module
alone does not replace the existing patched Naver Map app. It remains inert on
6.9.1.3 due to the explicit version gate.
