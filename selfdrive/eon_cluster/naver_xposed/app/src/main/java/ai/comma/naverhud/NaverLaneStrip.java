package ai.comma.naverhud;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.DashPathEffect;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.view.View;

import java.util.Locale;

/**
 * 네이버 6.12+ 용 HUD 차로 띠. 6.12 는 차로 표시를 Compose 로 그려 예전 NaviLaneControlView 가
 * 없다(폰 화면 캡처·오프스크린 그리기 모두 불가). 폰 네이버 차로 상자와 같은 모양으로 직접 그린
 * 이 View 를 기존 lane_bottom 경로(NaverViewImage: 잘라서 530x84 칸에 맞춤)로 보내므로 HUD
 * 위치·크기는 그대로다(2026-11-21 실차 사진 기준: 폰 상자 3차로 ≈ 200x85).
 *
 * 검은 둥근 상자 안에 왼쪽 → 오른쪽 차로, 차로 사이 점선. 화살표는 회색, 권장 차로에서 가야 할
 * 방향(active)만 초록. 상자 오른쪽에 남은 거리(30m, 1.2km).
 * 비트(NaverBridge.markingBits): 1 직진, 2 좌, 4 우, 8 유턴.
 */
final class NaverLaneStrip extends View {
    private static final int CELL_W = 66;
    // Square cells like Naver's own lane box (2026-11-22: tall cells made the arrows look long).
    private static final int CELL_H = 64;
    private static final int TEXT_GAP = 14;
    static final int MAX_LANES = 8;
    private static final int GREEN = Color.rgb(52, 209, 110);
    // Arrows other than the ones to take: solid white like Naver's own box (2026-11-22; was gray).
    private static final int GRAY = Color.rgb(255, 255, 255);

    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint stroke = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint dash = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textEdge = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path path = new Path();
    private final RectF rect = new RectF();
    private int count;
    private final int[] dirs = new int[MAX_LANES];
    private final int[] active = new int[MAX_LANES];
    private String label = "";

    NaverLaneStrip(Context context) {
        super(context);
        stroke.setStyle(Paint.Style.STROKE);
        stroke.setStrokeCap(Paint.Cap.ROUND);
        stroke.setStrokeJoin(Paint.Join.ROUND);
        stroke.setStrokeWidth(7f);
        dash.setStyle(Paint.Style.STROKE);
        dash.setStrokeWidth(2f);
        dash.setColor(Color.argb(150, 255, 255, 255));
        dash.setPathEffect(new DashPathEffect(new float[]{6f, 6f}, 0f));
        text.setColor(Color.WHITE);
        text.setTextSize(34f);
        text.setFakeBoldText(true);
        textEdge.setColor(Color.argb(230, 0, 0, 0));
        textEdge.setTextSize(34f);
        textEdge.setFakeBoldText(true);
        textEdge.setStyle(Paint.Style.STROKE);
        textEdge.setStrokeWidth(6f);
        textEdge.setStrokeJoin(Paint.Join.ROUND);
    }

    /** 차로 수, 화살표 모양 비트, 초록으로 칠할 방향 비트, 거리(m, 모르면 -1). */
    void set(int n, int[] laneDirs, int[] laneActive, double meters) {
        n = Math.max(0, Math.min(MAX_LANES, n));
        for (int i = 0; i < n; i++) {
            dirs[i] = laneDirs[i];
            active[i] = laneActive[i];
        }
        count = n;
        label = meters < 0 ? "" : meters >= 1000 ? String.format(Locale.US, "%.1fkm", meters / 1000.0)
                : String.format(Locale.US, "%dm", Math.max(0, Math.round(meters / 10.0) * 10));
        invalidate();
    }

    int widthPx() {
        int box = Math.max(1, count) * CELL_W;
        return label.isEmpty() ? box : box + TEXT_GAP + (int) Math.ceil(text.measureText(label)) + 4;
    }

    int heightPx() {
        return CELL_H;
    }

    @Override protected void onDraw(Canvas c) {
        if (count <= 0) return;
        float boxW = count * CELL_W;
        fill.setStyle(Paint.Style.FILL);
        fill.setColor(Color.argb(235, 34, 34, 36));
        rect.set(0, 0, boxW, CELL_H);
        c.drawRoundRect(rect, 10f, 10f, fill);
        for (int i = 1; i < count; i++) {
            float x = i * CELL_W;
            c.drawLine(x, 10f, x, CELL_H - 10f, dash);
        }
        for (int i = 0; i < count; i++) {
            drawLane(c, i * CELL_W + CELL_W / 2f, dirs[i], active[i]);
        }
        if (!label.isEmpty()) {
            Paint.FontMetrics fm = text.getFontMetrics();
            float y = CELL_H / 2f - (fm.ascent + fm.descent) / 2f;
            float x = boxW + TEXT_GAP;
            c.drawText(label, x, y, textEdge);
            c.drawText(label, x, y, text);
        }
    }

    /** 차로 하나: 아래에서 올라오는 줄기 + 모양별 갈래. 가야 할 갈래만 초록. */
    private void drawLane(Canvas c, float cx, int d, int on) {
        if (d == 0) {   // 접힌 차로·하이패스 등: 모양 없이 짧은 회색 줄
            stroke.setColor(GRAY);
            c.drawLine(cx - 8f, CELL_H / 2f, cx + 8f, CELL_H / 2f, stroke);
            return;
        }
        // Short, compact arrows like the phone's: stem from 80 % to 50 % of the cell, heads near 20 %.
        float bottom = CELL_H * 0.82f;
        float mid = CELL_H * 0.52f;
        float head = CELL_H * 0.16f;
        // 줄기: 가야 할 갈래가 하나라도 있으면 초록
        stroke.setColor(on != 0 ? GREEN : GRAY);
        c.drawLine(cx, bottom, cx, mid, stroke);
        // 회색 갈래를 먼저, 초록 갈래를 나중에(위에) 그린다.
        for (int pass = 0; pass < 2; pass++) {
            boolean green = pass == 1;
            for (int bit : new int[]{8, 2, 4, 1}) {
                if ((d & bit) == 0 || ((on & bit) != 0) != green) continue;
                int color = green ? GREEN : GRAY;
                stroke.setColor(color);
                fill.setColor(color);
                if (bit == 1) {
                    c.drawLine(cx, mid, cx, head + 10f, stroke);
                    arrowHead(c, cx, head, 0f, -1f);
                } else if (bit == 2) {
                    curve(c, cx, mid, cx - 14f, mid - 12f);
                    arrowHead(c, cx - 22f, mid - 12f, -1f, 0f);
                } else if (bit == 4) {
                    curve(c, cx, mid, cx + 14f, mid - 12f);
                    arrowHead(c, cx + 22f, mid - 12f, 1f, 0f);
                } else {
                    path.reset();
                    path.moveTo(cx, mid);
                    path.lineTo(cx, head + 10f);
                    path.quadTo(cx, head - 2f, cx - 11f, head - 2f);
                    path.quadTo(cx - 22f, head - 2f, cx - 22f, head + 10f);
                    path.lineTo(cx - 22f, mid - 8f);
                    c.drawPath(path, stroke);
                    arrowHead(c, cx - 22f, mid - 2f, 0f, 1f);
                }
            }
        }
    }

    private void curve(Canvas c, float x0, float y0, float x1, float y1) {
        path.reset();
        path.moveTo(x0, y0);
        path.quadTo(x0, y1, x1, y1);
        c.drawPath(path, stroke);
    }

    /** (x, y) 끝점, (dx, dy) 진행 방향 단위벡터. */
    private void arrowHead(Canvas c, float x, float y, float dx, float dy) {
        float len = 13f, half = 9f;
        float bx = x - dx * len, by = y - dy * len;
        path.reset();
        path.moveTo(x, y);
        path.lineTo(bx - dy * half, by + dx * half);
        path.lineTo(bx + dy * half, by - dx * half);
        path.close();
        fill.setStyle(Paint.Style.FILL);
        c.drawPath(path, fill);
    }
}
