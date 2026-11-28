package ai.s20.ui;

import android.app.Activity;
import android.opengl.GLSurfaceView;
import android.os.Bundle;
import android.view.View;
import android.view.WindowManager;
import android.widget.FrameLayout;

public final class MainActivity extends Activity {
  @Override protected void onCreate(Bundle b) {
    super.onCreate(b);
    getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON | WindowManager.LayoutParams.FLAG_FULLSCREEN);
    Geometry geo = new Geometry();
    CameraRenderer cam = new CameraRenderer(geo);
    GLSurfaceView gl = new GLSurfaceView(this);
    gl.setEGLContextClientVersion(2);
    gl.setRenderer(cam);
    gl.setRenderMode(GLSurfaceView.RENDERMODE_CONTINUOUSLY);
    FrameLayout root = new FrameLayout(this);
    root.addView(gl);
    root.addView(new OverlayView(this, geo, cam));
    setContentView(root);
    hideBars(root);
  }

  private static void hideBars(View v) {
    v.setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
        | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
  }
}
