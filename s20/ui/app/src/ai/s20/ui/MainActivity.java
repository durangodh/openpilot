package ai.s20.ui;

import android.app.Activity;
import android.app.KeyguardManager;
import android.content.Intent;
import android.opengl.GLSurfaceView;
import android.os.Bundle;
import android.provider.Settings;
import android.view.View;
import android.view.WindowManager;
import android.widget.FrameLayout;

/**
 * The phone's home screen, like the comma 3: a spinner while openpilot starts, the offroad home screen, and the camera
 * with the openpilot overlay while driving. Long press: Android settings. Tap on the home screen: camera preview.
 */
public final class MainActivity extends Activity {
  private GLSurfaceView gl;
  private boolean camShown = true;

  @Override protected void onCreate(Bundle b) {
    super.onCreate(b);
    getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON | WindowManager.LayoutParams.FLAG_FULLSCREEN);
    // come up over the lock screen after boot, like a car head unit
    setShowWhenLocked(true);
    setTurnScreenOn(true);
    KeyguardManager km = getSystemService(KeyguardManager.class);
    if (km != null) km.requestDismissKeyguard(this, null);

    Geometry geo = new Geometry();
    CameraRenderer cam = new CameraRenderer(geo);
    gl = new GLSurfaceView(this);
    gl.setEGLContextClientVersion(2);
    gl.setRenderer(cam);
    gl.setRenderMode(GLSurfaceView.RENDERMODE_CONTINUOUSLY);
    FrameLayout root = new FrameLayout(this);
    root.addView(gl);
    root.addView(new OverlayView(this, geo, cam, this::showCamera, this::openSettings));
    setContentView(root);
    hideBars(root);
  }

  /** Camera only while driving or previewing: offroad the GL view stops so the GPU is free. */
  private void showCamera(boolean show) {
    runOnUiThread(() -> {
      if (show == camShown) return;
      camShown = show;
      gl.setRenderMode(show ? GLSurfaceView.RENDERMODE_CONTINUOUSLY : GLSurfaceView.RENDERMODE_WHEN_DIRTY);
      gl.setVisibility(show ? View.VISIBLE : View.INVISIBLE);
    });
  }

  private void openSettings() {
    runOnUiThread(() -> startActivity(new Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)));
  }

  @Override protected void onResume() {
    super.onResume();
    hideBars(getWindow().getDecorView());
    gl.onResume();
  }

  @Override protected void onPause() {
    super.onPause();
    gl.onPause();
  }

  @Override public void onBackPressed() { /* home screen: nothing to go back to */ }

  private static void hideBars(View v) {
    v.setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
        | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
  }
}
