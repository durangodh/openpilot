package com.naver.map.carrot;

import android.os.SystemClock;
import android.util.Log;
import android.view.View;
import kotlin.jvm.functions.Function0;

/** Makes the navigation Compose microphone discoverable by nMirror's accessibility service. */
public final class CarrotVoiceButton implements View.OnClickListener {
  // Existing HUD13.11 resource: com.nhn.android.nmap:id/btn_speech_recognition.
  // nMirrorOS 20260926_2240 NavigationButtonService.k explicitly searches this ID.
  private static final int SPEECH_BUTTON_ID = 0x7f0b0174;
  private static final String TAG = "CarrotVoiceButton";
  private Function0<?> click;
  private long lastClick = -1L;

  private CarrotVoiceButton(Function0<?> click) { this.click = click; }

  public static void bind(View view, Function0<?> click) {
    if (view == null || click == null) return;
    // This is the Clova component's own ComposeView, not the screen container.
    // Children keep their normal Compose input and accessibility behavior.
    view.setId(SPEECH_BUTTON_ID);
    view.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
    view.setContentDescription("클로바 음성인식");
    Object tag = view.getTag(SPEECH_BUTTON_ID);
    if (tag instanceof CarrotVoiceButton) {
      ((CarrotVoiceButton) tag).click = click;
    } else {
      CarrotVoiceButton adapter = new CarrotVoiceButton(click);
      view.setTag(SPEECH_BUTTON_ID, adapter);
      view.setOnClickListener(adapter);
    }
  }

  @Override public void onClick(View view) {
    if (!view.isAttachedToWindow() || !view.isShown() || !view.isEnabled()) return;
    long now = SystemClock.uptimeMillis();
    if (lastClick >= 0 && now - lastClick < 350L) return;
    lastClick = now;
    // Exact Function0 supplied to Naver's MapButtonKt.E(), including its permission checks.
    try {
      click.invoke();
      Log.i(TAG, "Navigation Clova screen-button callback invoked");
    } catch (RuntimeException error) {
      Log.e(TAG, "Clova screen-button callback failed", error);
    }
  }
}
