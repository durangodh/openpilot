package ai.comma.remotehud;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Shader;
import android.os.Build;
import android.os.IBinder;
import android.os.SystemClock;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

public final class HudService extends Service {
  private static final String CHANNEL = "remote_hud";
  private static final int WIDTH = 1920;
  private static final int HEIGHT = 462;
  private final AtomicBoolean running = new AtomicBoolean(false);
  private final AtomicReference<JSONObject> state = new AtomicReference<>(new JSONObject());
  private final AtomicReference<Bitmap> mapFrame = new AtomicReference<>();
  private final AtomicReference<InetAddress> eonAddress = new AtomicReference<>();
  private TurzxDisplay display;
  private Thread receiverThread;
  private Thread mapThread;
  private Thread renderThread;

  @Override public void onCreate() {
    super.onCreate();
    NotificationManager nm = (NotificationManager)getSystemService(Context.NOTIFICATION_SERVICE);
    if (Build.VERSION.SDK_INT >= 26) nm.createNotificationChannel(new NotificationChannel(CHANNEL, "EON Remote HUD", NotificationManager.IMPORTANCE_LOW));
    Notification notification = new Notification.Builder(this, CHANNEL).setContentTitle("EON Remote HUD")
        .setContentText("TMAP 화면과 EON 데이터를 외부 HUD로 전송 중").setSmallIcon(android.R.drawable.ic_menu_directions).build();
    startForeground(72, notification);
  }

  @Override public int onStartCommand(Intent intent, int flags, int startId) {
    if (running.get()) return START_NOT_STICKY;
    running.set(true);
    display = new TurzxDisplay(this);
    receiverThread = new Thread(this::receiveLoop, "hud-telemetry");
    mapThread = new Thread(this::mapLoop, "hud-tmap");
    renderThread = new Thread(this::renderLoop, "hud-render");
    receiverThread.start();
    mapThread.start();
    renderThread.start();
    return START_NOT_STICKY;
  }

  private void receiveLoop() {
    try (DatagramSocket socket = new DatagramSocket(7210)) {
      socket.setBroadcast(true);
      socket.setSoTimeout(1000);
      byte[] buffer = new byte[8192];
      while (running.get()) {
        try {
          DatagramPacket packet = new DatagramPacket(buffer, buffer.length);
          socket.receive(packet);
          state.set(new JSONObject(new String(packet.getData(), packet.getOffset(), packet.getLength(), "UTF-8")));
          eonAddress.set(packet.getAddress());
          byte[] ack = "HUD1".getBytes("US-ASCII");
          socket.send(new DatagramPacket(ack, ack.length, packet.getAddress(), packet.getPort()));
        } catch (java.net.SocketTimeoutException ignored) { }
      }
    } catch (Exception ignored) { }
  }

  private void mapLoop() {
    while (running.get()) {
      InetAddress address = eonAddress.get();
      if (address == null) { SystemClock.sleep(500); continue; }
      try (Socket socket = new Socket()) {
        socket.connect(new InetSocketAddress(address, 7211), 2000);
        socket.setSoTimeout(4000);
        DataInputStream input = new DataInputStream(socket.getInputStream());
        byte[] magic = new byte[4];
        while (running.get() && address.equals(eonAddress.get())) {
          input.readFully(magic);
          if (magic[0] != 'M' || magic[1] != 'A' || magic[2] != 'P' || magic[3] != '1') throw new Exception("bad map frame");
          int length = input.readInt();
          if (length <= 4 || length > 2 * 1024 * 1024) throw new Exception("bad map size");
          byte[] jpeg = new byte[length];
          input.readFully(jpeg);
          Bitmap frame = BitmapFactory.decodeByteArray(jpeg, 0, jpeg.length);
          if (frame != null) {
            synchronized (mapFrame) {
              Bitmap old = mapFrame.getAndSet(frame);
              if (old != null && old != frame) old.recycle();
            }
          }
        }
      } catch (Exception ignored) { SystemClock.sleep(500); }
    }
  }

  private void renderLoop() {
    long next = 0;
    while (running.get()) {
      long now = SystemClock.elapsedRealtime();
      if (now < next) { SystemClock.sleep(Math.min(20, next - now)); continue; }
      next = now + 125;
      try {
        if (!display.openOrRequestPermission()) { SystemClock.sleep(500); continue; }
        Bitmap frame;
        synchronized (mapFrame) { frame = render(state.get(), mapFrame.get()); }
        ByteArrayOutputStream bytes = new ByteArrayOutputStream(180000);
        frame.compress(Bitmap.CompressFormat.JPEG, 55, bytes);
        frame.recycle();
        display.sendJpeg(bytes.toByteArray());
      } catch (Exception e) {
        display.close();
        SystemClock.sleep(500);
      }
    }
  }

  private Bitmap render(JSONObject s, Bitmap map) {
    Bitmap out = Bitmap.createBitmap(WIDTH, HEIGHT, Bitmap.Config.RGB_565);
    Canvas c = new Canvas(out);
    Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
    c.drawColor(Color.rgb(5, 8, 12));
    drawDriving(c, p, s);
    drawSystem(c, p, s);
    drawMap(c, p, map);
    return out;
  }

  private void drawDriving(Canvas c, Paint p, JSONObject s) {
    final int panelW = 768;
    boolean enabled = s.optBoolean("enabled", false);
    int save = c.save();
    c.clipRect(8, 8, panelW - 8, HEIGHT - 8);
    drawWorld(c, p, s, enabled, panelW);
    c.restoreToCount(save);

    p.setShader(null); p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(2); p.setColor(Color.rgb(48, 60, 72));
    c.drawRoundRect(new RectF(8, 8, panelW - 8, HEIGHT - 8), 22, 22, p);
    text(c, p, s.optString("gear", "--"), 38, 48, 37, Color.WHITE, Paint.Align.LEFT);
    text(c, p, "KM/H", panelW / 2f, 46, 26, Color.LTGRAY, Paint.Align.CENTER);
    int speed = s.optInt("speed", 0);
    text(c, p, Integer.toString(speed), panelW / 2f, 132, 82, Color.WHITE, Paint.Align.CENTER);
    int set = s.optInt("set", 0);
    text(c, p, "SET  " + (set > 0 ? set : "--"), panelW / 2f, 171, 29, enabled ? Color.rgb(0, 230, 135) : Color.LTGRAY, Paint.Align.CENTER);
    int limit = s.optInt("limit", 0);
    text(c, p, "LIMIT " + (limit > 0 ? limit : "--"), panelW - 32, 48, 28, Color.WHITE, Paint.Align.RIGHT);
    int camera = s.optInt("camera", 0);
    int cameraDist = s.optInt("cameraDist", 0);
    if (camera > 0) {
      p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(5); p.setColor(Color.rgb(235, 74, 74));
      c.drawCircle(panelW - 76, 127, 42, p);
      text(c, p, Integer.toString(camera), panelW - 76, 139, 35, Color.WHITE, Paint.Align.CENTER);
      text(c, p, cameraDist + " m", panelW - 76, 190, 25, Color.rgb(255, 205, 80), Paint.Align.CENTER);
    }
    int gap = s.optInt("gap", 0);
    text(c, p, "GAP " + (gap > 0 ? gap : "--"), 34, HEIGHT - 22, 26, Color.LTGRAY, Paint.Align.LEFT);
  }

  private void drawWorld(Canvas c, Paint p, JSONObject s, boolean enabled, int panelW) {
    final float center = panelW * 0.5f, horizon = 188f, bottom = 456f;
    p.setStyle(Paint.Style.FILL);
    p.setShader(new LinearGradient(0, 8, 0, horizon, Color.rgb(7, 17, 29), Color.rgb(26, 39, 51), Shader.TileMode.CLAMP));
    c.drawRect(8, 8, panelW - 8, horizon, p);
    p.setShader(new LinearGradient(0, horizon, 0, bottom, Color.rgb(39, 45, 51), Color.rgb(10, 13, 17), Shader.TileMode.CLAMP));
    Path road = new Path();
    road.moveTo(center - 92, horizon); road.lineTo(center + 92, horizon);
    road.lineTo(panelW - 26, bottom); road.lineTo(26, bottom); road.close();
    c.drawPath(road, p); p.setShader(null);

    drawModelLines(c, p, s.optJSONArray("edges"), center, horizon, bottom, Color.rgb(255, 78, 62), 4f);
    drawModelLines(c, p, s.optJSONArray("lanes"), center, horizon, bottom, Color.WHITE, 5f);
    drawPathSurface(c, p, s.optJSONArray("path"), enabled, center, horizon, bottom);

    JSONObject lead2 = s.optJSONObject("lead2");
    if (lead2 != null) drawLead(c, p, lead2, center, horizon, bottom, Color.rgb(255, 190, 64), false);
    JSONObject lead = s.optJSONObject("lead");
    if (lead != null) drawLead(c, p, lead, center, horizon, bottom, Color.rgb(255, 76, 66), true);

    drawVehicle3d(c, p, center, 405, 88, 82, Color.rgb(175, 185, 197), true);
    if (s.optBoolean("leftBsd", false)) drawBsd(c, p, center - 82, 405, true);
    if (s.optBoolean("rightBsd", false)) drawBsd(c, p, center + 82, 405, false);
    if (s.optBoolean("leftBlinker", false)) drawTurnArrow(c, p, 60, 250, true);
    if (s.optBoolean("rightBlinker", false)) drawTurnArrow(c, p, panelW - 60, 250, false);
  }

  private float[] project(float longitudinal, float lateral, float center, float horizon, float bottom) {
    float x = Math.max(0f, longitudinal);
    float py = bottom - (bottom - horizon) * x / (x + 13f);
    float lateralScale = 105f / (1f + x / 17f);
    return new float[] {center - lateral * lateralScale, py};
  }

  private void drawModelLines(Canvas c, Paint p, JSONArray lines, float center, float horizon, float bottom, int color, float width) {
    if (lines == null) return;
    for (int lineIndex = 0; lineIndex < lines.length(); lineIndex++) {
      JSONObject line = lines.optJSONObject(lineIndex); if (line == null) continue;
      float confidence = (float)line.optDouble("c", 0);
      if (confidence < 0.12f) continue;
      JSONArray points = line.optJSONArray("p"); if (points == null || points.length() < 2) continue;
      Path path = new Path(); boolean started = false;
      for (int i = 0; i < points.length(); i++) {
        JSONArray point = points.optJSONArray(i); if (point == null) continue;
        float[] screen = project((float)point.optDouble(0), (float)point.optDouble(1), center, horizon, bottom);
        if (!started) { path.moveTo(screen[0], screen[1]); started = true; } else path.lineTo(screen[0], screen[1]);
      }
      p.setShader(null); p.setStyle(Paint.Style.STROKE); p.setStrokeCap(Paint.Cap.ROUND);
      p.setStrokeWidth(width); p.setColor((color & 0x00ffffff) | (((int)(70 + confidence * 185)) << 24));
      c.drawPath(path, p);
    }
  }

  private void drawPathSurface(Canvas c, Paint p, JSONArray points, boolean enabled, float center, float horizon, float bottom) {
    if (points == null || points.length() < 2) return;
    int count = points.length(); float[][] left = new float[count][], right = new float[count][]; int valid = 0;
    for (int i = 0; i < count; i++) {
      JSONArray point = points.optJSONArray(i); if (point == null) continue;
      float x = (float)point.optDouble(0), y = (float)point.optDouble(1);
      left[valid] = project(x, y + 0.72f, center, horizon, bottom);
      right[valid] = project(x, y - 0.72f, center, horizon, bottom); valid++;
    }
    if (valid < 2) return;
    Path surface = new Path(); surface.moveTo(left[0][0], left[0][1]);
    for (int i = 1; i < valid; i++) surface.lineTo(left[i][0], left[i][1]);
    for (int i = valid - 1; i >= 0; i--) surface.lineTo(right[i][0], right[i][1]);
    surface.close();
    int near = enabled ? Color.argb(205, 0, 183, 255) : Color.argb(135, 90, 102, 112);
    int far = enabled ? Color.argb(35, 0, 220, 150) : Color.argb(20, 70, 80, 90);
    p.setStyle(Paint.Style.FILL); p.setShader(new LinearGradient(0, bottom, 0, horizon, near, far, Shader.TileMode.CLAMP));
    c.drawPath(surface, p); p.setShader(null);
    p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(2); p.setColor(enabled ? Color.rgb(94, 225, 255) : Color.rgb(100, 110, 120));
    c.drawPath(surface, p);
  }

  private void drawLead(Canvas c, Paint p, JSONObject lead, float center, float horizon, float bottom, int tint, boolean primary) {
    float distance = (float)lead.optDouble("d", 0), lateral = (float)lead.optDouble("y", 0);
    if (distance <= 0 || distance > 150) return;
    float[] pos = project(distance, lateral, center, horizon, bottom);
    float scale = Math.max(0.24f, 1f / (1f + distance / 18f));
    float w = 76f * scale, h = 82f * scale;
    drawVehicle3d(c, p, pos[0], pos[1] - h * 0.25f, w, h, tint, false);
    if (primary) text(c, p, String.format(Locale.US, "%.0fm", distance), pos[0], pos[1] - h - 8, Math.max(17, 23 * scale), Color.WHITE, Paint.Align.CENTER);
  }

  private void drawVehicle3d(Canvas c, Paint p, float cx, float cy, float w, float h, int tint, boolean ego) {
    float left = cx - w * 0.5f, right = cx + w * 0.5f, top = cy - h * 0.58f, bottom = cy + h * 0.42f;
    Path body = new Path(); body.moveTo(cx - w * 0.34f, top); body.lineTo(cx + w * 0.34f, top);
    body.lineTo(right, bottom - h * 0.12f); body.lineTo(cx + w * 0.42f, bottom);
    body.lineTo(cx - w * 0.42f, bottom); body.lineTo(left, bottom - h * 0.12f); body.close();
    p.setShader(new LinearGradient(left, top, right, bottom, lighten(tint, 45), darken(tint, 55), Shader.TileMode.CLAMP));
    p.setStyle(Paint.Style.FILL); c.drawPath(body, p); p.setShader(null);
    Path glass = new Path(); glass.moveTo(cx - w * 0.25f, top + h * 0.13f); glass.lineTo(cx + w * 0.25f, top + h * 0.13f);
    glass.lineTo(cx + w * 0.34f, cy - h * 0.02f); glass.lineTo(cx - w * 0.34f, cy - h * 0.02f); glass.close();
    p.setColor(Color.rgb(24, 43, 60)); c.drawPath(glass, p);
    p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(Math.max(1.5f, w * 0.025f)); p.setColor(lighten(tint, 65)); c.drawPath(body, p);
    p.setStyle(Paint.Style.FILL); p.setColor(ego ? Color.rgb(255, 72, 65) : Color.rgb(255, 220, 120));
    c.drawOval(new RectF(left + w * 0.12f, bottom - h * 0.16f, left + w * 0.28f, bottom - h * 0.08f), p);
    c.drawOval(new RectF(right - w * 0.28f, bottom - h * 0.16f, right - w * 0.12f, bottom - h * 0.08f), p);
  }

  private void drawBsd(Canvas c, Paint p, float x, float y, boolean left) {
    p.setStyle(Paint.Style.FILL); p.setColor(Color.argb(70, 255, 30, 30)); c.drawCircle(x, y, 27, p);
    p.setColor(Color.RED); c.drawCircle(x, y, 10, p);
    p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(3); c.drawCircle(x, y, 20, p);
  }

  private void drawTurnArrow(Canvas c, Paint p, float x, float y, boolean left) {
    Path arrow = new Path(); float sign = left ? -1f : 1f;
    arrow.moveTo(x + 28 * sign, y - 26); arrow.lineTo(x - 24 * sign, y); arrow.lineTo(x + 28 * sign, y + 26);
    p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(9); p.setStrokeCap(Paint.Cap.ROUND); p.setStrokeJoin(Paint.Join.ROUND);
    p.setColor(Color.rgb(0, 235, 135)); c.drawPath(arrow, p);
  }

  private static int lighten(int color, int amount) {
    return Color.rgb(Math.min(255, Color.red(color) + amount), Math.min(255, Color.green(color) + amount), Math.min(255, Color.blue(color) + amount));
  }

  private static int darken(int color, int amount) {
    return Color.rgb(Math.max(0, Color.red(color) - amount), Math.max(0, Color.green(color) - amount), Math.max(0, Color.blue(color) - amount));
  }

  private void drawSystem(Canvas c, Paint p, JSONObject s) {
    int left = 768, right = 1152;
    p.setStyle(Paint.Style.FILL); p.setColor(Color.rgb(16, 21, 28));
    c.drawRoundRect(new RectF(left + 8, 8, right - 8, HEIGHT - 8), 22, 22, p);
    text(c, p, "SYSTEM", (left + right) / 2f, 55, 30, Color.LTGRAY, Paint.Align.CENTER);
    text(c, p, "CPU", left + 42, 122, 27, Color.GRAY, Paint.Align.LEFT);
    text(c, p, s.optInt("cpu", 0) + "%", right - 42, 122, 44, Color.WHITE, Paint.Align.RIGHT);
    text(c, p, "TEMP", left + 42, 202, 27, Color.GRAY, Paint.Align.LEFT);
    text(c, p, String.format(Locale.US, "%.0f°C", s.optDouble("temp", 0)), right - 42, 202, 44, Color.WHITE, Paint.Align.RIGHT);
    text(c, p, "ACCEL", left + 42, 282, 27, Color.GRAY, Paint.Align.LEFT);
    text(c, p, String.format(Locale.US, "%+.2f", s.optDouble("accel", 0)), right - 42, 282, 39, Color.WHITE, Paint.Align.RIGHT);
    long age = Math.max(0, System.currentTimeMillis() - s.optLong("t", 0));
    text(c, p, age < 1500 ? "EON CONNECTED" : "WAITING FOR EON", (left + right) / 2f, 406, 25,
        age < 1500 ? Color.rgb(0, 220, 120) : Color.rgb(235, 74, 74), Paint.Align.CENTER);
  }

  private void drawMap(Canvas c, Paint p, Bitmap map) {
    Rect dst = new Rect(1152, 0, WIDTH, HEIGHT);
    if (map == null || map.isRecycled()) {
      p.setStyle(Paint.Style.FILL); p.setColor(Color.BLACK); c.drawRect(dst, p);
      text(c, p, "TMAP 화면 대기", 1536, 240, 34, Color.GRAY, Paint.Align.CENTER);
      return;
    }
    float targetRatio = dst.width() / (float)dst.height();
    float sourceRatio = map.getWidth() / (float)map.getHeight();
    Rect src;
    if (sourceRatio > targetRatio) {
      int w = Math.round(map.getHeight() * targetRatio), x = (map.getWidth() - w) / 2;
      src = new Rect(x, 0, x + w, map.getHeight());
    } else {
      int h = Math.round(map.getWidth() / targetRatio), y = (map.getHeight() - h) / 2;
      src = new Rect(0, y, map.getWidth(), y + h);
    }
    c.drawBitmap(map, src, dst, p);
  }

  private static void text(Canvas c, Paint p, String value, float x, float y, float size, int color, Paint.Align align) {
    p.setStyle(Paint.Style.FILL); p.setTypeface(android.graphics.Typeface.create("sans", android.graphics.Typeface.BOLD));
    p.setTextSize(size); p.setTextAlign(align); p.setColor(color); c.drawText(value, x, y, p);
  }

  @Override public void onDestroy() {
    running.set(false);
    if (display != null) display.close();
    synchronized (mapFrame) {
      Bitmap map = mapFrame.getAndSet(null); if (map != null) map.recycle();
    }
    super.onDestroy();
  }

  @Override public IBinder onBind(Intent intent) { return null; }
}
