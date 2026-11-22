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

0.3은 서버의 중앙 후보 안정화 결과를 표시합니다. 회색 원본 박스와 색상 안정 후보를 구분하며, 내 진행 신호는 미확정입니다. 표시 조건·지연·제약은 `../signal_lab/README.md`를 참고하세요. 시험 남은 시간을 화면에 표시합니다.

0.4 APK는 EON의 선택적 APILOT 정차 유지 보조 설정을 안내합니다. 보조는 이온웹에서 별도로 켜야 하며, 내비 진행 방향 빨강 + 기존 정차 상태 + 안정된 앱 빨강이 모두 필요합니다. 앱은 주행 중 제동이나 초록불 출발 명령을 전송하지 않습니다. 상세 조건과 입력 소실 시 기존 제어로 돌아가는 동작은 `../signal_lab/README.md`를 참고하세요.
