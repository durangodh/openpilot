# HUD13.12 — nMirror navigation voice button

Analyzed inputs:

- `CarrotNaver_6.9.1.3_hud13.11.apks`, SHA-256 `a584f0d22f00570e3a170cd2fd9bd39cf60582febb0be35f346c633d9fbbe880`.
- `nMirrorOS_20260926_2240.img.xz`, supplied by the user; extracted `/system/product/priv-app/nMirror2/nMirror2.apk`.

## Actual delivery path

nMirror's `com.legendn.nmirror.car.NavigationButtonService.a(String, boolean, boolean)` finds a visible navigation accessibility node by resource ID, then calls `performAction(16)` (`ACTION_CLICK`). Its voice-ID list includes `btn_speech_recognition` and `v_clova_button`. `or.w()` searches `package + ":id/" + name` and requires a visible, enabled, clickable target (or child/parent).

Naver's navigation microphone is implemented in Compose. `NaviClovaButtonComponent.Q()` passes Function0 register v5 to `MapButtonKt.E()`. This is `clova/o.invoke() -> NaviClovaButtonComponent.K() -> R() -> g0()`, including the original Clova state, permission and agreement handling. The component's ComposeView is created without a resource ID or native OnClickListener.

`btn_speech_recognition` still exists in the original Naver resource table as `0x7f0b0174`. `v_clova_button` does not appear in that table. Thus simply adding a KEYCODE_SEARCH Activity handler would not fix nMirror's configured accessibility-click path.

## Patch

Bind the exact rendered Function0 to the microphone component's own ComposeView:

- Assign the existing `btn_speech_recognition` resource ID.
- Mark the host important for accessibility and give it a native OnClickListener.
- Invoke the exact current Compose callback on accessibility click.
- Reject hidden, detached or disabled hosts; suppress duplicate adapter clicks within 350 ms, preserving this state across recomposition.
- Keep the Compose children and their original touch behavior.

Only `classes4.dex` changes. The manifest, resource table, primary dex, HUD bridge in `classes43.dex`, other dex files, native libraries and split APK payloads are preserved. nMirror and EON are not modified. The release signing stage uses the existing HUD certificate and verifies every split against SHA-256 `8864e2b9ad307f5f189de9c747697854275089fc81296d6fbb5b2a9576d23c06`.

## Validation and limits

- Production Java adapter tests cover ID/accessibility exposure, original callback invocation, duplicate suppression across recomposition, hidden/disabled/detached views, callback replacement and exceptions.
- The build rejects any input other than the exact HUD13.11 bundle and verifies that only `classes4.dex` changed in the output APK.
- Local APK assembly succeeds. Signed split verification runs in Actions.
- This environment cannot perform an on-device Android accessibility query or test the vehicle button. Android test doubles verify adapter logic, not Android framework behavior.
- nMirror's existing navigation accessibility service must be enabled. Its current service selects the focused application window on display 0. The navigation microphone must be present and visible (route guidance or safe-driving screen).

On-device acceptance: keep nMirror's voice-button navigation option enabled and Naver selected, open the navigation screen, press the vehicle voice button once, and verify the same Clova listening UI as a direct microphone tap. nMirror should log `Navigation button: id=com.nhn.android.nmap:id/btn_speech_recognition, action=voice, clicked=true`; Naver should log `CarrotVoiceButton: Navigation Clova screen-button callback invoked`.
