package ai.comma.eonlink;

import android.app.*;
import android.content.*;
import android.graphics.Color;
import android.net.Uri;
import android.os.*;
import android.view.*;
import android.widget.*;
import org.json.*;
import java.io.*;

public final class MainActivity extends Activity {
  private final Handler handler = new Handler(Looper.getMainLooper());
  private TextView state, details;
  private boolean showLogs;
  private final Runnable refresh = new Runnable() {
    @Override public void run() {
      Intent battery = registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
      String temperature = battery == null ? "" : "\n배터리 온도 " + battery.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0) / 10.0 + "°C";
      state.setText(LinkService.status + temperature + "\n\n" + LinkService.addresses());
      details.setText(showLogs ? LinkService.logs : summary(LinkService.snapshot));
      handler.postDelayed(this, 1000);
    }
  };
  @Override public void onCreate(Bundle saved) {
    super.onCreate(saved); getWindow().setStatusBarColor(Color.rgb(16, 23, 34));
    ScrollView scroll = new ScrollView(this); LinearLayout layout = new LinearLayout(this);
    layout.setOrientation(LinearLayout.VERTICAL); layout.setPadding(32, 40, 32, 40);
    layout.setBackgroundColor(Color.rgb(16, 23, 34)); scroll.addView(layout); setContentView(scroll);
    TextView title = text("이온링크", 32); layout.addView(title);
    layout.addView(text("이온 전용 N16 연결·성능 시험  0.1.0\nSnapdragon NPU · 기존 이온 모델로 주행", 15));
    state = text("중지됨", 20); state.setPadding(0, 32, 0, 24); layout.addView(state);
    button(layout, "연결 시작", () -> send(null));
    button(layout, "N16 다운로드", () -> send(LinkService.DOWNLOAD));
    button(layout, "N16 준비 / 사용", () -> send(LinkService.PREPARE));
    button(layout, "ONNX 파일 가져오기", () -> {
      Intent picker = new Intent(Intent.ACTION_OPEN_DOCUMENT).setType("*/*").addCategory(Intent.CATEGORY_OPENABLE);
      startActivityForResult(picker, 10);
    });
    button(layout, "연결 중지", () -> stopService(new Intent(this, LinkService.class)));
    button(layout, "진행 상태 / 로그 전환", () -> { showLogs = !showLogs; });
    details = text("", 15); details.setTextIsSelectable(true); details.setPadding(0, 24, 0, 24); layout.addView(details);
    layout.addView(text("1. 같은 핫스팟에 연결하고 기존 젯링크는 종료하세요.\n2. 연결 시작 → N16 다운로드 후 완료되면 N16 준비 / 사용을 누르세요.\n3. 이온웹에서 이온링크를 선택하고 자동 검색 또는 위 주소로 1분 시험을 시작하세요.\n\n화면을 꺼도 알림이 있는 동안 실행됩니다. 별도 앱이므로 모델을 한 번 다시 받아야 합니다. 현재는 차량 제어에 적용되지 않습니다.\n\nJetlink Carrot .46 계산 엔진 재사용 · MIT 및 포함 런타임 라이선스는 앱의 assets/NOTICE.txt 참조.", 14));
    if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission("android.permission.POST_NOTIFICATIONS") != android.content.pm.PackageManager.PERMISSION_GRANTED)
      requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, 2);
  }
  private TextView text(String value, int size) {
    TextView view = new TextView(this); view.setText(value); view.setTextSize(size); view.setTextColor(Color.rgb(225, 235, 245)); return view;
  }
  private void button(LinearLayout parent, String label, Runnable action) {
    Button b = new Button(this); b.setText(label); b.setAllCaps(false); b.setOnClickListener(v -> action.run()); parent.addView(b);
  }
  private void send(String action) { startForegroundService(new Intent(this, LinkService.class).setAction(action)); }
  @Override protected void onResume() { super.onResume(); handler.post(refresh); }
  @Override protected void onPause() { handler.removeCallbacks(refresh); super.onPause(); }
  @Override protected void onActivityResult(int request, int result, Intent data) {
    super.onActivityResult(request, result, data);
    if (request != 10 || result != RESULT_OK || data == null || data.getData() == null) return;
    Uri uri = data.getData();
    LinkService.status = "모델 파일 복사 중";
    new Thread(() -> {
      File copy;
      try { copy = File.createTempFile("n16-import-", ".onnx", getFilesDir()); }
      catch (IOException e) { LinkService.status = "파일 생성 실패"; return; }
      try (InputStream in = getContentResolver().openInputStream(uri); OutputStream out = new FileOutputStream(copy)) {
        if (in == null) throw new IOException("파일을 열 수 없습니다");
        java.security.MessageDigest digest = java.security.MessageDigest.getInstance("SHA-256");
        byte[] buffer = new byte[1024 * 1024]; int n; long total = 0;
        while ((n = in.read(buffer)) != -1) {
          total += n; if (total > 900000000L) throw new IOException("모델 파일이 너무 큽니다"); digest.update(buffer, 0, n); out.write(buffer, 0, n);
        }
        StringBuilder sha = new StringBuilder();
        for (byte value : digest.digest()) sha.append(String.format(java.util.Locale.ROOT, "%02x", value & 255));
        if (total != 814591107L || !sha.toString().equals(Relay.SHA)) throw new IOException("이 앱은 지정된 N16 모델만 가져옵니다");
        startForegroundService(new Intent(this, LinkService.class).setAction(LinkService.IMPORT).putExtra("path", copy.getAbsolutePath()));
      } catch (Exception e) { copy.delete(); LinkService.status = "파일 가져오기 실패: " + e.getMessage(); }
    }, "eon-import").start();
  }
  private String label(String state) {
    switch (state) {
      case "none": return "준비 전";
      case "building": case "preparing": return "NPU용 준비 중";
      case "loading": return "불러오는 중";
      case "ready": case "loaded": return "사용 준비 완료";
      case "downloading": return "다운로드 중";
      case "downloaded": case "prepared": return "다운로드 완료 · N16 준비 / 사용을 누르세요";
      case "failed": return "실패 · 로그를 확인하세요";
      case "not_downloaded": return "다운로드 필요";
      default: return state;
    }
  }
  private String summary(String raw) {
    try {
      JSONObject root = new JSONObject(raw);
      StringBuilder b = new StringBuilder();
      JSONObject engine = root.optJSONObject("engine");
      if (engine != null) {
        b.append("N16 엔진: ").append(label(engine.optString("state"))).append('\n');
        String detail = engine.optString("detail", "");
        if (!detail.isEmpty()) b.append(detail).append('\n');
        if (engine.optString("state").equals("building")) b.append(Math.round(engine.optDouble("frac", 0) * 100)).append("%\n");
      }
      JSONArray models = root.optJSONArray("models");
      if (models != null) for (int i = 0; i < models.length(); i++) {
        JSONObject model = models.optJSONObject(i);
        if (model == null || !Relay.SHA.equals(model.optString("sha256"))) continue;
        JSONObject progress = model.optJSONObject("status");
        if (progress != null) {
          b.append("모델: ").append(label(progress.optString("kind")));
          if (progress.has("frac")) b.append(' ').append(Math.round(progress.optDouble("frac", 0) * 100)).append('%');
          b.append('\n');
          String error = progress.optString("error", ""); if (!error.isEmpty()) b.append(error).append('\n');
        }
      }
      JSONObject disk = root.optJSONObject("disk");
      if (disk != null) b.append(String.format(java.util.Locale.KOREA, "\n남은 저장 공간 %.1f GB\n", disk.optDouble("free_bytes", 0) / 1e9));
      return b.toString();
    } catch (JSONException e) { return "N16 다운로드·준비 상태가 여기에 표시됩니다."; }
  }
}
