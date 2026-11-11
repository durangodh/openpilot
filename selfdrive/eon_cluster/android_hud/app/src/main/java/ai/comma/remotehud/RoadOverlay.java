package ai.comma.remotehud;

import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Typeface;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.Locale;

/**
 * Draws the EON onroad lines (selfdrive/ui NvgWindow) over the EON-view camera
 * crop sent by eon_web. The HUD phone (S20) does the drawing; the EON only
 * crops and encodes the camera. Geometry follows ui.cc exactly:
 * calib_frame_to_full_frame, update_line_data, update_model, update_leads and
 * NvgWindow::drawLaneLines / drawCarrotLead.
 */
final class RoadOverlay {
    // EON road camera and onroad view (common/transformations/camera.py, ui.h).
    private static final float FOCAL = 910f;
    private static final float CAM_CX = 1164f / 2f;
    private static final float CAM_CY = 874f / 2f;
    private static final float EON_W = 1880f;   // 1920x1080 minus the 20 px border
    private static final float EON_H = 1040f;
    private static final float ZOOM = 2138.5f / FOCAL;
    private static final float CLIP_MARGIN = 1000f;
    private static final float MIN_DRAW_DISTANCE = 10f;
    private static final float MAX_DRAW_DISTANCE = 100f;
    private static final float PATH_Z = 1.22f;
    private static final float LEAD_BOX_MIN_W = 180f;
    private static final float LEAD_BOX_MAX_W = 320f;
    private static final int MAX_POINTS = 33;

    private final float[] view = new float[9];
    private final float[] projected = new float[2];
    private final float[] lx = new float[MAX_POINTS], ly = new float[MAX_POINTS];
    private final float[] rx = new float[MAX_POINTS], ry = new float[MAX_POINTS];
    private final float[] px = new float[MAX_POINTS], py = new float[MAX_POINTS], pz = new float[MAX_POINTS];
    private final Path path = new Path();
    private final RectF rect = new RectF();
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private int pathCount;

    // Output mapping: dest = left + (eonX - originX) * scale.
    private float left, top, originX, originY, scale;

    // drawCarrotLead EMA (0.85 per 10 Hz EON frame, kept as a time constant).
    private float boxW, boxX, boxY;
    private boolean boxValid;
    private long boxTime;

    /**
     * @param dest  where the camera crop is drawn on the HUD canvas
     * @param crop  camera crop {u0, v0, u1, v1} reported by eon_web
     * @param pathBottom HUD y below which the path is not drawn (ego sprite), or Float.NaN
     */
    void draw(Canvas c, RectF dest, int[] crop, JSONObject s, float pathBottom) {
        JSONObject cam = s.optJSONObject("cam");
        JSONArray rpy = cam == null ? null : cam.optJSONArray("rpy");
        if (cam == null || rpy == null || rpy.length() != 3 || !s.optBoolean("worldValid", true)
                || crop == null || crop[2] <= crop[0] || crop[3] <= crop[1]) {
            boxValid = false;
            return;
        }
        if (!loadPath(s.optJSONArray("path"))) {
            boxValid = false;
            return;
        }
        setCalibration((float) rpy.optDouble(0), (float) rpy.optDouble(1), (float) rpy.optDouble(2));
        left = dest.left;
        top = dest.top;
        scale = dest.width() / ((crop[2] - crop[0]) * ZOOM);
        originX = EON_W / 2f + (crop[0] - CAM_CX) * ZOOM;
        originY = EON_H / 2f + (crop[1] - CAM_CY) * ZOOM;

        int save = c.save();
        c.clipRect(dest);
        paint.setShader(null);
        paint.setColorFilter(null);
        paint.setStyle(Paint.Style.FILL);

        float maxDistance = clamp(px[pathCount - 1], MIN_DRAW_DISTANCE, MAX_DRAW_DISTANCE);

        // Lane lines: half width 0.025 * prob m, alpha clamp(prob, 0, 0.7).
        JSONArray lanes = s.optJSONArray("lanes");
        JSONArray probs = cam.optJSONArray("lp");
        int laneMaxIdx = MAX_POINTS - 1;
        if (lanes != null && lanes.length() > 0) {
            JSONObject first = lanes.optJSONObject(0);
            laneMaxIdx = pathLengthIdx(first == null ? null : first.optJSONArray("p"), maxDistance);
        }
        for (int i = 0; lanes != null && i < lanes.length(); i++) {
            JSONObject lane = lanes.optJSONObject(i);
            if (lane == null) continue;
            float prob = probs != null && i < probs.length()
                    ? (float) probs.optDouble(i, 0d) : (float) lane.optDouble("c", 0d);
            if (!(prob > 0f)) continue;
            int count = groundLine(lane.optJSONArray("p"), 0.025f * prob, laneMaxIdx);
            fill(c, count, Color.argb(Math.round(255f * clamp(prob, 0f, 0.7f)), 255, 255, 255));
        }

        // Road edges: red, alpha clamp(1 - std) (the packet already sends 1 - std).
        JSONArray edges = s.optJSONArray("edges");
        for (int i = 0; edges != null && i < edges.length(); i++) {
            JSONObject edge = edges.optJSONObject(i);
            if (edge == null) continue;
            float alpha = clamp((float) edge.optDouble("c", 0d), 0f, 1f);
            if (alpha <= 0f) continue;
            int count = groundLine(edge.optJSONArray("p"), 0.025f, laneMaxIdx);
            fill(c, count, Color.argb(Math.round(255f * alpha), 255, 0, 0));
        }

        // Path: ShowPathWidth half width, 45/10/45 split, NvgWindow colours.
        JSONObject lead = s.optJSONObject("lead");
        if (lead != null) maxDistance = clamp((float) lead.optDouble("d", 0d), 0f, maxDistance);
        float width = cam.optInt("pw", 100) / 100f;
        int count = pathRibbon(clamp(width, 0.3f, 1.5f), maxDistance);
        if (count >= 2) {
            int pathSave = c.save();
            if (!Float.isNaN(pathBottom)) c.clipRect(dest.left, dest.top, dest.right, pathBottom);
            paint.setColor(pathColor(cam.optInt("pc", 1) != 0, s, lead != null));
            drawBand(c, count, 0f, 0.45f);
            drawBand(c, count, 0.55f, 1f);
            c.restoreToCount(pathSave);
        }

        drawLeads(c, s, cam, lead);
        c.restoreToCount(save);
    }

    private static int pathColor(boolean statusColor, JSONObject s, boolean lead) {
        if (!statusColor) return Color.argb(178, 26, 190, 255);  // HSL(197°, 100%, 55%), 70 %
        if (!s.optBoolean("enabled", false)) return Color.argb(120, 0, 0, 0);
        if (!lead) return Color.argb(120, 0, 153, 0);
        double accel = s.optDouble("planAccel", 0d);
        if (Double.isNaN(accel) || Math.abs(accel) < 0.5) return Color.argb(120, 255, 255, 0);
        return accel >= 0.5 ? Color.argb(120, 255, 153, 0) : Color.argb(120, 255, 0, 0);
    }

    private void drawLeads(Canvas c, JSONObject s, JSONObject cam, JSONObject lead) {
        float k = scale;
        JSONObject lead2 = s.optJSONObject("lead2");
        if (lead2 != null && leadEdges(lead2)) {
            float w2 = clamp(rx[0] - lx[0], 80f, LEAD_BOX_MAX_W * 0.8f);
            roundRect(c, lx[0] - 10f, ly[0] - w2 * 0.8f, lx[0] + w2 + 10f, ly[0],
                    Color.argb(255, 218, 111, 37), 2f);
        }
        if (lead == null || !leadEdges(lead)) {
            boxValid = false;
            return;
        }
        float rawW = clamp(rx[0] - lx[0], LEAD_BOX_MIN_W, LEAD_BOX_MAX_W);
        float rawX = clamp((lx[0] + rx[0]) / 2f, 200f, EON_W - 200f);
        float rawY = clamp(ly[0], 150f, EON_H - 120f);
        long now = android.os.SystemClock.elapsedRealtime();
        if (!boxValid) {
            boxW = rawW;
            boxX = rawX;
            boxY = rawY;
            boxValid = true;
        } else {
            float a = (float) Math.pow(0.85, clamp((now - boxTime) / 100f, 0f, 10f));
            boxW = boxW * a + rawW * (1f - a);
            boxX = boxX * a + rawX * (1f - a);
            boxY = boxY * a + rawY * (1f - a);
        }
        boxTime = now;
        boolean radar = "R".equals(lead.optString("src", "R"));
        roundRect(c, boxX - boxW / 2f - 10f, boxY - boxW * 0.8f, boxX + boxW / 2f + 10f, boxY,
                radar ? Color.argb(255, 255, 175, 3) : Color.argb(255, 0, 0, 255), 3f);

        float dy = boxY + 60f;
        float vision = (float) cam.optDouble("lv", 0d);
        tag(c, radar ? (float) lead.optDouble("d", 0d) : 0f, boxX - 125f, dy, Color.rgb(255, 175, 3), k);
        tag(c, vision, boxX + 35f, dy, Color.rgb(0, 0, 255), k);
    }

    private void tag(Canvas c, float distance, float x, float dy, int color, float k) {
        if (!(distance > 0f)) return;
        rect.set(outX(x), outY(dy - 35f), outX(x + 90f), outY(dy + 7f));
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(color);
        c.drawRoundRect(rect, 15f * k, 15f * k, paint);
        String text = String.format(Locale.US, "%.1f", distance);
        paint.setColor(Color.WHITE);
        paint.setTypeface(Typeface.DEFAULT);
        paint.setTextSize(40f * k);
        paint.setTextAlign(Paint.Align.CENTER);
        Paint.FontMetrics metrics = paint.getFontMetrics();
        c.drawText(text, rect.centerX(), rect.centerY() - (metrics.ascent + metrics.descent) / 2f, paint);
    }

    private void roundRect(Canvas c, float x0, float y0, float x1, float y1, int stroke, float strokeWidth) {
        rect.set(outX(x0), outY(y0), outX(x1), outY(y1));
        float radius = 15f * scale;
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(Color.argb(10, 0, 0, 0));
        c.drawRoundRect(rect, radius, radius, paint);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(Math.max(1f, strokeWidth * scale));
        paint.setColor(stroke);
        c.drawRoundRect(rect, radius, radius, paint);
        paint.setStyle(Paint.Style.FILL);
    }

    /** update_leads: rear corners 1.2 m either side, at road height + 1.22 m. */
    private boolean leadEdges(JSONObject lead) {
        float d = (float) lead.optDouble("d", Double.NaN);
        float y = (float) lead.optDouble("y", 0d);
        if (!(d >= 0f)) return false;
        float z = pz[pathLengthIdx(d)] + PATH_Z;
        if (!project(d, -y - 1.2f, z)) return false;
        lx[0] = projected[0];
        ly[0] = projected[1];
        if (!project(d, -y + 1.2f, z)) return false;
        rx[0] = projected[0];
        ry[0] = projected[1];
        return true;
    }

    private boolean loadPath(JSONArray points) {
        pathCount = 0;
        if (points == null) return false;
        for (int i = 0; i < points.length() && pathCount < MAX_POINTS; i++) {
            JSONArray p = points.optJSONArray(i);
            if (p == null || p.length() < 2) continue;
            px[pathCount] = (float) p.optDouble(0);
            py[pathCount] = (float) p.optDouble(1);
            pz[pathCount] = p.length() > 2 ? (float) p.optDouble(2) : 0f;
            pathCount++;
        }
        return pathCount >= 2;
    }

    /** get_path_length_idx on the path. */
    private int pathLengthIdx(float distance) {
        int idx = 0;
        for (int i = 1; i < pathCount && px[i] <= distance; i++) idx = i;
        return idx;
    }

    private static int pathLengthIdx(JSONArray points, float distance) {
        int idx = 0;
        for (int i = 1; points != null && i < points.length(); i++) {
            JSONArray p = points.optJSONArray(i);
            if (p == null || p.optDouble(0) > distance) break;
            idx = i;
        }
        return idx;
    }

    /** Road height at distance x, from the path's model elevation. */
    private float roadZ(float x) {
        if (x <= px[0]) return pz[0];
        for (int i = 1; i < pathCount; i++) {
            if (x <= px[i]) {
                float span = px[i] - px[i - 1];
                float t = span > 1e-3f ? (x - px[i - 1]) / span : 0f;
                return pz[i - 1] + (pz[i] - pz[i - 1]) * t;
            }
        }
        return pz[pathCount - 1];
    }

    /** update_line_data for lane lines and road edges (allow_invert = true). */
    private int groundLine(JSONArray points, float yOff, int maxIdx) {
        int count = 0;
        for (int i = 0; points != null && i <= maxIdx && i < points.length() && count < MAX_POINTS; i++) {
            JSONArray p = points.optJSONArray(i);
            if (p == null || p.length() < 2) continue;
            float x = (float) p.optDouble(0), y = (float) p.optDouble(1);
            if (!(x >= 0f)) continue;
            float z = p.length() > 2 ? (float) p.optDouble(2) : roadZ(x);
            if (!project(x, y - yOff, z)) continue;
            float leftX = projected[0], leftY = projected[1];
            if (!project(x, y + yOff, z)) continue;
            lx[count] = leftX;
            ly[count] = leftY;
            rx[count] = projected[0];
            ry[count] = projected[1];
            count++;
        }
        return count;
    }

    /** update_mpc_path_data: no inversions, x <= max distance, z + 1.22. */
    private int pathRibbon(float yOff, float maxDistance) {
        int count = 0;
        for (int i = 0; i < pathCount; i++) {
            float x = px[i];
            if (!(x >= 0f) || x > maxDistance) continue;
            float z = pz[i] + PATH_Z;
            if (!project(x, py[i] - yOff, z)) continue;
            float leftX = projected[0], leftY = projected[1];
            if (!project(x, py[i] + yOff, z)) continue;
            if (count > 0 && leftY > ly[count - 1]) continue;
            lx[count] = leftX;
            ly[count] = leftY;
            rx[count] = projected[0];
            ry[count] = projected[1];
            count++;
        }
        return count;
    }

    private void fill(Canvas c, int count, int color) {
        if (count < 2) return;
        path.rewind();
        path.moveTo(outX(lx[0]), outY(ly[0]));
        for (int i = 1; i < count; i++) path.lineTo(outX(lx[i]), outY(ly[i]));
        for (int i = count - 1; i >= 0; i--) path.lineTo(outX(rx[i]), outY(ry[i]));
        path.close();
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(color);
        c.drawPath(path, paint);
    }

    /** One band of the corridor between fractions a and b of the left→right width. */
    private void drawBand(Canvas c, int count, float a, float b) {
        path.rewind();
        for (int i = 0; i < count; i++) {
            float x = outX(lx[i] + (rx[i] - lx[i]) * a), y = outY(ly[i] + (ry[i] - ly[i]) * a);
            if (i == 0) path.moveTo(x, y);
            else path.lineTo(x, y);
        }
        for (int i = count - 1; i >= 0; i--) {
            path.lineTo(outX(lx[i] + (rx[i] - lx[i]) * b), outY(ly[i] + (ry[i] - ly[i]) * b));
        }
        path.close();
        paint.setStyle(Paint.Style.FILL);
        c.drawPath(path, paint);
    }

    private float outX(float eonX) {
        return left + (eonX - originX) * scale;
    }

    private float outY(float eonY) {
        return top + (eonY - originY) * scale;
    }

    /** view_from_calib = view_from_device · euler2rot(roll, pitch, yaw). */
    private void setCalibration(float roll, float pitch, float yaw) {
        double cr = Math.cos(roll), sr = Math.sin(roll);
        double cp = Math.cos(pitch), sp = Math.sin(pitch);
        double cy = Math.cos(yaw), sy = Math.sin(yaw);
        // device_from_calib = Rz(yaw) · Ry(pitch) · Rx(roll)
        double r00 = cy * cp, r01 = cy * sp * sr - sy * cr, r02 = cy * sp * cr + sy * sr;
        double r10 = sy * cp, r11 = sy * sp * sr + cy * cr, r12 = sy * sp * cr - cy * sr;
        double r20 = -sp, r21 = cp * sr, r22 = cp * cr;
        // view_from_device rows: (0,1,0), (0,0,1), (1,0,0)
        view[0] = (float) r10; view[1] = (float) r11; view[2] = (float) r12;
        view[3] = (float) r20; view[4] = (float) r21; view[5] = (float) r22;
        view[6] = (float) r00; view[7] = (float) r01; view[8] = (float) r02;
    }

    /** calib_frame_to_full_frame into EON screen pixels; false outside the clip margin. */
    private boolean project(float x, float y, float z) {
        float vx = view[0] * x + view[1] * y + view[2] * z;
        float vy = view[3] * x + view[4] * y + view[5] * z;
        float vz = view[6] * x + view[7] * y + view[8] * z;
        if (!(vz > 1e-6f)) return false;
        float u = FOCAL * vx / vz + CAM_CX;
        float v = FOCAL * vy / vz + CAM_CY;
        float sx = EON_W / 2f + (u - CAM_CX) * ZOOM;
        float sy = EON_H / 2f + (v - CAM_CY) * ZOOM;
        if (!(sx >= -CLIP_MARGIN && sx <= EON_W + CLIP_MARGIN
                && sy >= -CLIP_MARGIN && sy <= EON_H + CLIP_MARGIN)) return false;
        projected[0] = sx;
        projected[1] = sy;
        return true;
    }

    private static float clamp(float value, float min, float max) {
        return Math.max(min, Math.min(max, value));
    }
}
