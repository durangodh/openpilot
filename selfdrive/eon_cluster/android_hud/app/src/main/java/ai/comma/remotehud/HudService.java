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
import java.util.Arrays;
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
  private final GeometryStabilizer geometry = new GeometryStabilizer();
  private TurzxDisplay display;
  private Thread receiverThread;
  private Thread mapThread;
  private Thread renderThread;
  private Bitmap egoCar;
  private Bitmap otherCar;

  @Override public void onCreate() {
    super.onCreate();
    egoCar = BitmapFactory.decodeResource(getResources(), R.drawable.hud_ego_car);
    otherCar = BitmapFactory.decodeResource(getResources(), R.drawable.hud_other_car);
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

  private static final class StableScene {
    final JSONArray path;
    final JSONArray lanes;
    final JSONArray edges;

    StableScene(JSONArray path, JSONArray lanes, JSONArray edges) {
      this.path = path;
      this.lanes = lanes;
      this.edges = edges;
    }
  }

  private static final class GeometryStabilizer {
    private static final float[] SAMPLE_X = {0f, 3f, 6f, 10f, 15f, 22f, 30f, 42f, 58f, 78f, 105f};
    private static final float EMA_ALPHA = 0.28f;
    private static final long HOLD_MS = 500;
    private static final float LANE_CONFIDENCE = 0.25f;
    private static final float EDGE_CONFIDENCE = 0.25f;
    private static final float MIN_ROAD_WIDTH_M = 4.5f;
    private static final float MAX_ROAD_WIDTH_M = 18.0f;
    private final float[][] lanes = new float[4][SAMPLE_X.length];
    private final float[][] edges = new float[2][SAMPLE_X.length];
    private final float[] path = new float[SAMPLE_X.length];
    private final boolean[] laneActive = new boolean[4];
    private final boolean[] edgeActive = new boolean[2];
    private final long[] laneLastValid = new long[4];
    private final long[] edgeLastValid = new long[2];
    private boolean pathActive = false;
    private long pathLastValid = 0;

    StableScene update(JSONObject state, long now) {
      updatePath(state.optJSONArray("path"), now);
      updateLines(state.optJSONArray("lanes"), lanes, laneActive, laneLastValid, LANE_CONFIDENCE, now);
      updateLines(state.optJSONArray("edges"), edges, edgeActive, edgeLastValid, EDGE_CONFIDENCE, now);
      constrainRoadAndLanes();
      return new StableScene(toPath(), toLines(lanes, laneActive), toLines(edges, edgeActive));
    }

    private void updatePath(JSONArray points, long now) {
      float[] sampled = sample(points);
      if (sampled != null) {
        blend(path, sampled, pathActive);
        pathActive = true;
        pathLastValid = now;
      } else if (now - pathLastValid > HOLD_MS) {
        pathActive = false;
      }
    }

    private void updateLines(JSONArray source, float[][] destination, boolean[] active, long[] lastValid,
                             float minimumConfidence, long now) {
      for (int index = 0; index < destination.length; index++) {
        JSONObject line = source == null ? null : source.optJSONObject(index);
        float confidence = line == null ? 0f : (float)line.optDouble("c", 0);
        float[] sampled = line == null || confidence < minimumConfidence ? null : sample(line.optJSONArray("p"));
        if (sampled != null) {
          blend(destination[index], sampled, active[index]);
          active[index] = true;
          lastValid[index] = now;
        } else if (now - lastValid[index] > HOLD_MS) {
          active[index] = false;
        }
      }
    }

    private static void blend(float[] stable, float[] incoming, boolean initialized) {
      if (!initialized) {
        System.arraycopy(incoming, 0, stable, 0, stable.length);
        return;
      }
      for (int i = 0; i < stable.length; i++) stable[i] += (incoming[i] - stable[i]) * EMA_ALPHA;
    }

    private static float[] sample(JSONArray points) {
      if (points == null || points.length() < 2) return null;
      float[] result = new float[SAMPLE_X.length];
      for (int sampleIndex = 0; sampleIndex < SAMPLE_X.length; sampleIndex++) {
        float target = SAMPLE_X[sampleIndex];
        JSONArray first = points.optJSONArray(0);
        JSONArray last = points.optJSONArray(points.length() - 1);
        if (first == null || last == null) return null;
        float y = (float)first.optDouble(1, Double.NaN);
        boolean found = target <= first.optDouble(0, 0);
        for (int pointIndex = 0; !found && pointIndex + 1 < points.length(); pointIndex++) {
          JSONArray a = points.optJSONArray(pointIndex), b = points.optJSONArray(pointIndex + 1);
          if (a == null || b == null) continue;
          float x0 = (float)a.optDouble(0), x1 = (float)b.optDouble(0);
          if (target <= x1 || pointIndex + 2 == points.length()) {
            float denominator = x1 - x0;
            float ratio = Math.abs(denominator) < 0.001f ? 0f : Math.max(0f, Math.min(1f, (target - x0) / denominator));
            y = (float)(a.optDouble(1) + (b.optDouble(1) - a.optDouble(1)) * ratio);
            found = true;
          }
        }
        if (!Float.isFinite(y)) return null;
        result[sampleIndex] = y;
      }
      return result;
    }

    private void constrainRoadAndLanes() {
      if (edgeActive[0] && edgeActive[1]) {
        float previousWidth = 0f;
        for (int sampleIndex = 0; sampleIndex < SAMPLE_X.length; sampleIndex++) {
          float low = Math.min(edges[0][sampleIndex], edges[1][sampleIndex]);
          float high = Math.max(edges[0][sampleIndex], edges[1][sampleIndex]);
          float center = (low + high) * 0.5f;
          float width = Math.max(MIN_ROAD_WIDTH_M, Math.min(MAX_ROAD_WIDTH_M, high - low));
          if (sampleIndex > 0) width = Math.max(previousWidth - 2f, Math.min(previousWidth + 2f, width));
          previousWidth = width;
          edges[0][sampleIndex] = center - width * 0.5f;
          edges[1][sampleIndex] = center + width * 0.5f;

          int[] ids = new int[4]; float[] values = new float[4]; int count = 0;
          for (int laneIndex = 0; laneIndex < lanes.length; laneIndex++) {
            if (!laneActive[laneIndex]) continue;
            ids[count] = laneIndex;
            values[count] = Math.max(edges[0][sampleIndex] + 0.20f,
                Math.min(edges[1][sampleIndex] - 0.20f, lanes[laneIndex][sampleIndex]));
            count++;
          }
          for (int left = 0; left < count; left++) {
            for (int right = left + 1; right < count; right++) {
              if (values[right] < values[left]) {
                float value = values[left]; values[left] = values[right]; values[right] = value;
              }
            }
          }
          for (int i = 1; i < count; i++) values[i] = Math.max(values[i], values[i - 1] + 0.30f);
          if (count > 0 && values[count - 1] > edges[1][sampleIndex] - 0.20f) {
            float shift = values[count - 1] - (edges[1][sampleIndex] - 0.20f);
            for (int i = 0; i < count; i++) values[i] -= shift;
          }
          for (int i = 0; i < count; i++) lanes[ids[i]][sampleIndex] = values[i];
          if (pathActive) path[sampleIndex] = Math.max(edges[0][sampleIndex] + 0.45f,
              Math.min(edges[1][sampleIndex] - 0.45f, path[sampleIndex]));
        }
      }
    }

    private JSONArray toPath() {
      JSONArray result = new JSONArray();
      if (!pathActive) return result;
      for (int i = 0; i < SAMPLE_X.length; i++) result.put(new JSONArray(Arrays.asList(SAMPLE_X[i], path[i])));
      return result;
    }

    private static JSONArray toLines(float[][] values, boolean[] active) {
      JSONArray result = new JSONArray();
      for (int lineIndex = 0; lineIndex < values.length; lineIndex++) {
        if (!active[lineIndex]) continue;
        JSONArray points = new JSONArray();
        for (int sampleIndex = 0; sampleIndex < SAMPLE_X.length; sampleIndex++) {
          points.put(new JSONArray(Arrays.asList(SAMPLE_X[sampleIndex], values[lineIndex][sampleIndex])));
        }
        JSONObject line = new JSONObject();
        try { line.put("c", 1.0); line.put("p", points); } catch (Exception ignored) { continue; }
        result.put(line);
      }
      return result;
    }
  }

  private void drawWorld(Canvas c, Paint p, JSONObject s, boolean enabled, int panelW) {
    final float center = panelW * 0.5f, horizon = 188f, bottom = 456f;
    StableScene stable = geometry.update(s, SystemClock.elapsedRealtime());
    p.setStyle(Paint.Style.FILL);
    p.setShader(new LinearGradient(0, 8, 0, horizon, Color.rgb(7, 17, 29), Color.rgb(26, 39, 51), Shader.TileMode.CLAMP));
    c.drawRect(8, 8, panelW - 8, horizon, p);
    p.setShader(new LinearGradient(0, horizon, 0, bottom, Color.rgb(39, 45, 51), Color.rgb(10, 13, 17), Shader.TileMode.CLAMP));
    Path road = roadSurface(stable.edges, center, horizon, bottom, panelW);
    c.drawPath(road, p); p.setShader(null);

    drawModelLines(c, p, stable.edges, center, horizon, bottom, Color.rgb(255, 78, 62), false);
    drawModelLines(c, p, stable.lanes, center, horizon, bottom, Color.WHITE, true);
    drawPathSurface(c, p, stable.path, enabled, center, horizon, bottom);

    JSONArray modelPath = stable.path;
    JSONObject lead2 = s.optJSONObject("lead2");
    if (lead2 != null) drawLead(c, p, lead2, modelPath, center, horizon, bottom, false);
    JSONObject lead = s.optJSONObject("lead");
    if (lead != null) drawLead(c, p, lead, modelPath, center, horizon, bottom, true);

    drawVehicleSprite(c, p, egoCar, center, 407, 108, pathYaw(modelPath, 5f, center, horizon, bottom), 255);
    // The former dot/triangle BSD markers are intentionally gone.  A detected
    // rear-quarter vehicle is represented by the vehicle sprite itself.
    if (s.optBoolean("leftBsd", false)) drawBsdVehicle(c, p, center - 102, 414, true);
    if (s.optBoolean("rightBsd", false)) drawBsdVehicle(c, p, center + 102, 414, false);
    if (s.optBoolean("leftBlinker", false)) drawTurnArrow(c, p, 60, 250, true);
    if (s.optBoolean("rightBlinker", false)) drawTurnArrow(c, p, panelW - 60, 250, false);
  }

  private float[] project(float longitudinal, float lateral, float center, float horizon, float bottom) {
    float x = Math.max(0f, longitudinal);
    float py = bottom - (bottom - horizon) * x / (x + 13f);
    float lateralScale = 105f / (1f + x / 17f);
    return new float[] {center - lateral * lateralScale, py};
  }

  private Path roadSurface(JSONArray edges, float center, float horizon, float bottom, int panelW) {
    if (edges != null && edges.length() >= 2) {
      JSONObject first = edges.optJSONObject(0), second = edges.optJSONObject(1);
      JSONArray a = first == null ? null : first.optJSONArray("p");
      JSONArray b = second == null ? null : second.optJSONArray("p");
      if (a != null && b != null && a.length() >= 2 && b.length() >= 2) {
        Path path = new Path(); boolean started = false;
        for (int i = 0; i < a.length(); i++) {
          JSONArray point = a.optJSONArray(i); if (point == null) continue;
          float[] screen = project((float)point.optDouble(0), (float)point.optDouble(1), center, horizon, bottom);
          if (!started) { path.moveTo(screen[0], screen[1]); started = true; } else path.lineTo(screen[0], screen[1]);
        }
        for (int i = b.length() - 1; i >= 0; i--) {
          JSONArray point = b.optJSONArray(i); if (point == null) continue;
          float[] screen = project((float)point.optDouble(0), (float)point.optDouble(1), center, horizon, bottom);
          path.lineTo(screen[0], screen[1]);
        }
        path.close();
        return path;
      }
    }
    Path fallback = new Path();
    fallback.moveTo(center - 92, horizon); fallback.lineTo(center + 92, horizon);
    fallback.lineTo(panelW - 26, bottom); fallback.lineTo(26, bottom); fallback.close();
    return fallback;
  }

  private void drawModelLines(Canvas c, Paint p, JSONArray lines, float center, float horizon, float bottom, int color, boolean dashed) {
    if (lines == null) return;
    for (int lineIndex = 0; lineIndex < lines.length(); lineIndex++) {
      JSONObject line = lines.optJSONObject(lineIndex); if (line == null) continue;
      float confidence = (float)line.optDouble("c", 0);
      if (confidence < 0.12f) continue;
      JSONArray points = line.optJSONArray("p"); if (points == null || points.length() < 2) continue;
      drawPerspectiveLine(c, p, points, confidence, center, horizon, bottom, color, dashed);
    }
  }

  private void drawPerspectiveLine(Canvas c, Paint p, JSONArray points, float confidence, float center, float horizon,
                                   float bottom, int color, boolean dashed) {
    int alpha = (int)(70 + confidence * 185);
    p.setShader(null); p.setStyle(Paint.Style.STROKE); p.setStrokeCap(Paint.Cap.ROUND);
    p.setColor((color & 0x00ffffff) | (alpha << 24));
    for (int i = 0; i + 1 < points.length(); i++) {
      JSONArray from = points.optJSONArray(i), to = points.optJSONArray(i + 1);
      if (from == null || to == null) continue;
      float x0 = (float)from.optDouble(0), y0 = (float)from.optDouble(1);
      float x1 = (float)to.optDouble(0), y1 = (float)to.optDouble(1);
      int steps = Math.max(1, (int)Math.ceil(Math.abs(x1 - x0)));
      for (int step = 0; step < steps; step++) {
        float t0 = step / (float)steps, t1 = (step + 1) / (float)steps;
        float xa = x0 + (x1 - x0) * t0, xb = x0 + (x1 - x0) * t1;
        if (dashed && (((int)Math.floor((xa + xb) * 0.5f / 4.5f)) & 1) != 0) continue;
        float ya = y0 + (y1 - y0) * t0, yb = y0 + (y1 - y0) * t1;
        float[] a = project(xa, ya, center, horizon, bottom), b = project(xb, yb, center, horizon, bottom);
        float depth = Math.max(0, (xa + xb) * 0.5f);
        p.setStrokeWidth((dashed ? 1.6f : 1.2f) + (dashed ? 5.2f : 3.6f) / (1f + depth / 16f));
        c.drawLine(a[0], a[1], b[0], b[1], p);
      }
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

  private void drawLead(Canvas c, Paint p, JSONObject lead, JSONArray modelPath, float center, float horizon, float bottom, boolean primary) {
    float distance = (float)lead.optDouble("d", 0), lateral = (float)lead.optDouble("y", 0);
    if (distance <= 0 || distance > 150) return;
    float[] pos = project(distance, lateral, center, horizon, bottom);
    float scale = Math.max(0.22f, 1f / (1f + distance / 24f));
    float width = Math.max(22f, 94f * scale);
    drawVehicleSprite(c, p, otherCar, pos[0], pos[1] - width * 0.18f, width,
        pathYaw(modelPath, distance, center, horizon, bottom), primary ? 255 : 215);
    if (primary) text(c, p, String.format(Locale.US, "%.0fm", distance), pos[0], pos[1] - width * 0.92f,
        Math.max(17, 23 * scale), Color.WHITE, Paint.Align.CENTER);
  }

  private void drawVehicleSprite(Canvas c, Paint p, Bitmap car, float cx, float cy, float width, float angle, int alpha) {
    if (car == null || car.isRecycled()) return;
    float height = width * car.getHeight() / (float)car.getWidth();
    int save = c.save(); c.translate(cx, cy); c.rotate(angle);
    p.setShader(null); p.setStyle(Paint.Style.FILL); p.setAlpha(alpha); p.setFilterBitmap(true);
    c.drawBitmap(car, null, new RectF(-width * 0.5f, -height * 0.5f, width * 0.5f, height * 0.5f), p);
    p.setAlpha(255); c.restoreToCount(save);
  }

  private void drawBsdVehicle(Canvas c, Paint p, float x, float y, boolean left) {
    drawVehicleSprite(c, p, otherCar, x, y, 58, left ? -16f : 16f, 245);
  }

  private float pathYaw(JSONArray points, float distance, float center, float horizon, float bottom) {
    if (points == null || points.length() < 2) return 0f;
    JSONArray before = null, after = null;
    for (int i = 0; i < points.length(); i++) {
      JSONArray point = points.optJSONArray(i); if (point == null) continue;
      float x = (float)point.optDouble(0);
      if (x <= distance) before = point;
      if (x >= distance) { after = point; break; }
    }
    if (before == null) before = points.optJSONArray(0);
    if (after == null) after = points.optJSONArray(points.length() - 1);
    if (before == null || after == null || before == after) return 0f;
    float[] a = project((float)before.optDouble(0), (float)before.optDouble(1), center, horizon, bottom);
    float[] b = project((float)after.optDouble(0), (float)after.optDouble(1), center, horizon, bottom);
    float angle = (float)Math.toDegrees(Math.atan2(b[0] - a[0], a[1] - b[1]));
    return Math.max(-22f, Math.min(22f, angle));
  }

  private void drawTurnArrow(Canvas c, Paint p, float x, float y, boolean left) {
    Path arrow = new Path(); float sign = left ? -1f : 1f;
    arrow.moveTo(x + 28 * sign, y - 26); arrow.lineTo(x - 24 * sign, y); arrow.lineTo(x + 28 * sign, y + 26);
    p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(9); p.setStrokeCap(Paint.Cap.ROUND); p.setStrokeJoin(Paint.Join.ROUND);
    p.setColor(Color.rgb(0, 235, 135)); c.drawPath(arrow, p);
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
    if (egoCar != null) { egoCar.recycle(); egoCar = null; }
    if (otherCar != null) { otherCar.recycle(); otherCar = null; }
    synchronized (mapFrame) {
      Bitmap map = mapFrame.getAndSet(null); if (map != null) map.recycle();
    }
    super.onDestroy();
  }

  @Override public IBinder onBind(Intent intent) { return null; }
}
