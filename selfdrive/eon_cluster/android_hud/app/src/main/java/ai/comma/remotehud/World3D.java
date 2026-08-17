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
 * v0.20 — 실제 원근투영(핀홀 카메라) 3D 주행씬.
 *
 *   depth = X + CAM_BACK                (X: 차 기준 전방 m)
 *   u     = CX     - FOCAL * Y / depth  (Y: 좌측이 +)
 *   v     = HORIZON + FOCAL * (CAM_H - Z) / depth
 *
 * v0.19 대비 바뀐 것
 *  - 카메라를 뒤/위로 물림 (9.5→13.0m, 3.4→4.6m). 자차가 화면을 덜 먹고
 *    앞이 더 보인다.
 *  - 경로 보간을 Catmull-Rom 으로. 17점 폴리라인의 각진 곡선이 사라진다.
 *  - **정차 중 좌우 흔들림 수정.** 모델 유효구간 밖 외삽 기울기 상한을
 *    0.12→0.06 으로 낮추고, 20m에 걸쳐 0으로 감쇠시킨다. 정차하면
 *    modelV2 position 이 0 근처에만 몰려서 먼 쪽이 ±8m씩 흔들렸다.
 *  - 노면 폭을 고정 ±4.45m 가 아니라 인식된 roadEdges 로 만든다.
 *  - 도로 경계를 연석 높이로 세운다.
 *  - 다크 / 라이트 팔레트 (hudTheme 연동).
 *  - BSD 를 차량 그림 대신 옆차선 경고 띠로. openpilot 은 옆차 유무만 알고
 *    앞뒤 위치는 모르므로, 특정 지점에 차를 그리면 없는 정보를 주장하게 된다.
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
    static final float CAM_H = 4.6f;
    static final float CAM_BACK = 13.0f;
    static final float HORIZON = 249f;
    static final float NEAR_DEPTH = 11.4f;
    static final float FAR_DEPTH = 190f;

    /** 도로 */
    static final float ROAD_HALF = 4.45f;
    static final float SHOULDER = 0.65f;
    static final float CURB_HEIGHT = 0.13f;
    static final float LANE_PAINT_W = 0.16f;
    static final float EDGE_PAINT_W = 0.20f;
    static final float DASH_PERIOD = 8.0f;
    static final float DASH_ON = 3.0f;

    /** 차량 표현 방식 */
    static final int CAR_SPRITE = 1;   // 사진 스프라이트 (기본)
    static final int CAR_BOX = 2;      // 단색 3D 박스 + 음영
    private static final float CAR_W = 1.86f;
    private static final float CAR_H = 1.46f;
    private static final float CAR_LEN = 4.6f;

    /** BSD 표시 방식 */
    static final int BSD_BAR = 1;      // 막대만
    static final int BSD_SOFT = 2;     // 옅은 면 + 막대 (기본)
    static final int BSD_SOLID = 3;    // 진한 면 + 막대
    private static final float BSD_NEAR = -6f;
    private static final float BSD_FAR = 16f;
    private static final float BSD_INNER = 2.0f;
    private static final float BSD_OUTER = 5.2f;

    /** 건물 */
    private static final float BLOCK = 24f;
    private static final int BLOCK_COUNT = 13;
    private static final float BUILD_NEAR = 14f;
    private static final float BUILD_FAR = 170f;
    private static final float HAZE_START = 95f;

    private static final int SLICES = 30;
    private static final int MAX_PTS = 80;

    // ── 프레임당 재할당을 피하기 위한 버퍼 ────────────────────────────────
    private final float[] pathX = new float[MAX_PTS];
    private final float[] pathY = new float[MAX_PTS];
    private int pathCount;

    private final float[] edgeLX = new float[MAX_PTS];
    private final float[] edgeLY = new float[MAX_PTS];
    private int edgeLCount;
    private final float[] edgeRX = new float[MAX_PTS];
    private final float[] edgeRY = new float[MAX_PTS];
    private int edgeRCount;

    private final float[] pa = new float[2];
    private final float[] pb = new float[2];
    private final float[] pc = new float[2];
    private final float[] pd = new float[2];

    private final float[] lx = new float[SLICES];
    private final float[] ly = new float[SLICES];
    private final float[] rx = new float[SLICES];
    private final float[] ry = new float[SLICES];

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

    private boolean dark;
    private int carStyle = CAR_SPRITE;

    /**
     * lateral_planner 가 최종 경로에 더하는 OffsetTotal (m). 경로 리본에만
     * 적용한다 — 차선·도로경계는 모델 원본이고 오프셋이 안 들어가 있다.
     */
    private float pathOffset = 0f;

    /**
     * liveCalibration pitch (rad). 카메라가 실제로 얼마나 아래를 보는지에
     * 맞춰 수평선을 옮긴다. HORIZON 은 상수지만 실제 EON 카메라는 캘리브에
     * 따라 다르므로, 이 값만큼 보정하면 이온 화면과 세로 구도가 가까워진다.
     */
    private float horizonShift = 0f;

    private final float[] boxX = new float[8];
    private final float[] boxY = new float[8];

    // ── 투영 ──────────────────────────────────────────────────────────────

    boolean project(float x, float y, float z, float[] out) {
        float depth = x + CAM_BACK;
        if (depth < NEAR_DEPTH) {
            return false;
        }
        float inv = FOCAL / depth;
        out[0] = CX - y * inv;
        out[1] = HORIZON + horizonShift + (CAM_H - z) * inv;
        return true;
    }

    static float pxPerMeter(float depth) {
        return FOCAL / Math.max(NEAR_DEPTH, depth);
    }

    // ── 씬 디코딩 ─────────────────────────────────────────────────────────

    void setScene(JSONObject s) {
        pathCount = 0;
        edgeLCount = 0;
        edgeRCount = 0;
        if (s == null) {
            return;
        }
        pathCount = decode(s.optJSONArray("path"), pathX, pathY);

        JSONArray edges = s.optJSONArray("edges");
        if (edges == null) {
            return;
        }
        for (int i = 0; i < edges.length(); i++) {
            JSONObject e = edges.optJSONObject(i);
            if (e == null || e.optDouble("c", 0d) < 0.18d) {
                continue;
            }
            JSONArray pts = e.optJSONArray("p");
            if (pts == null || pts.length() < 2) {
                continue;
            }
            JSONArray first = pts.optJSONArray(0);
            if (first == null) {
                continue;
            }
            boolean leftSide = first.optDouble(1, 0d) > 0d;
            if (leftSide && edgeLCount == 0) {
                edgeLCount = decode(pts, edgeLX, edgeLY);
            } else if (!leftSide && edgeRCount == 0) {
                edgeRCount = decode(pts, edgeRX, edgeRY);
            }
        }
    }

    private static int decode(JSONArray a, float[] xs, float[] ys) {
        if (a == null) {
            return 0;
        }
        int n = 0;
        int limit = Math.min(a.length(), xs.length);
        for (int i = 0; i < limit; i++) {
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
            if (n > 0 && x <= xs[n - 1]) {
                continue;
            }
            xs[n] = x;
            ys[n] = (float) dy;
            n++;
        }
        return n;
    }

    /**
     * Catmull-Rom 보간. 모델 유효구간 밖에서는 기울기를 20m에 걸쳐 0으로
     * 감쇠시킨다 — 정차 중 먼 쪽이 좌우로 쓸리는 것을 막는 핵심.
     */
    private static float sample(float[] xs, float[] ys, int n, float x) {
        if (n == 0) {
            return 0f;
        }
        if (n == 1 || x <= xs[0]) {
            return ys[0];
        }
        int last = n - 1;
        if (x >= xs[last]) {
            float dx = xs[last] - xs[last - 1];
            float slope = dx > 0.01f ? (ys[last] - ys[last - 1]) / dx : 0f;
            slope = Math.max(-0.06f, Math.min(0.06f, slope));
            float over = x - xs[last];
            float decay = Math.max(0f, 1f - over / 20f);
            return ys[last] + slope * over * decay;
        }
        int i = 0;
        int hi = last;
        while (hi - i > 1) {
            int mid = (i + hi) >>> 1;
            if (xs[mid] <= x) {
                i = mid;
            } else {
                hi = mid;
            }
        }
        float y0 = ys[Math.max(0, i - 1)];
        float y1 = ys[i];
        float y2 = ys[i + 1];
        float y3 = ys[Math.min(last, i + 2)];
        float span = xs[i + 1] - xs[i];
        float t = span > 0.001f ? (x - xs[i]) / span : 0f;
        float t2 = t * t;
        float t3 = t2 * t;
        return 0.5f * ((2f * y1)
                + (-y0 + y2) * t
                + (2f * y0 - 5f * y1 + 4f * y2 - y3) * t2
                + (-y0 + 3f * y1 - 3f * y2 + y3) * t3);
    }

    float centerAt(float x) {
        return sample(pathX, pathY, pathCount, x);
    }

    /** 인식된 도로경계가 있으면 그것으로, 없으면 고정 폭으로. */
    private float roadEdgeAt(float x, boolean leftSide) {
        if (leftSide && edgeLCount >= 2) {
            return sample(edgeLX, edgeLY, edgeLCount, x);
        }
        if (!leftSide && edgeRCount >= 2) {
            return sample(edgeRX, edgeRY, edgeRCount, x);
        }
        float half = ROAD_HALF + SHOULDER;
        return centerAt(x) + (leftSide ? half : -half);
    }

    // ── 씬 전체 ───────────────────────────────────────────────────────────

    void draw(Canvas c, Paint p, JSONObject s, boolean enabled,
              Bitmap egoCar, Bitmap otherCar, float odoM,
              int bgColor, int roadTop, int roadBottom, int pathColor,
              int radarInfo, boolean buildings, boolean darkTheme, int bsdStyle,
              int carStyleMode, float offsetTotal, float calibPitch) {
        this.dark = darkTheme;
        this.carStyle = carStyleMode == CAR_BOX ? CAR_BOX : CAR_SPRITE;
        this.pathOffset = Math.max(-1f, Math.min(1f, offsetTotal));
        // pitch 가 아래를 볼수록(양수) 수평선이 화면 아래로 내려간다.
        this.horizonShift = Math.max(-46f, Math.min(46f,
                FOCAL * (float) Math.tan(Math.max(-0.15f, Math.min(0.15f, calibPitch)))));
        setScene(s);

        int save = c.save();
        c.clipRect(LEFT, TOP, RIGHT, BOTTOM);

        int sky = dark ? blend(bgColor, Color.BLACK, 0.35f) : blend(bgColor, Color.WHITE, 0.35f);
        int ground = dark ? blend(bgColor, Color.BLACK, 0.15f) : blend(bgColor, Color.BLACK, 0.10f);

        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(sky);
        c.drawRect(LEFT, TOP, RIGHT, HORIZON, p);
        p.setColor(ground);
        c.drawRect(LEFT, HORIZON, RIGHT, BOTTOM, p);

        drawRoad(c, p, roadTop, roadBottom);
        drawCurb(c, p, true, roadTop);
        drawCurb(c, p, false, roadTop);
        drawMarkings(c, p, s);
        if (enabled) {
            drawPathRibbon(c, p, pathColor);
        }
        if (buildings) {
            drawBuildings(c, p, odoM, sky);
        }
        drawBsd(c, p, s, bsdStyle);
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
            float inv = FOCAL / depth;
            float vy = HORIZON + horizonShift + CAM_H * inv;
            lx[i] = CX - roadEdgeAt(x, true) * inv;
            ly[i] = vy;
            rx[i] = CX - roadEdgeAt(x, false) * inv;
            ry[i] = vy;
        }

        if (roadShader == null || roadShaderTop != topColor || roadShaderBottom != bottomColor) {
            roadShader = new LinearGradient(0f, HORIZON, 0f, BOTTOM, topColor, bottomColor,
                    Shader.TileMode.CLAMP);
            roadShaderTop = topColor;
            roadShaderBottom = bottomColor;
        }

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

    /** 도로 경계를 연석 높이로 세워 평면감을 없앤다. */
    private void drawCurb(Canvas c, Paint p, boolean leftSide, int roadColor) {
        int face = dark ? blend(roadColor, Color.BLACK, 0.35f) : blend(roadColor, Color.BLACK, 0.22f);
        int lip = dark ? blend(roadColor, Color.WHITE, 0.22f) : blend(roadColor, Color.WHITE, 0.45f);
        float invNear = 1f / NEAR_DEPTH;
        float invFar = 1f / (FAR_DEPTH * 0.55f);
        boolean have = false;
        float prevBaseX = 0f, prevBaseY = 0f, prevTopX = 0f, prevTopY = 0f;

        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        for (int i = 0; i < SLICES; i++) {
            float t = i / (SLICES - 1f);
            float depth = 1f / (invNear + (invFar - invNear) * t);
            float x = depth - CAM_BACK;
            float y = roadEdgeAt(x, leftSide);
            if (!project(x, y, 0f, pa) || !project(x, y, CURB_HEIGHT, pb)) {
                have = false;
                continue;
            }
            if (have) {
                poly2.rewind();
                poly2.moveTo(prevBaseX, prevBaseY);
                poly2.lineTo(pa[0], pa[1]);
                poly2.lineTo(pb[0], pb[1]);
                poly2.lineTo(prevTopX, prevTopY);
                poly2.close();
                p.setColor(face);
                c.drawPath(poly2, p);
                p.setColor(lip);
                c.drawRect(Math.min(prevTopX, pb[0]), Math.min(prevTopY, pb[1]) - 1.4f,
                        Math.max(prevTopX, pb[0]), Math.min(prevTopY, pb[1]), p);
            }
            prevBaseX = pa[0];
            prevBaseY = pa[1];
            prevTopX = pb[0];
            prevTopY = pb[1];
            have = true;
        }
    }

    // ── 차선 / 도로 경계 ──────────────────────────────────────────────────

    private void drawMarkings(Canvas c, Paint p, JSONObject s) {
        if (s == null) {
            return;
        }
        int edgeColor = dark ? Color.rgb(120, 132, 146) : Color.rgb(148, 157, 163);
        JSONArray edges = s.optJSONArray("edges");
        if (edges != null) {
            for (int i = 0; i < edges.length(); i++) {
                JSONObject e = edges.optJSONObject(i);
                if (e == null || e.optDouble("c", 0d) < 0.18d) {
                    continue;
                }
                polyline(c, p, e.optJSONArray("p"), edgeColor, EDGE_PAINT_W, false, 255);
            }
        }
        JSONArray lanes = s.optJSONArray("lanes");
        if (lanes == null) {
            return;
        }
        int plain = dark ? Color.rgb(214, 222, 230) : Color.rgb(248, 250, 250);
        int ego = dark ? Color.rgb(246, 206, 92) : Color.rgb(238, 196, 70);
        for (int i = 0; i < lanes.length(); i++) {
            JSONObject lane = lanes.optJSONObject(i);
            if (lane == null) {
                continue;
            }
            double conf = lane.optDouble("c", 1d);
            int alpha = (int) Math.max(70d, Math.min(255d, 90d + conf * 170d));
            boolean isEgo = (lanes.length() >= 3) && (i == 1 || i == 2);
            polyline(c, p, lane.optJSONArray("p"), isEgo ? ego : plain, LANE_PAINT_W, true, alpha);
        }
    }

    private void polyline(Canvas c, Paint p, JSONArray pts, int color, float widthM,
                          boolean dashed, int alpha) {
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
            quad(c, p, pa, pb,
                    Math.max(1.1f, widthM * pxPerMeter(x1 + CAM_BACK)),
                    Math.max(1.0f, widthM * pxPerMeter(x2 + CAM_BACK)));
        }
        p.setAlpha(255);
    }

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
        final float half = 0.9f;
        final int steps = 44;
        float reach = Math.min(FAR_DEPTH * 0.62f, pathX[pathCount - 1] + 10f);
        poly.rewind();
        boolean started = false;
        int used = 0;
        for (int i = 0; i < steps; i++) {
            float x = reach * i / (steps - 1f);
            if (!project(x, centerAt(x) + pathOffset + half, 0f, pa)) {
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
        for (int i = steps - 1; i >= 0; i--) {
            float x = reach * i / (steps - 1f);
            if (project(x, centerAt(x) + pathOffset - half, 0f, pa)) {
                poly.lineTo(pa[0], pa[1]);
            }
        }
        poly.close();

        if (ribbonShader == null || ribbonShaderColor != color) {
            ribbonShader = new LinearGradient(0f, HORIZON, 0f, BOTTOM,
                    Color.argb(40, Color.red(color), Color.green(color), Color.blue(color)),
                    Color.argb(190, Color.red(color), Color.green(color), Color.blue(color)),
                    Shader.TileMode.CLAMP);
            ribbonShaderColor = color;
        }
        p.setStyle(Paint.Style.FILL);
        p.setShader(ribbonShader);
        c.drawPath(poly, p);
        p.setShader(null);

        p.setStyle(Paint.Style.STROKE);
        p.setStrokeWidth(2.8f);
        p.setStrokeJoin(Paint.Join.ROUND);
        p.setColor(Color.rgb(Math.min(255, Color.red(color) + 60),
                Math.min(255, Color.green(color) + 50),
                Math.min(255, Color.blue(color) + 20)));
        c.drawPath(poly, p);
        p.setStyle(Paint.Style.FILL);
    }

    // ── BSD 경고 띠 ───────────────────────────────────────────────────────

    private void drawBsd(Canvas c, Paint p, JSONObject s, int style) {
        if (s == null) {
            return;
        }
        if (s.optBoolean("leftBsd", false)) {
            bsdBand(c, p, 1, style);
        }
        if (s.optBoolean("rightBsd", false)) {
            bsdBand(c, p, -1, style);
        }
    }

    /**
     * 옆차선 전체를 띠로 칠한다. 특정 지점에 차를 그리지 않는 이유는
     * openpilot 이 옆차의 앞뒤 위치를 알려주지 않기 때문이다.
     */
    private void bsdBand(Canvas c, Paint p, int side, int style) {
        final int steps = 16;
        int fillAlpha = style == BSD_SOLID ? 96 : style == BSD_SOFT ? 48 : 0;

        if (fillAlpha > 0) {
            poly.rewind();
            boolean started = false;
            for (int i = 0; i < steps; i++) {
                float x = BSD_NEAR + (BSD_FAR - BSD_NEAR) * i / (steps - 1f);
                float base = centerAt(Math.max(0f, x));
                if (!project(x, base + side * BSD_INNER, 0f, pa)) {
                    continue;
                }
                if (started) {
                    poly.lineTo(pa[0], pa[1]);
                } else {
                    poly.moveTo(pa[0], pa[1]);
                    started = true;
                }
            }
            if (!started) {
                return;
            }
            for (int i = steps - 1; i >= 0; i--) {
                float x = BSD_NEAR + (BSD_FAR - BSD_NEAR) * i / (steps - 1f);
                float base = centerAt(Math.max(0f, x));
                if (project(x, base + side * BSD_OUTER, 0f, pa)) {
                    poly.lineTo(pa[0], pa[1]);
                }
            }
            poly.close();
            p.setShader(null);
            p.setStyle(Paint.Style.FILL);
            p.setColor(Color.argb(fillAlpha, 255, 168, 40));
            c.drawPath(poly, p);
        }

        // 안쪽 경계 막대는 어느 방식에서나 그린다.
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(Color.argb(238, 255, 190, 70));
        boolean have = false;
        for (int i = 0; i < steps; i++) {
            float x = BSD_NEAR + (BSD_FAR - BSD_NEAR) * i / (steps - 1f);
            float base = centerAt(Math.max(0f, x));
            if (!project(x, base + side * BSD_INNER, 0f, pb)) {
                have = false;
                continue;
            }
            if (have) {
                quad(c, p, pa, pb, 7f, 6f);
            }
            pa[0] = pb[0];
            pa[1] = pb[1];
            have = true;
        }
    }

    // ── 건물 ──────────────────────────────────────────────────────────────

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
                    continue;
                }
                float gap = 1.6f + rnd(k, salt + 1) * 3.2f;
                float width = 7f + rnd(k, salt + 2) * 9f;
                float depth = 8f + rnd(k, salt + 3) * 10f;
                float height = 4f + rnd(k, salt + 4) * 7f;
                int tone = dark ? 78 - (int) (rnd(k, salt + 5) * 22f)
                        : 196 - (int) (rnd(k, salt + 5) * 34f);
                building(c, p, d, d + depth, side, gap, width, height, tone, fogColor);
            }
        }
    }

    private void building(Canvas c, Paint p, float d0, float d1, int side,
                          float gap, float width, float height, int tone, int fogColor) {
        float inner = roadEdgeAt(d0, side > 0) + side * gap;
        float outer = inner + side * width;

        if (!project(d0, inner, 0f, pa) || !project(d0, outer, 0f, pb)
                || !project(d0, outer, height, pc) || !project(d0, inner, height, pd)) {
            return;
        }
        float minX = Math.min(Math.min(pa[0], pb[0]), Math.min(pc[0], pd[0]));
        float maxX = Math.max(Math.max(pa[0], pb[0]), Math.max(pc[0], pd[0]));
        if (maxX < LEFT - 40f || minX > RIGHT + 40f) {
            return;
        }

        float fog = d0 <= HAZE_START ? 0f
                : Math.min(0.82f, (d0 - HAZE_START) / (BUILD_FAR - HAZE_START) * 0.95f);
        int front = blend(Color.rgb(tone, Math.min(255, tone + 4), Math.min(255, tone + 11)),
                fogColor, fog);
        int flank = blend(Color.rgb(Math.max(24, tone - 34), Math.max(28, tone - 29),
                Math.max(34, tone - 21)), fogColor, fog);

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

        float faceW = Math.abs(pb[0] - pa[0]);
        float faceH = Math.abs(pa[1] - pd[1]);
        if (faceW < 20f || faceH < 26f || fog > 0.55f) {
            return;
        }
        int cols = Math.max(2, Math.min(4, (int) (faceW / 14f)));
        int rows = Math.max(2, Math.min(5, (int) (faceH / 16f)));
        p.setColor(blend(dark ? Color.rgb(150, 176, 110) : Color.rgb(122, 152, 176), fogColor, fog));
        for (int r = 0; r < rows; r++) {
            for (int q = 0; q < cols; q++) {
                float xa = lerp(pa[0], pb[0], (q + 0.30f) / cols);
                float xb = lerp(pa[0], pb[0], (q + 0.70f) / cols);
                float ya = lerp(pa[1], pd[1], (r + 0.28f) / rows);
                float yb = lerp(pa[1], pd[1], (r + 0.66f) / rows);
                rect.set(Math.min(xa, xb), Math.min(ya, yb), Math.max(xa, xb), Math.max(ya, yb));
                c.drawRect(rect, p);
            }
        }
    }

    // ── 차량 ──────────────────────────────────────────────────────────────

    private void drawVehicles(Canvas c, Paint p, JSONObject s, Bitmap egoCar, Bitmap otherCar,
                              int radarInfo) {
        if (s == null) {
            return;
        }
        JSONObject lead = s.optJSONObject("lead");
        JSONObject lead2 = s.optJSONObject("lead2");
        float d1 = lead == null ? -1f : (float) lead.optDouble("d", -1d);
        float d2 = lead2 == null ? -1f : (float) lead2.optDouble("d", -1d);

        if (d2 > d1) {
            car(c, p, otherCar, lead2, 205);
            car(c, p, otherCar, lead, 245);
        } else {
            car(c, p, otherCar, lead, 245);
            car(c, p, otherCar, lead2, 205);
        }
        if (carStyle == CAR_BOX) {
            boxCar(c, p, 0f, 0f, dark ? Color.rgb(74, 130, 214) : Color.rgb(86, 132, 200), true, 255);
        } else {
            billboard(c, p, egoCar, 0f, 0f, 1.95f, egoRoll(), 255);
        }

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
        if (carStyle == CAR_BOX) {
            boxCar(c, p, d, y, dark ? Color.rgb(126, 134, 146) : Color.rgb(112, 122, 136),
                    false, alpha);
        } else {
            billboard(c, p, bmp, d, y, 1.9f, 0f, alpha);
        }
    }

    /**
     * 단색 3D 박스 차량. 뒷면 / 윗면 / 보이는 옆면 세 면에 명암을 준다.
     * 카메라가 차 지붕보다 위(4.6m)에 있으므로 윗면이 보이는 게 맞다.
     */
    private void boxCar(Canvas c, Paint p, float x, float y, int body, boolean ego, int alpha) {
        float rear = Math.max(x - CAR_LEN / 2f, NEAR_DEPTH - CAM_BACK + 0.2f);
        float front = Math.max(x + CAR_LEN / 2f, rear + 0.8f);
        float yl = y + CAR_W / 2f;
        float yr = y - CAR_W / 2f;

        // 0 rl 1 rr 2 rrTop 3 rlTop 4 fl 5 fr 6 frTop 7 flTop
        float[][] src = {
                {rear, yl, 0f}, {rear, yr, 0f}, {rear, yr, CAR_H}, {rear, yl, CAR_H},
                {front, yl, 0f}, {front, yr, 0f}, {front, yr, CAR_H}, {front, yl, CAR_H},
        };
        for (int i = 0; i < 8; i++) {
            if (!project(src[i][0], src[i][1], src[i][2], pa)) {
                return;
            }
            boxX[i] = pa[0];
            boxY[i] = pa[1];
        }

        float depth = x + CAM_BACK;
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);

        // 접지 그림자
        if (project(x, y, 0f, pa)) {
            float rx0 = CAR_W * 0.62f * FOCAL / depth;
            float ry0 = Math.max(1.2f, rx0 * CAM_H / depth);
            p.setColor(Color.argb(Math.min(120, alpha / 2), 6, 10, 14));
            rect.set(pa[0] - rx0, pa[1] - ry0, pa[0] + rx0, pa[1] + ry0);
            c.drawOval(rect, p);
        }

        p.setAlpha(alpha);
        // 보이는 옆면. 카메라가 Y=0 에 있으므로 차가 왼쪽(y>0)이면 왼쪽 면,
        // 오른쪽이면 오른쪽 면이 보인다.
        p.setColor(blend(body, Color.BLACK, 0.45f));
        if (y > 0f) {
            face(c, p, 0, 4, 7, 3);   // rl - fl - flTop - rlTop
        } else {
            face(c, p, 1, 5, 6, 2);   // rr - fr - frTop - rrTop
        }

        // 윗면
        p.setColor(blend(body, Color.WHITE, 0.18f));
        face(c, p, 3, 2, 6, 7);

        // 뒷면
        p.setColor(body);
        face(c, p, 0, 1, 2, 3);

        // 후미등 (자차는 생략)
        if (!ego) {
            p.setColor(Color.rgb(232, 62, 58));
            float lampR = Math.max(1.2f, CAR_W * 0.13f * FOCAL / depth);
            for (float f : new float[]{0.24f, 0.76f}) {
                float lxp = lerp(boxX[0], boxX[1], f);
                float lyp = lerp(boxY[0], boxY[3], 0.62f);
                rect.set(lxp - lampR, lyp - lampR * 0.55f, lxp + lampR, lyp + lampR * 0.55f);
                c.drawOval(rect, p);
            }
        }
        p.setAlpha(255);
    }

    private void face(Canvas c, Paint p, int a, int b, int d0, int e) {
        poly2.rewind();
        poly2.moveTo(boxX[a], boxY[a]);
        poly2.lineTo(boxX[b], boxY[b]);
        poly2.lineTo(boxX[d0], boxY[d0]);
        poly2.lineTo(boxX[e], boxY[e]);
        poly2.close();
        c.drawPath(poly2, p);
    }

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

    private float egoRoll() {
        if (pathCount < 2) {
            return 0f;
        }
        float roll = (float) Math.toDegrees(Math.atan2(centerAt(2f) - centerAt(14f), 12f)) * 0.45f;
        return Math.max(-16f, Math.min(16f, roll));
    }

    private void billboard(Canvas c, Paint p, Bitmap b, float x, float y,
                           float widthM, float rollDeg, int alpha) {
        if (b == null || b.isRecycled() || !project(x, y, 0f, pa)) {
            return;
        }
        float depth = x + CAM_BACK;
        float w = widthM * FOCAL / depth;
        if (w < 6f) {
            return;
        }
        float h = b.getHeight() * w / b.getWidth();

        int save = c.save();
        c.translate(pa[0], pa[1]);
        if (rollDeg != 0f) {
            c.rotate(rollDeg);
        }
        float rx0 = w * 0.52f;
        float ry0 = Math.max(1.5f, rx0 * CAM_H / depth);
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(Color.argb(Math.min(110, alpha / 2), 8, 12, 16));
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
