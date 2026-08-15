package ai.comma.remotehud;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.graphics.RectF;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.IBinder;
import android.os.SystemClock;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

public final class HudService extends Service {
  static final String EXTRA_RESULT_CODE = "resultCode";
  static final String EXTRA_RESULT_DATA = "resultData";
  private static final String CHANNEL = "remote_hud";
  private static final int WIDTH = 1920;
  private static final int HEIGHT = 462;
  private static final int CAPTURE_W = 960;
  private static final int CAPTURE_H = 540;
  private final AtomicBoolean running = new AtomicBoolean(false);
  private final AtomicReference<JSONObject> state = new AtomicReference<>(new JSONObject());
  private final AtomicReference<Bitmap> mapFrame = new AtomicReference<>();
  private MediaProjection projection;
  private VirtualDisplay virtualDisplay;
  private ImageReader imageReader;
  private TurzxDisplay display;
  private Thread receiverThread;
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
    if (intent == null) { stopSelf(); return START_NOT_STICKY; }
    int code = intent.getIntExtra(EXTRA_RESULT_CODE, 0);
    Intent data = intent.getParcelableExtra(EXTRA_RESULT_DATA);
    if (code == 0 || data == null) { stopSelf(); return START_NOT_STICKY; }
    running.set(true);
    MediaProjectionManager manager = (MediaProjectionManager)getSystemService(Context.MEDIA_PROJECTION_SERVICE);
    projection = manager.getMediaProjection(code, data);
    projection.registerCallback(new MediaProjection.Callback() {
      @Override public void onStop() { stopSelf(); }
    }, null);
    startCapture();
    display = new TurzxDisplay(this);
    receiverThread = new Thread(this::receiveLoop, "hud-telemetry");
    renderThread = new Thread(this::renderLoop, "hud-render");
    receiverThread.start();
    renderThread.start();
    return START_NOT_STICKY;
  }

  private void startCapture() {
    imageReader = ImageReader.newInstance(CAPTURE_W, CAPTURE_H, PixelFormat.RGBA_8888, 2);
    imageReader.setOnImageAvailableListener(reader -> {
      Image image = reader.acquireLatestImage();
      if (image == null) return;
      try {
        Image.Plane plane = image.getPlanes()[0];
        int pixelStride = plane.getPixelStride();
        int rowStride = plane.getRowStride();
        int paddedWidth = CAPTURE_W + (rowStride - pixelStride * CAPTURE_W) / pixelStride;
        Bitmap padded = Bitmap.createBitmap(paddedWidth, CAPTURE_H, Bitmap.Config.ARGB_8888);
        padded.copyPixelsFromBuffer(plane.getBuffer());
        Bitmap frame = Bitmap.createBitmap(padded, 0, 0, CAPTURE_W, CAPTURE_H);
        padded.recycle();
        Bitmap old = mapFrame.getAndSet(frame);
        if (old != null && old != frame) old.recycle();
      } finally { image.close(); }
    }, null);
    virtualDisplay = projection.createVirtualDisplay("EON-HUD-TMAP", CAPTURE_W, CAPTURE_H, getResources().getDisplayMetrics().densityDpi,
        DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, imageReader.getSurface(), null, null);
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
          byte[] ack = "HUD1".getBytes("US-ASCII");
          socket.send(new DatagramPacket(ack, ack.length, packet.getAddress(), packet.getPort()));
        } catch (java.net.SocketTimeoutException ignored) { }
      }
    } catch (Exception ignored) { }
  }

  private void renderLoop() {
    long next = 0;
    while (running.get()) {
      long now = SystemClock.elapsedRealtime();
      if (now < next) { SystemClock.sleep(Math.min(20, next - now)); continue; }
      next = now + 125;
      try {
        if (!display.openOrRequestPermission()) { SystemClock.sleep(500); continue; }
        Bitmap frame = render(state.get(), mapFrame.get());
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
    p.setStyle(Paint.Style.FILL); p.setColor(Color.rgb(12, 17, 24));
    c.drawRoundRect(new RectF(8, 8, panelW - 8, HEIGHT - 8), 22, 22, p);
    text(c, p, s.optString("gear", "--"), 38, 48, 37, Color.WHITE, Paint.Align.LEFT);
    text(c, p, "KM/H", panelW / 2f, 46, 26, Color.LTGRAY, Paint.Align.CENTER);
    int speed = s.optInt("speed", 0);
    text(c, p, Integer.toString(speed), panelW / 2f, 142, 92, Color.WHITE, Paint.Align.CENTER);
    int set = s.optInt("set", 0);
    text(c, p, "SET  " + (set > 0 ? set : "--"), panelW / 2f, 187, 31, enabled ? Color.rgb(0, 220, 120) : Color.LTGRAY, Paint.Align.CENTER);
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
    drawPath(c, p, s.optJSONArray("path"), enabled);
    float carX = panelW / 2f, carY = 374;
    p.setStyle(Paint.Style.FILL); p.setColor(Color.rgb(160, 166, 174));
    c.drawRoundRect(new RectF(carX - 25, carY - 38, carX + 25, carY + 38), 12, 12, p);
    if (s.optBoolean("leftBsd", false)) { p.setColor(Color.RED); c.drawCircle(carX - 66, carY + 7, 12, p); }
    if (s.optBoolean("rightBsd", false)) { p.setColor(Color.RED); c.drawCircle(carX + 66, carY + 7, 12, p); }
    JSONObject lead = s.optJSONObject("lead");
    if (lead != null) {
      p.setColor(Color.LTGRAY); c.drawRoundRect(new RectF(carX - 14, 250, carX + 14, 286), 8, 8, p);
      text(c, p, String.format(Locale.US, "%.1fm", lead.optDouble("d", 0)), carX, 238, 23, Color.WHITE, Paint.Align.CENTER);
    }
    int gap = s.optInt("gap", 0);
    text(c, p, "GAP " + (gap > 0 ? gap : "--"), 34, HEIGHT - 22, 26, Color.LTGRAY, Paint.Align.LEFT);
  }

  private void drawPath(Canvas c, Paint p, JSONArray points, boolean enabled) {
    if (points == null || points.length() < 2) return;
    Path left = new Path(), right = new Path();
    float center = 384, bottom = 340;
    for (int i = 0; i < points.length(); i++) {
      JSONArray pt = points.optJSONArray(i); if (pt == null) continue;
      float x = (float)pt.optDouble(0, 0), y = (float)pt.optDouble(1, 0);
      float depth = Math.min(1f, x / 55f);
      float py = bottom - depth * 145;
      float px = center - y * (7f - depth * 4f);
      float half = 38f - depth * 25f;
      if (i == 0) { left.moveTo(px - half, py); right.moveTo(px + half, py); }
      else { left.lineTo(px - half, py); right.lineTo(px + half, py); }
    }
    p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(7); p.setStrokeCap(Paint.Cap.ROUND);
    p.setColor(enabled ? Color.rgb(0, 150, 255) : Color.rgb(70, 80, 90));
    c.drawPath(left, p); c.drawPath(right, p);
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
    if (virtualDisplay != null) virtualDisplay.release();
    if (imageReader != null) imageReader.close();
    if (projection != null) projection.stop();
    Bitmap map = mapFrame.getAndSet(null); if (map != null) map.recycle();
    super.onDestroy();
  }

  @Override public IBinder onBind(Intent intent) { return null; }
}
