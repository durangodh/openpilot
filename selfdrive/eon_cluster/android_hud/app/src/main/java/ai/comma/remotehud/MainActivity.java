package ai.comma.remotehud;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;

/** Entry point used by E-Mirror auto-launch and TURZX USB attachment. */
public final class MainActivity extends Activity {
  @Override public void onCreate(Bundle state) {
    super.onCreate(state);
    startForegroundService(new Intent(this, HudService.class));
    finish();
  }
}
