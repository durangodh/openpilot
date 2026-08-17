package ai.comma.remotehud;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Shader;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * v0.19 — 실제 원근투영(핀홀 카메라)으로 그리는 3D 주행씬.
 *
 * v0.18 까지의 project() 는 세로는 d/(13+d), 가로는 66/(1+d/17) 이라는
 * 서로 다른 근사식을 써서, 거리에 따른 도로 폭 / 차선 간격 / 차량 크기의
 * 비율이 물리적으로 맞지 않았다. 여기서는 하나의 카메라 모델만 쓴다.
 *
 *   depth = X + CAM_BACK                (X: 차 기준 전방 m)
 *   u     = CX     - FOCAL * Y / depth  (Y: 좌측이 +)
 *   v     = HORIZON + FOCAL * (CAM_H - Z) / depth
 *
 * 즉 노면(Z=0)은 depth 가 커질수록 HORIZON 으로 수렴하고, 높이 Z 를 가진
 * 점은 같은 depth 에서 정확히 그만큼 위로 올라간다. 건물·차량·차선·노면이
 * 전부 같은 변환을 통과하므로 원근이 서로 어긋나지 않는다.
 *
 * 카메라는 차 뒤 CAM_BACK m, 노면 위 CAM_H m 에서 수평으로 본다.
 * 주행 패널이 765x237 로 납작하기 때문에 지평선 위 여유가 32px 뿐이고,
 * 높은 건물은 위가 잘린다. 잘린 자국이 보이지 않도록 상단에 헤이즈
 * 그라디언트를 덮는다.
 */
final class World3D {

    /** 주행 패널 화면 영역 */
    static final float LEFT = 0f;
    static final float RIGHT = 952f;
    static final float TOP = 217f;
    static final float BOTTOM = 454f;
    static final float CX = 476f;

    /** 카메라 */
    static final float FOCAL = 520f;
    static final float CAM_H = 3.4f;
    static final float CAM_BACK = 9.5f;
    static final float HORIZON = 249f;
    static final float NEAR_DEPTH = 8.2f;
    static final float FAR_DEPTH = 185f;

    /** 도로 */
    static final float ROAD_HALF = 4.45f;
    static final float SHOULDER = 0.65f;
    static final float LANE_PAINT_W = 0.16f;
    static final float EDGE_PAINT_W = 0.22f;
    static final float DASH_PERIOD = 8.0f;
    static final float DASH_ON = 3.0f;

    /** 건물 */
    private static final float BLOCK = 24f;
    private static final int BLOCK_COUNT = 13;
    private static final float BUILD_NEAR = 12f;
    private static final float BUILD_FAR = 165f;
    private static final float HAZE_START = 95f;

    private static final int SLICES = 26;
    private static final int MAX_PATH = 80;

    // ── 프레임당 재할당을 피하기 위한 버퍼 ────────────────────────────────
    private final float[] pathX = new float[MAX_PATH];
    private final float[] pathY = new float[MAX_PATH];
    private int pathCount;

    private final float[] pa = new float[2];
    private final float[] pb = new float[2];
    private final float[] pc = new float[2];
    private final float[] pd = new float[2];

    private final float[] lx = new float[SLICES];
    private final float[] ly = new float[SLICES];
    private final float[] rx = new float[SLICES];
    private final float[] ry = new float[SLICES];
    private final float[] olx = new float[SLICES];
    private final float[] oly = new float[SLICES];
    private final float[] orx = new float[SLICES];
    private final float[] ory = new float[SLICES];

    private final Path poly = new Path();
    private final Path poly2 = new Path();
    private final RectF rect = new RectF();

    private LinearGradient roadShader;
    private int roadShaderTop;
    private int roadShaderBottom;
    private LinearGradient hazeShader;
    private int hazeShaderColor;
    private LinearGradient ribbonShader;
    private int ribbonShaderColor;

    // ── 투영 ──────────────────────────────────────────────────────────────

    /** 차 기준 (X 전방, Y 좌측, Z 상방) 한 점을 화면 좌표로. 너무 가까우면 false. */
    boolean project(float x, float y, float z, float[] out) {
        float depth = x + CAM_BACK;
        if (depth < NEAR_DEPTH) {
            return false;
        }
        float inv = FOCAL / depth;
        out[0] = CX - y * inv;
        out[1] = HORIZON + (CAM_H - z) * inv;
        return true;
    }

    /** 해당 depth 에서 1m 가 화면상 몇 px 인지 */
    static float pxPerMeter(float depth) {
        return FOCAL / Math.max(NEAR_DEPTH, depth);
    }

    // ── 모델 경로 ─────────────────────────────────────────────────────────

    /**
     * v0.18 의 pathCenterAt() 은 호출마다 JSON 배열 전체를 훑으면서 최근접
     * 표본 하나를 골랐다. 프레임당 100회 가까이 불리므로 JSON 접근만
     * 수천 번이었고, 최근접 표본이라 값이 계단처럼 튀었다.
     * 여기서는 프레임당 한 번만 배열로 풀고, 이후에는 선형보간한다.
     */
    void setScene(JSONObject s) {
        pathCount = 0;
        if (s == null) {
            return;
        }
        JSONArray a = s.optJSONArray("path");
        if (a == null) {
            return;
        }
        int n = Math.min(a.length(), MAX_PATH);
        for (int i = 0; i < n; i++) {
            JSONArray q = a.optJSONArray(i);
            if (q == null) {
                continue;
            }
            double dx = q.optDouble(0, Double.NaN);
            double dy = q.optDouble(1, Double.NaN);
            if (Double.isNaN(dx) || Double.isNaN(dy)) {
                continue;
            }
            float x = (float) dx;
            float y = (float) dy;
            if (pathCount > 0 && x <= pathX[pathCount - 1]) {
                continue;
            }
            pathX[pathCount] = x;
            pathY[pathCount] = y;
            pathCount++;
        }
    }

    /** 전방 x(m) 에서의 주행 경로 중심 y(m). 모델 끝 너머는 기울기를 제한해 연장. */
    float centerAt(float x) {
        if (pathCount == 0) {
            return 0f;
        }
        if (x <= pathX[0]) {
            return pathY[0];
        }
        int last = pathCount - 1;
        if (x >= pathX[last]) {
            if (pathCount >= 2) {
                float dx = pathX[last] - pathX[last - 1];
                if (dx > 0.01f) {
                    float slope = (pathY[last] - pathY[last - 1]) / dx;
                    slope = Math.max(-0.12f, Math.min(0.12f, slope));
                    return pathY[last] + slope * Math.min(70f, x - pathX[last]);
                }
            }
            return pathY[last];
        }
        int lo = 0;
        int hi = last;
        while (hi - lo > 1) {
            int mid = (lo + hi) >>> 1;
            if (pathX[mid] <= x) {
                lo = mid;
            } else {
                hi = mid;
            }
        }
        float span = pathX[hi] - pathX[lo];
        float t = span > 0.001f ? (x - pathX[lo]) / span : 0f;
        return pathY[lo] + (pathY[hi] - pathY[lo]) * t;
    }

    // ── 씬 전체 ───────────────────────────────────────────────────────────

    /**
     * @param buildings 도로변 건물을 그릴지. 건물은 실제 주변 지형이 아니라
     *                  24m 격자에 해시로 찍어내는 장식이므로, 헷갈리면 끈다.
     *                  EON 패킷의 hudBuildings 로 제어된다 (0 = 끔, 기본 1).
     */
    void draw(Canvas c, Paint p, JSONObject s, boolean enabled,
              Bitmap egoCar, Bitmap otherCar, float odoM,
              int bgColor, int roadTop, int roadBottom, int pathColor,
              int radarInfo, boolean buildings) {
        setScene(s);

        int save = c.save();
        c.clipRect(LEFT, TOP, RIGHT, BOTTOM);

        int sky = blend(bgColor, Color.WHITE, 0.35f);
        int ground = blend(bgColor, Color.BLACK, 0.10f);

        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(sky);
        c.drawRect(LEFT, TOP, RIGHT, HORIZON, p);
        p.setColor(ground);
        c.drawRect(LEFT, HORIZON, RIGHT, BOTTOM, p);

        drawRoad(c, p, roadTop, roadBottom);
        drawMarkings(c, p, s);
        if (enabled) {
            drawPathRibbon(c, p, pathColor);
        }
        if (buildings) {
            drawBuildings(c, p, odoM, sky);
        }
        drawVehicles(c, p, s, egoCar, otherCar, radarInfo);
        drawHaze(c, p, sky);

        p.setShader(null);
        c.restoreToCount(save);
    }

    // ── 노면 ──────────────────────────────────────────────────────────────

    private void drawRoad(Canvas c, Paint p, int topColor, int bottomColor) {
        float invNear = 1f / NEAR_DEPTH;
        float invFar = 1f / FAR_DEPTH;
        for (int i = 0; i < SLICES; i++) {
            float t = i / (SLICES - 1f);
            float depth = 1f / (invNear + (invFar - invNear) * t);
            float x = depth - CAM_BACK;
            float cen = centerAt(x);
            float inv = FOCAL / depth;
            float vy = HORIZON + CAM_H * inv;
            lx[i] = CX - (cen + ROAD_HALF) * inv;
            ly[i] = vy;
            rx[i] = CX - (cen - ROAD_HALF) * inv;
            ry[i] = vy;
            olx[i] = CX - (cen + ROAD_HALF + SHOULDER) * inv;
            oly[i] = vy;
            orx[i] = CX - (cen - ROAD_HALF - SHOULDER) * inv;
            ory[i] = vy;
        }

        if (roadShader == null || roadShaderTop != topColor || roadShaderBottom != bottomColor) {
            roadShader = new LinearGradient(0f, HORIZON, 0f, BOTTOM, topColor, bottomColor, Shader.TileMode.CLAMP);
            roadShaderTop = topColor;
            roadShaderBottom = bottomColor;
        }

        ribbon(c, p, olx, oly, lx, ly, blend(topColor, Color.BLACK, 0.16f));
        ribbon(c, p, rx, ry, orx, ory, blend(topColor, Color.BLACK, 0.16f));

        poly.rewind();
        poly.moveTo(lx[0], ly[0]);
        for (int i = 1; i < SLICES; i++) {
            poly.lineTo(lx[i], ly[i]);
        }
        for (int i = SLICES - 1; i >= 0; i--) {
            poly.lineTo(rx[i], ry[i]);
        }
        poly.close();
        p.setStyle(Paint.Style.FILL);
        p.setShader(roadShader);
        c.drawPath(poly, p);
        p.setShader(null);
    }

    private void ribbon(Canvas c, Paint p, float[] ax, float[] ay, float[] bx, float[] by, int color) {
        poly2.rewind();
        poly2.moveTo(ax[0], ay[0]);
        for (int i = 1; i < SLICES; i++) {
            poly2.lineTo(ax[i], ay[i]);
        }
        for (int i = SLICES - 1; i >= 0; i--) {
            poly2.lineTo(bx[i], by[i]);
        }
        poly2.close();
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(color);
        c.drawPath(poly2, p);
    }

    // ── 차선 / 도로 경계 ──────────────────────────────────────────────────

    private void drawMarkings(Canvas c, Paint p, JSONObject s) {
        if (s == null) {
            return;
        }
        JSONArray edges = s.optJSONArray("edges");
        if (edges != null) {
            for (int i = 0; i < edges.length(); i++) {
                JSONObject e = edges.optJSONObject(i);
                if (e == null || e.optDouble("c", 0d) < 0.18d) {
                    continue;
                }
                polyline(c, p, e.optJSONArray("p"), Color.rgb(148, 157, 163), EDGE_PAINT_W, false, 255);
            }
        }
        JSONArray lanes = s.optJSONArray("lanes");
        if (lanes != null) {
            int egoLeft = -1;
            int egoRight = -1;
            // laneLines 는 모델 순서상 index 1 이 좌측 주행차선, 2 가 우측이다.
            if (lanes.length() >= 3) {
                egoLeft = 1;
                egoRight = 2;
            }
            for (int i = 0; i < lanes.length(); i++) {
                JSONObject lane = lanes.optJSONObject(i);
                if (lane == null) {
                    continue;
                }
                double conf = lane.optDouble("c", 1d);
                int alpha = (int) Math.max(70d, Math.min(255d, 90d + conf * 170d));
                boolean ego = (i == egoLeft || i == egoRight);
                int color = ego ? Color.rgb(246, 208, 84) : Color.rgb(248, 250, 250);
                polyline(c, p, lane.optJSONArray("p"), color, LANE_PAINT_W, true, alpha);
            }
        }
    }

    private void polyline(Canvas c, Paint p, JSONArray pts, int color, float widthM, boolean dashed, int alpha) {
        if (pts == null || pts.length() < 2) {
            return;
        }
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(color);
        p.setAlpha(alpha);
        for (int i = 0; i < pts.length() - 1; i++) {
            JSONArray a = pts.optJSONArray(i);
            JSONArray b = pts.optJSONArray(i + 1);
            if (a == null || b == null) {
                continue;
            }
            float x1 = (float) a.optDouble(0, 0d);
            float y1 = (float) a.optDouble(1, 0d);
            float x2 = (float) b.optDouble(0, 0d);
            float y2 = (float) b.optDouble(1, 0d);
            if (x2 > FAR_DEPTH) {
                continue;
            }
            if (dashed) {
                float mid = (x1 + x2) * 0.5f;
                float phase = mid - (float) Math.floor(mid / DASH_PERIOD) * DASH_PERIOD;
                if (phase > DASH_ON) {
                    continue;
                }
            }
            if (!project(x1, y1, 0f, pa) || !project(x2, y2, 0f, pb)) {
                continue;
            }
            float wa = Math.max(1.1f, widthM * pxPerMeter(x1 + CAM_BACK));
            float wb = Math.max(1.0f, widthM * pxPerMeter(x2 + CAM_BACK));
            quad(c, p, pa, pb, wa, wb);
        }
        p.setAlpha(255);
    }

    /** 두 점을 잇는, 양 끝 두께가 다른 사다리꼴. 원근에 맞게 선이 가늘어진다. */
    private void quad(Canvas c, Paint p, float[] a, float[] b, float wa, float wb) {
        float dx = b[0] - a[0];
        float dy = b[1] - a[1];
        float len = (float) Math.hypot(dx, dy);
        if (len < 0.4f) {
            return;
        }
        float nx = -dy / len;
        float ny = dx / len;
        float ha = wa * 0.5f;
        float hb = wb * 0.5f;
        poly2.rewind();
        poly2.moveTo(a[0] + nx * ha, a[1] + ny * ha);
        poly2.lineTo(b[0] + nx * hb, b[1] + ny * hb);
        poly2.lineTo(b[0] - nx * hb, b[1] - ny * hb);
        poly2.lineTo(a[0] - nx * ha, a[1] - ny * ha);
        poly2.close();
        c.drawPath(poly2, p);
    }

    // ── 주행 경로 리본 ────────────────────────────────────────────────────

    private void drawPathRibbon(Canvas c, Paint p, int color) {
        if (pathCount < 2) {
            return;
        }
        final float half = 0.85f;
        poly.rewind();
        boolean started = false;
        int used = 0;
        for (int i = 0; i < pathCount; i++) {
            float x = pathX[i];
            if (x + CAM_BACK < NEAR_DEPTH || x > FAR_DEPTH) {
                continue;
            }
            if (!project(x, pathY[i] + half, 0f, pa)) {
                continue;
            }
            if (started) {
                poly.lineTo(pa[0], pa[1]);
            } else {
                poly.moveTo(pa[0], pa[1]);
                started = true;
            }
            used++;
        }
        if (used < 2) {
            return;
        }
        for (int i = pathCount - 1; i >= 0; i--) {
            float x = pathX[i];
            if (x + CAM_BACK < NEAR_DEPTH || x > FAR_DEPTH) {
                continue;
            }
            if (project(x, pathY[i] - half, 0f, pa)) {
                poly.lineTo(pa[0], pa[1]);
            }
        }
        poly.close();

        if (ribbonShader == null || ribbonShaderColor != color) {
            ribbonShader = new LinearGradient(0f, HORIZON, 0f, BOTTOM,
                    Color.argb(40, Color.red(color), Color.green(color), Color.blue(color)),
                    Color.argb(180, Color.red(color), Color.green(color), Color.blue(color)),
                    Shader.TileMode.CLAMP);
            ribbonShaderColor = color;
        }
        p.setStyle(Paint.Style.FILL);
        p.setShader(ribbonShader);
        c.drawPath(poly, p);
        p.setShader(null);

        p.setStyle(Paint.Style.STROKE);
        p.setStrokeWidth(2.6f);
        p.setStrokeJoin(Paint.Join.ROUND);
        p.setColor(Color.rgb(Math.min(255, Color.red(color) + 30),
                Math.min(255, Color.green(color) + 34),
                Math.min(255, Color.blue(color) + 30)));
        c.drawPath(poly, p);
        p.setStyle(Paint.Style.FILL);
    }

    // ── 건물 ──────────────────────────────────────────────────────────────

    /**
     * 건물은 도로변 24m 격자에 고정돼 있고, 주행거리(odoM)만큼 뒤로 흘러간다.
     * v0.18 은 거리 배열이 {96,80,66,...} 로 고정이라 아무리 달려도 건물이
     * 제자리에 붙어 있었다. 여기서는 블록 인덱스를 절대 주행거리로 잡아
     * 실제로 다가왔다가 지나간다.
     */
    private void drawBuildings(Canvas c, Paint p, float odoM, int fogColor) {
        int base = (int) Math.floor(odoM / BLOCK);
        for (int i = BLOCK_COUNT - 1; i >= 0; i--) {
            int k = base + i;
            float d = k * BLOCK - odoM;
            if (d < BUILD_NEAR || d > BUILD_FAR) {
                continue;
            }
            for (int side = -1; side <= 1; side += 2) {
                int salt = side > 0 ? 0x51ED : 0x2F19;
                if (rnd(k, salt) < 0.26f) {
                    continue;   // 빈 필지
                }
                float gap = 1.6f + rnd(k, salt + 1) * 3.2f;
                float width = 7f + rnd(k, salt + 2) * 9f;
                float depth = 8f + rnd(k, salt + 3) * 10f;
                float height = 4f + rnd(k, salt + 4) * 7f;
                int tone = 196 - (int) (rnd(k, salt + 5) * 34f);
                building(c, p, d, d + depth, side, gap, width, height, tone, fogColor);
            }
        }
    }

    private void building(Canvas c, Paint p, float d0, float d1, int side,
                          float gap, float width, float height, int tone, int fogColor) {
        float inner = centerAt(d0) + side * (ROAD_HALF + SHOULDER + gap);
        float outer = inner + side * width;

        // 앞면(가까운 쪽 X=d0) 네 모서리
        if (!project(d0, inner, 0f, pa)) {
            return;
        }
        if (!project(d0, outer, 0f, pb)) {
            return;
        }
        if (!project(d0, outer, height, pc)) {
            return;
        }
        if (!project(d0, inner, height, pd)) {
            return;
        }
        // 화면 밖이면 통째로 버림
        float minX = Math.min(Math.min(pa[0], pb[0]), Math.min(pc[0], pd[0]));
        float maxX = Math.max(Math.max(pa[0], pb[0]), Math.max(pc[0], pd[0]));
        if (maxX < LEFT - 40f || minX > RIGHT + 40f) {
            return;
        }

        float fog = d0 <= HAZE_START ? 0f
                : Math.min(0.82f, (d0 - HAZE_START) / (BUILD_FAR - HAZE_START) * 0.95f);

        int front = blend(Color.rgb(tone, Math.min(255, tone + 4), Math.min(255, tone + 11)), fogColor, fog);
        int flank = blend(Color.rgb(Math.max(120, tone - 34), Math.max(126, tone - 29), Math.max(134, tone - 21)),
                fogColor, fog);

        // 측면(안쪽 벽, Y=inner 평면). 카메라가 도로 중앙에 있으므로 이 면이 보인다.
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        poly2.rewind();
        poly2.moveTo(pa[0], pa[1]);
        poly2.lineTo(pd[0], pd[1]);
        if (project(d1, inner, height, pc) && project(d1, inner, 0f, pd)) {
            poly2.lineTo(pc[0], pc[1]);
            poly2.lineTo(pd[0], pd[1]);
            poly2.close();
            p.setColor(flank);
            c.drawPath(poly2, p);
        }

        // 앞면
        if (!project(d0, outer, height, pc) || !project(d0, inner, height, pd)) {
            return;
        }
        poly2.rewind();
        poly2.moveTo(pa[0], pa[1]);
        poly2.lineTo(pb[0], pb[1]);
        poly2.lineTo(pc[0], pc[1]);
        poly2.lineTo(pd[0], pd[1]);
        poly2.close();
        p.setColor(front);
        c.drawPath(poly2, p);

        // 창문 (앞면이 충분히 클 때만)
        float faceW = Math.abs(pb[0] - pa[0]);
        float faceH = Math.abs(pa[1] - pd[1]);
        if (faceW < 20f || faceH < 26f || fog > 0.55f) {
            return;
        }
        int cols = Math.max(2, Math.min(4, (int) (faceW / 14f)));
        int rows = Math.max(2, Math.min(5, (int) (faceH / 16f)));
        p.setColor(blend(Color.rgb(122, 152, 176), fogColor, fog));
        for (int r = 0; r < rows; r++) {
            for (int q = 0; q < cols; q++) {
                float u0 = (q + 0.30f) / cols;
                float u1 = (q + 0.70f) / cols;
                float v0 = (r + 0.28f) / rows;
                float v1 = (r + 0.66f) / rows;
                float xa = lerp(pa[0], pb[0], u0);
                float xb = lerp(pa[0], pb[0], u1);
                float ya = lerp(pa[1], pd[1], v0);
                float yb = lerp(pa[1], pd[1], v1);
                rect.set(Math.min(xa, xb), Math.min(ya, yb), Math.max(xa, xb), Math.max(ya, yb));
                c.drawRect(rect, p);
            }
        }
    }

    // ── 차량 ──────────────────────────────────────────────────────────────

    private void drawVehicles(Canvas c, Paint p, JSONObject s, Bitmap egoCar, Bitmap otherCar, int radarInfo) {
        if (s == null) {
            return;
        }
        JSONObject lead = s.optJSONObject("lead");
        JSONObject lead2 = s.optJSONObject("lead2");
        float d1 = lead == null ? -1f : (float) lead.optDouble("d", -1d);
        float d2 = lead2 == null ? -1f : (float) lead2.optDouble("d", -1d);

        // 먼 것부터 (painter's algorithm)
        if (d2 > d1) {
            car(c, p, otherCar, lead2, 205);
            car(c, p, otherCar, lead, 245);
        } else {
            car(c, p, otherCar, lead, 245);
            car(c, p, otherCar, lead2, 205);
        }

        boolean leftBsd = s.optBoolean("leftBsd", false);
        boolean rightBsd = s.optBoolean("rightBsd", false);
        if (leftBsd) {
            billboard(c, p, otherCar, -0.6f, 3.5f, 1.9f, -7f, 235);
        }
        if (rightBsd) {
            billboard(c, p, otherCar, -0.6f, -3.5f, 1.9f, 7f, 235);
        }
        billboard(c, p, egoCar, 0f, 0f, 1.95f, egoRoll(), 255);

        if (radarInfo == 3 || radarInfo == 4) {
            markLead(c, p, lead);
        }
    }

    private void car(Canvas c, Paint p, Bitmap bmp, JSONObject lead, int alpha) {
        if (lead == null) {
            return;
        }
        float d = (float) lead.optDouble("d", -1d);
        float y = (float) lead.optDouble("y", 0d);
        if (d <= 0f || d > FAR_DEPTH) {
            return;
        }
        billboard(c, p, bmp, d, y, 1.9f, 0f, alpha);
    }

    /** 리드 차량 발밑에 거리 표시용 얇은 가로선 (radarInfo 3/4 일 때만) */
    private void markLead(Canvas c, Paint p, JSONObject lead) {
        if (lead == null) {
            return;
        }
        float d = (float) lead.optDouble("d", -1d);
        if (d <= 0f || d > 90f) {
            return;
        }
        float y = (float) lead.optDouble("y", 0d);
        if (!project(d, y + 1.2f, 0f, pa) || !project(d, y - 1.2f, 0f, pb)) {
            return;
        }
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(Color.argb(150, 236, 92, 72));
        quad(c, p, pa, pb, 2.4f, 2.4f);
    }

    /** 경로 접선 기울기로 자차 스프라이트를 살짝 기울인다 (곡선 주행감) */
    private float egoRoll() {
        if (pathCount < 2) {
            return 0f;
        }
        float y0 = centerAt(2f);
        float y1 = centerAt(14f);
        float roll = (float) Math.toDegrees(Math.atan2(y0 - y1, 12f)) * 0.45f;
        return Math.max(-16f, Math.min(16f, roll));
    }

    /**
     * 지면에 세운 빌보드. 스프라이트가 후면뷰이므로 depth 에 따른 크기만
     * 정확히 맞추면 원근이 성립한다. 폭 widthM(m) 이 화면에서 몇 px 인지는
     * FOCAL/depth 로 결정된다.
     */
    private void billboard(Canvas c, Paint p, Bitmap b, float x, float y,
                           float widthM, float rollDeg, int alpha) {
        if (b == null || b.isRecycled()) {
            return;
        }
        if (!project(x, y, 0f, pa)) {
            return;
        }
        float depth = x + CAM_BACK;
        float scale = FOCAL / depth;
        float w = widthM * scale;
        if (w < 6f) {
            return;
        }
        float h = b.getHeight() * w / b.getWidth();

        int save = c.save();
        c.translate(pa[0], pa[1]);
        if (rollDeg != 0f) {
            c.rotate(rollDeg);
        }

        // 접지 그림자: 지면 위의 원은 depth 에 따라 세로로 눌린 타원이 된다.
        float rx0 = w * 0.52f;
        float ry0 = Math.max(1.5f, rx0 * CAM_H / depth);
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(Color.argb(Math.min(96, alpha / 2), 16, 20, 24));
        rect.set(-rx0, -ry0, rx0, ry0);
        c.drawOval(rect, p);

        p.setAlpha(alpha);
        p.setFilterBitmap(true);
        rect.set(-w / 2f, -h, w / 2f, 0f);
        c.drawBitmap(b, null, rect, p);
        p.setAlpha(255);
        c.restoreToCount(save);
    }

    // ── 상단 헤이즈 ───────────────────────────────────────────────────────

    private void drawHaze(Canvas c, Paint p, int skyColor) {
        if (hazeShader == null || hazeShaderColor != skyColor) {
            hazeShader = new LinearGradient(0f, TOP, 0f, TOP + 46f,
                    Color.argb(255, Color.red(skyColor), Color.green(skyColor), Color.blue(skyColor)),
                    Color.argb(0, Color.red(skyColor), Color.green(skyColor), Color.blue(skyColor)),
                    Shader.TileMode.CLAMP);
            hazeShaderColor = skyColor;
        }
        p.setStyle(Paint.Style.FILL);
        p.setShader(hazeShader);
        c.drawRect(LEFT, TOP, RIGHT, TOP + 46f, p);
        p.setShader(null);
    }

    // ── 유틸 ──────────────────────────────────────────────────────────────

    private static float lerp(float a, float b, float t) {
        return a + (b - a) * t;
    }

    static int blend(int a, int b, float t) {
        if (t <= 0f) {
            return a;
        }
        if (t >= 1f) {
            return b;
        }
        return Color.rgb(
                (int) (Color.red(a) + (Color.red(b) - Color.red(a)) * t),
                (int) (Color.green(a) + (Color.green(b) - Color.green(a)) * t),
                (int) (Color.blue(a) + (Color.blue(b) - Color.blue(a)) * t));
    }

    private static float rnd(int k, int salt) {
        int h = (k * 0x9E3779B1) ^ (salt * 0x85EBCA6B);
        h ^= h >>> 15;
        h *= 0x2545F491;
        h ^= h >>> 13;
        return ((h >>> 8) & 0xFFFF) / 65535f;
    }
}
