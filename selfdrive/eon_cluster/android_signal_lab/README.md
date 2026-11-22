# Korean Signal Lab Android app

Main-phone inference over EON's road camera; observer only. Four-digit pairing, leading zeros retained. Install the APK from the `signal-lab-auto` GitHub release, then follow `../signal_lab/README.md`.

- arm64 Android 11+; no phone camera/location permission.
- LightSignal v20260727 Korean-trained weights, immutable URL and SHA-256 checked at build/runtime.
- Five output classes in fixed order Green/Left/Red/Speed_Sign/Yellow; speed signs are excluded, red+left are preserved as separate candidates.
- CPU four-thread LiteRT baseline; no assumptions about phone GPU/NPU performance.
- Background observation uses a connected-device foreground service; each run is capped at 10 minutes.

Build on Linux with Python 3, JDK 17+ and internet access:

```sh
python3 selfdrive/eon_cluster/android_signal_lab/build_apk.py
```

Build tools/LiteRT are pinned by SHA-256 in `dependencies.json`; the Korean model is downloaded into ignored `assets/korean-signal.tflite` when missing. No Gradle installation is required. The same public trial key supplied with the 0.1 source package is encoded in `signing/trial.p12.base64` so updates replace the original app. This key is for this experimental app, unrelated to the permanent Remote HUD key. CI supplies monotonically increasing version codes.

New app code/model are AGPL-3.0; complete app/bridge source is in this repository. Refer to LICENSE, model provenance in assets/THIRD_PARTY_NOTICES.txt and LiteRT license in assets/LiteRT-LICENSE.txt.
