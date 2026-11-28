package ai.s20.ui;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.view.View;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;

/** openpilot overlay from /data/s20pilot/share/ui.json (written by s20uid): path, lanes, edges, lead, speeds, status. */
final class OverlayView extends View {
  static final File UI = new File("/data/s20pilot/share/ui.json");
  private final Geometry geo;
  private final CameraRenderer cam;
  private volatile JSONObject ui;
  private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG), text = new Paint(Paint.ANTI_ALIAS_FLAG), box = new Paint(Paint.ANTI_ALIAS_FLAG);
  private final Path path = new Path();

  OverlayView(Context c, Geometry geo, CameraRenderer cam) {
    super(c);
    this.geo = geo; this.cam = cam;
    text.setTypeface(Typeface.DEFAULT_BOLD);
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
    boolean fresh = u != null && System.currentTimeMillis() / 1000.0 - u.optDouble("t") < 2.0;
    boolean active = fresh && u.optBoolean("active"), enabled = fresh && u.optBoolean("enabled");
    String alertStatus = fresh ? u.optString("alert_status") : "";

    // status border like openpilot: green engaged, grey disengaged, orange/red alerts
    int border = !fresh ? Color.rgb(60, 60, 60) : alertStatus.equals("critical") ? Color.rgb(201, 34, 49)
        : alertStatus.equals("userPrompt") ? Color.rgb(218, 111, 37) : enabled ? Color.rgb(23, 134, 68) : Color.rgb(23, 51, 73);
    box.setStyle(Paint.Style.STROKE); box.setStrokeWidth(24); box.setColor(border);
    c.drawRect(12, 12, W - 12, H - 12, box);
    if (!fresh) { centered(c, "openpilot 데이터 없음 (s20uid 대기)", W / 2f, H / 2f, 48, Color.WHITE); fps(c, W); return; }

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
    String st = String.format("%s  calib:%s  model:%s  cam %.0f fps  CPU %.0f°C", u.optString("state"), u.optString("calib"),
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
