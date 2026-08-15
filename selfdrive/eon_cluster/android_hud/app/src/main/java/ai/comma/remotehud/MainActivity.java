package ai.comma.remotehud;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.media.projection.MediaProjectionManager;
import android.os.Bundle;
import android.os.Build;
import android.Manifest;
import android.content.pm.PackageManager;
import android.view.Gravity;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

public final class MainActivity extends Activity {
  private static final int CAPTURE_REQUEST = 9001;

  @Override public void onCreate(Bundle state) {
    super.onCreate(state);
    if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
      requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 9002);
    }
    LinearLayout layout = new LinearLayout(this);
    layout.setOrientation(LinearLayout.VERTICAL);
    layout.setGravity(Gravity.CENTER);
    layout.setPadding(48, 48, 48, 48);
    TextView help = new TextView(this);
    help.setText("1. 갤럭시와 EON을 같은 핫스팟에 연결\n2. TURZX 1CBE:0092를 USB OTG로 연결\n3. 시작 후 화면 캡처와 USB 권한 허용\n4. TMAP을 전면에 표시");
    help.setTextSize(18);
    Button start = new Button(this);
    start.setText("외부 HUD 시작");
    start.setOnClickListener(v -> {
      MediaProjectionManager manager = (MediaProjectionManager)getSystemService(Context.MEDIA_PROJECTION_SERVICE);
      startActivityForResult(manager.createScreenCaptureIntent(), CAPTURE_REQUEST);
    });
    Button stop = new Button(this);
    stop.setText("중지");
    stop.setOnClickListener(v -> stopService(new Intent(this, HudService.class)));
    layout.addView(help);
    layout.addView(start);
    layout.addView(stop);
    setContentView(layout);
  }

  @Override protected void onActivityResult(int request, int result, Intent data) {
    super.onActivityResult(request, result, data);
    if (request == CAPTURE_REQUEST && result == RESULT_OK && data != null) {
      Intent service = new Intent(this, HudService.class);
      service.putExtra(HudService.EXTRA_RESULT_CODE, result);
      service.putExtra(HudService.EXTRA_RESULT_DATA, data);
      startForegroundService(service);
      moveTaskToBack(true);
    }
  }
}
