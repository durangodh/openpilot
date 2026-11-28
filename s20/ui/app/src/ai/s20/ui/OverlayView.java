package ai.s20.ui;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.view.GestureDetector;
import android.view.MotionEvent;
import android.view.View;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.function.Consumer;

/**
 * Everything drawn over the camera, from /data/s20pilot/share/ui.json (written by s20uid in the chroot):
 *  - starting: the comma spinner while openpilot is not running yet (no fresh ui.json)
 *  - offroad: the home screen like the comma 3 (status cards, offroad alerts, version), camera hidden
 *  - onroad: path, lanes, edges, lead, speeds, alerts, status border over the camera
 */
final class OverlayView extends View {
  static final File UI = new File("/data/s20pilot/share/ui.json");
  static final int BG = Color.rgb(0, 0, 0), PANEL = Color.rgb(41, 41, 41), GOOD = Color.rgb(23, 134, 68),
      WARN = Color.rgb(218, 202, 37), BAD = Color.rgb(201, 34, 49), ORANGE = Color.rgb(218, 111, 37), GREY = Color.rgb(160, 160, 160);
  private final Geometry geo;
  private final CameraRenderer cam;
  private final Consumer<Boolean> showCamera;
  private final Runnable openSettings;
  private final GestureDetector gestures;
  private volatile JSONObject ui;
  private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG), text = new Paint(Paint.ANTI_ALIAS_FLAG),
      box = new Paint(Paint.ANTI_ALIAS_FLAG), bmp = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);
  private final Path path = new Path();
  private Bitmap comma, track;
  private final long t0 = System.currentTimeMillis();
  private boolean preview;
  private final SimpleDateFormat clock = new SimpleDateFormat("M월 d일 (E)  HH:mm", Locale.KOREAN);

  OverlayView(Context c, Geometry geo, CameraRenderer cam, Consumer<Boolean> showCamera, Runnable openSettings) {
    super(c);
    this.geo = geo; this.cam = cam; this.showCamera = showCamera; this.openSettings = openSettings;
    text.setTypeface(Typeface.DEFAULT_BOLD);
    try {
      comma = BitmapFactory.decodeStream(c.getAssets().open("spinner_comma.png"));
      track = BitmapFactory.decodeStream(c.getAssets().open("spinner_track.png"));
    } catch (Exception ignored) {}
    gestures = new GestureDetector(c, new GestureDetector.SimpleOnGestureListener() {
      @Override public boolean onDown(MotionEvent e) { return true; }
      @Override public boolean onSingleTapUp(MotionEvent e) { preview = !preview; invalidate(); return true; }
      @Override public void onLongPress(MotionEvent e) { openSettings.run(); }
    });
    Thread t = new Thread(() -> {
      long lastMod = 0;
      while (true) {
        try {
          long m = UI.lastModified();
          if (m != lastMod) {
            ui = new JSONObject(new String(Files.readAllBytes(UI.toPath()), StandardCharsets.UTF_8));
            lastMod = m;
          }
        } catch (Exception ignored) {}
        postInvalidate();
        try { Thread.sleep(50); } catch (InterruptedException e) { return; }
      }
    }, "s20ui-poll");
    t.setDaemon(true);
    t.start();
  }

  @Override public boolean onTouchEvent(MotionEvent e) { return gestures.onTouchEvent(e); }

  private boolean polygon(JSONArray pts) {
    path.reset();
    if (pts == null || pts.length() < 3) return false;
    for (int i = 0; i < pts.length(); i++) {
      JSONArray p = pts.optJSONArray(i);
      float x = geo.x((float) p.optDouble(0)), y = geo.y((float) p.optDouble(1));
      if (i == 0) path.moveTo(x, y); else path.lineTo(x, y);
    }
    path.close();
    return true;
  }

  @Override protected void onDraw(Canvas c) {
    int W = getWidth(), H = getHeight();
    JSONObject u = ui;
    boolean fresh = u != null && System.currentTimeMillis() / 1000.0 - u.optDouble("t") < 3.0;
    boolean onroad = fresh && u.optBoolean("onroad", true);
    showCamera.accept(onroad || preview);
    if (!fresh && !preview) { spinner(c, W, H); return; }
    if (!onroad && !preview) { home(c, W, H, u); return; }
    if (!fresh) { centered(c, "카메라 미리보기 (openpilot 꺼짐)", W / 2f, 80, 40, Color.WHITE); fps(c, W); return; }
    onroad(c, W, H, u, onroad);
  }

  /** comma 3 style spinner: the comma with a turning ring, shown until openpilot (s20uid) is up */
  private void spinner(Canvas c, int W, int H) {
    c.drawColor(BG);
    float s = Math.min(W, H) * 0.42f, cx = W / 2f, cy = H / 2f - H * 0.05f;
    RectF r = new RectF(cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2);
    if (comma != null) c.drawBitmap(comma, null, r, bmp);
    if (track != null) {
      c.save();
      c.rotate((System.currentTimeMillis() - t0) * 0.36f, cx, cy);  // one turn per second
      c.drawBitmap(track, null, r, bmp);
      c.restore();
    }
    long sec = (System.currentTimeMillis() - t0) / 1000;
    centered(c, "openpilot 시작 중", cx, r.bottom + 90, 46, Color.WHITE);
    if (sec > 90) centered(c, "오래 걸리면: 로그 /data/s20pilot/share/log  ·  길게 누르기: 설정", cx, r.bottom + 150, 30, GREY);
  }

  /** offroad home screen like the comma 3: sidebar with status cards, the main panel with alerts and the device info */
  private void home(Canvas c, int W, int H, JSONObject u) {
    c.drawColor(BG);
    float sw = Math.max(300, W * 0.16f), pad = 30;
    // sidebar
    box.setStyle(Paint.Style.FILL); box.setColor(Color.rgb(57, 57, 57));
    c.drawRect(0, 0, sw, H, box);
    if (comma != null) c.drawBitmap(comma, null, new RectF(sw / 2 - 70, 40, sw / 2 + 70, 180), bmp);
    String thermal = u.optString("thermal");
    double temp = u.optDouble("cpu_temp");
    int tc = thermal.equals("green") ? GOOD : thermal.equals("yellow") ? WARN : thermal.isEmpty() ? GREY : BAD;
    String tv = thermal.equals("green") ? "좋음" : thermal.equals("yellow") ? "보통" : thermal.isEmpty() ? "–" : "높음";
    if (temp > 0) tv += String.format(Locale.US, "  %.0f°C", temp);
    String panda = u.optString("panda");
    boolean ign = u.optBoolean("ignition");
    boolean demo = u.optString("mode").equals("demo");
    float y = 240, ch = (H - y - 40) / 3f - 24;
    card(c, pad, y, sw - pad, y + ch, "온도", tv, tc); y += ch + 24;
    card(c, pad, y, sw - pad, y + ch, "판다", panda.isEmpty() ? (demo ? "데모 모드" : "연결 안됨") : (ign ? "시동 ON" : "연결됨"),
        panda.isEmpty() ? (demo ? WARN : BAD) : GOOD); y += ch + 24;
    boolean camOk = camRingFresh();
    card(c, pad, y, sw - pad, y + ch, "카메라", camOk ? "정상" : "꺼짐", camOk ? GOOD : BAD);

    // main panel
    float x0 = sw + 50, x1 = W - 50;
    text.setTextAlign(Paint.Align.LEFT); text.setTextSize(64); text.setColor(Color.WHITE);
    c.drawText("openpilot", x0, 110, text);
    text.setTextAlign(Paint.Align.RIGHT); text.setTextSize(40); text.setColor(GREY);
    c.drawText(clock.format(new Date()), x1, 100, text);

    // status line under the header
    String status = demo ? "데모 모드 — 판다 없이 모델만 실행 중" : panda.isEmpty() ? "판다를 연결하세요 (OTG)"
        : ign ? "주행 준비 중..." : "준비 완료 — 차량 시동을 켜면 시작합니다";
    int sc = demo || panda.isEmpty() ? ORANGE : GOOD;
    box.setColor(PANEL); RectF sr = new RectF(x0, 150, x1, 260);
    c.drawRoundRect(sr, 24, 24, box);
    box.setColor(sc); c.drawRoundRect(new RectF(x0, 150, x0 + 16, 260), 8, 8, box);
    text.setTextAlign(Paint.Align.LEFT); text.setTextSize(46); text.setColor(Color.WHITE);
    c.drawText(status, x0 + 50, 222, text);

    // offroad alerts (Offroad_* params)
    JSONArray al = u.optJSONArray("alerts");
    float ay = 290;
    int n = al == null ? 0 : al.length();
    for (int i = 0; i < n && ay < H - 220; i++) {
      JSONObject a = al.optJSONObject(i);
      int col = a.optInt("severity") > 0 ? BAD : Color.rgb(70, 70, 70);
      float bh = textBlock(null, a.optString("text"), x0 + 40, ay + 60, x1 - x0 - 80, 36) + 50;
      box.setColor(col); c.drawRoundRect(new RectF(x0, ay, x1, ay + bh), 20, 20, box);
      textBlock(c, a.optString("text"), x0 + 40, ay + 60, x1 - x0 - 80, 36);
      ay += bh + 20;
    }
    if (n == 0) {
      text.setTextAlign(Paint.Align.LEFT); text.setTextSize(36); text.setColor(GREY);
      c.drawText("알림 없음", x0 + 10, ay + 60, text);
    }

    // device info at the bottom
    String ver = u.optString("version"), br = u.optString("branch"), cm = u.optString("commit");
    String info = String.format(Locale.US, "openpilot %s  %s %s   ·   저장공간 %d%% 남음   ·   메모리 %d%%",
        ver.isEmpty() ? "-" : ver, br, cm, u.optInt("free_space"), u.optInt("mem"));
    text.setTextAlign(Paint.Align.LEFT); text.setTextSize(30); text.setColor(GREY);
    c.drawText(info, x0, H - 90, text);
    c.drawText("탭: 카메라 미리보기   ·   길게 누르기: 안드로이드 설정", x0, H - 40, text);
  }

  private java.nio.MappedByteBuffer ringHdr;

  /** the camera bridge (s20cam) wrote a frame in the last 2 s: read from the ring header, works with the GL view off */
  private boolean camRingFresh() {
    if (cam.fps > 1) return true;
    try {
      if (ringHdr == null) {
        try (java.io.RandomAccessFile f = new java.io.RandomAccessFile(CameraRenderer.RING, "r")) {
          ringHdr = f.getChannel().map(java.nio.channels.FileChannel.MapMode.READ_ONLY, 0, 4096);
          ringHdr.order(java.nio.ByteOrder.LITTLE_ENDIAN);
        }
      }
      long id = ringHdr.getLong(24) - 1;
      if (id < 0) return false;
      int slots = Math.max(1, ringHdr.getInt(20));
      long written = ringHdr.getLong(64 + 32 * (int) (id % slots) + 24);
      return android.os.SystemClock.elapsedRealtimeNanos() - written < 2_000_000_000L;
    } catch (Exception e) {
      ringHdr = null;  // not there yet, or recreated by a new s20cam: map again next time
      return false;
    }
  }

  private void card(Canvas c, float l, float t, float r, float b, String title, String value, int color) {
    box.setStyle(Paint.Style.FILL); box.setColor(PANEL);
    c.drawRoundRect(new RectF(l, t, r, b), 20, 20, box);
    box.setColor(color); c.drawRoundRect(new RectF(l, t, l + 14, b), 7, 7, box);
    text.setTextAlign(Paint.Align.LEFT);
    text.setTextSize(30); text.setColor(GREY); c.drawText(title, l + 40, t + (b - t) * 0.42f, text);
    text.setTextSize(38); text.setColor(Color.WHITE); c.drawText(value, l + 40, t + (b - t) * 0.78f, text);
  }

  /** draws word-wrapped text (c == null: only measures); returns the height used */
  private float textBlock(Canvas c, String s, float x, float y, float maxW, float size) {
    text.setTextAlign(Paint.Align.LEFT); text.setTextSize(size); text.setColor(Color.WHITE);
    float lh = size * 1.3f, used = 0;
    int start = 0;
    while (start < s.length()) {
      int cnt = text.breakText(s, start, s.length(), true, maxW, null);
      if (cnt <= 0) break;
      int end = start + cnt;
      if (end < s.length()) { int sp = s.lastIndexOf(' ', end); if (sp > start) end = sp + 1; }
      if (c != null) c.drawText(s.substring(start, end).trim(), x, y + used, text);
      used += lh; start = end;
    }
    return used;
  }

  private void onroad(Canvas c, int W, int H, JSONObject u, boolean onroad) {
    boolean active = u.optBoolean("active"), enabled = u.optBoolean("enabled");
    String alertStatus = u.optString("alert_status");

    // status border like openpilot: green engaged, grey disengaged, orange/red alerts
    int border = alertStatus.equals("critical") ? BAD : alertStatus.equals("userPrompt") ? ORANGE
        : enabled ? GOOD : Color.rgb(23, 51, 73);
    box.setStyle(Paint.Style.STROKE); box.setStrokeWidth(24); box.setColor(border);
    c.drawRect(12, 12, W - 12, H - 12, box);
    box.setStyle(Paint.Style.FILL);
    if (!onroad) centered(c, "카메라 미리보기 (탭: 돌아가기)", W / 2f, H - 120, 40, Color.WHITE);

    // road edges, lane lines, path
    JSONArray edges = u.optJSONArray("edges");
    for (int i = 0; edges != null && i < edges.length(); i++) {
      JSONObject e = edges.optJSONObject(i);
      float a = (float) Math.max(0, Math.min(1, 1.0 - e.optDouble("std")));
      fill.setShader(null); fill.setStyle(Paint.Style.FILL); fill.setColor(Color.argb((int) (255 * a), 255, 0, 0));
      if (polygon(e.optJSONArray("poly"))) c.drawPath(path, fill);
    }
    JSONArray lanes = u.optJSONArray("lanes");
    for (int i = 0; lanes != null && i < lanes.length(); i++) {
      JSONObject l = lanes.optJSONObject(i);
      fill.setShader(null); fill.setColor(Color.argb((int) (255 * Math.min(1, l.optDouble("p"))), 255, 255, 255));
      if (polygon(l.optJSONArray("poly"))) c.drawPath(path, fill);
    }
    if (polygon(u.optJSONArray("path"))) {
      RectF b = new RectF(); path.computeBounds(b, true);
      int c0 = active ? Color.argb(110, 23, 211, 102) : Color.argb(110, 255, 255, 255);
      fill.setShader(new LinearGradient(0, b.bottom, 0, b.top, c0, Color.argb(0, Color.red(c0), Color.green(c0), Color.blue(c0)), Shader.TileMode.CLAMP));
      c.drawPath(path, fill);
      fill.setShader(null);
    }
    JSONObject lead = u.optJSONObject("lead");
    if (lead != null) {
      float x = geo.x(lead.optInt("x")), y = geo.y(lead.optInt("y")), s = 40;
      path.reset(); path.moveTo(x, y - s); path.lineTo(x + s * 1.2f, y + s * 0.6f); path.lineTo(x - s * 1.2f, y + s * 0.6f); path.close();
      fill.setColor(Color.rgb(201, 34, 49)); c.drawPath(path, fill);
      centered(c, String.format("%.0fm", lead.optDouble("d")), x, y + s * 2f, 34, Color.WHITE);
    }

    // speeds: current (km/h) big in the middle, set speed box at top left
    centered(c, String.format("%.0f", u.optDouble("v_ego") * 3.6), W / 2f, 170, 150, Color.WHITE);
    centered(c, "km/h", W / 2f, 225, 44, Color.argb(200, 255, 255, 255));
    double vc = u.optDouble("v_cruise");
    box.setStyle(Paint.Style.FILL); box.setColor(Color.argb(160, 0, 0, 0));
    c.drawRoundRect(60, 50, 280, 250, 30, 30, box);
    centered(c, "MAX", 170, 105, 40, Color.rgb(160, 160, 160));
    centered(c, vc > 0 && vc < 255 ? String.format("%.0f", vc) : "–", 170, 210, 90, Color.WHITE);

    // alert
    JSONArray al = u.optJSONArray("alert");
    String a1 = al != null ? al.optString(0) : "", a2 = al != null ? al.optString(1) : "";
    if (!a1.isEmpty()) {
      box.setColor(Color.argb(200, Color.red(border), Color.green(border), Color.blue(border)));
      c.drawRoundRect(W * 0.15f, H - 230, W * 0.85f, H - 50, 30, 30, box);
      centered(c, a1, W / 2f, H - 150, 56, Color.WHITE);
      if (!a2.isEmpty()) centered(c, a2, W / 2f, H - 85, 40, Color.WHITE);
    }

    // status line
    String st = String.format(Locale.US, "%s  calib:%s  model:%s  cam %.0f fps  CPU %.0f°C", u.optString("state"), u.optString("calib"),
        u.optBoolean("model_ok") ? "ok" : "–", cam.fps, u.optDouble("cpu_temp"));
    text.setTextSize(28); text.setColor(Color.argb(200, 255, 255, 255)); text.setTextAlign(Paint.Align.LEFT);
    c.drawText(st, 40, H - 24, text);
  }

  private void fps(Canvas c, int W) {
    text.setTextSize(28); text.setColor(Color.GRAY); text.setTextAlign(Paint.Align.RIGHT);
    c.drawText(String.format("cam %.0f fps", cam.fps), W - 40, 60, text);
  }

  private void centered(Canvas c, String s, float x, float y, float size, int color) {
    text.setTextSize(size); text.setColor(color); text.setTextAlign(Paint.Align.CENTER);
    text.setShadowLayer(6, 0, 0, Color.BLACK);
    c.drawText(s, x, y, text);
    text.clearShadowLayer();
  }
}
