package ai.comma.naverhud;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.view.View;

import java.util.Locale;

/**
 * 네이버 6.12+ 용 HUD 차로 띠. 6.12 는 차로 표시를 Compose 로 그려 예전 NaviLaneControlView 가
 * 없다(폰 화면 캡처·오프스크린 그리기 모두 불가). 차로 데이터로 직접 그린 이 View 를 기존
 * lane_bottom 경로(NaverViewImage: 잘라서 530x84 칸에 맞춤)로 보내므로 HUD 위치·크기는 그대로다.
 *
 * 왼쪽 → 오른쪽 차로 칸, 권장 차로는 파란 칸에 흰 화살표, 나머지는 회색 화살표.
 * 화살표 비트(NaverCodes.laneDirs): 1 직진, 2 좌, 4 우, 8 유턴. 위에 남은 거리.
 */
final class NaverLaneStrip extends View {
    private static final int CELL_W = 96;
    private static final int CELL_H = 128;
    private static final int GAP = 6;
    private static final int TEXT_H = 52;
    static final int MAX_LANES = 8;

    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint stroke = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path path = new Path();
    private final RectF rect = new RectF();
    private int count;
    private final int[] dirs = new int[MAX_LANES];
    private final boolean[] recommended = new boolean[MAX_LANES];
    private double distance = -1;

    NaverLaneStrip(Context context) {
        super(context);
        stroke.setStyle(Paint.Style.STROKE);
        stroke.setStrokeCap(Paint.Cap.ROUND);
        stroke.setStrokeJoin(Paint.Join.ROUND);
        text.setColor(Color.WHITE);
        text.setTextSize(42f);
        text.setFakeBoldText(true);
        text.setTextAlign(Paint.Align.CENTER);
    }

    /** 차로 수·방향·권장 여부·거리(m, 모르면 -1). 바뀌었으면 true. */
    boolean set(int n, int[] laneDirs, boolean[] laneRecommended, double meters) {
        n = Math.max(0, Math.min(MAX_LANES, n));
        boolean changed = n != count || Math.round(meters / 10.0) != Math.round(distance / 10.0);
        for (int i = 0; i < n; i++) {
            changed |= dirs[i] != laneDirs[i] || recommended[i] != laneRecommended[i];
            dirs[i] = laneDirs[i];
            recommended[i] = laneRecommended[i];
        }
        count = n;
        distance = meters;
        if (changed) invalidate();
        return changed;
    }

    int widthPx() {
        return Math.max(1, count) * (CELL_W + GAP) + GAP;
    }

    int heightPx() {
        return TEXT_H + CELL_H + 2 * GAP;
    }

    @Override protected void onDraw(Canvas c) {
        if (count <= 0) return;
        int w = widthPx();
        float top = TEXT_H;
        // 바탕(검은 반투명 둥근 띠)
        fill.setStyle(Paint.Style.FILL);
        fill.setColor(Color.argb(220, 20, 20, 24));
        rect.set(0, top, w, top + CELL_H + 2 * GAP);
        c.drawRoundRect(rect, 18f, 18f, fill);
        for (int i = 0; i < count; i++) {
            float x0 = GAP + i * (CELL_W + GAP);
            rect.set(x0, top + GAP, x0 + CELL_W, top + GAP + CELL_H);
            fill.setColor(recommended[i] ? Color.rgb(32, 120, 255) : Color.argb(255, 52, 52, 58));
            c.drawRoundRect(rect, 12f, 12f, fill);
            int color = recommended[i] ? Color.WHITE : Color.rgb(150, 150, 156);
            drawArrows(c, x0 + CELL_W / 2f, top + GAP, dirs[i], color);
        }
        if (distance >= 0) {
            String label = distance >= 1000 ? String.format(Locale.US, "%.1fkm", distance / 1000.0)
                    : String.format(Locale.US, "%dm", Math.round(distance / 10.0) * 10);
            c.drawText(label, w / 2f, TEXT_H - 10f, text);
        }
    }

    /** 칸 하나: 아래 가운데에서 올라가는 줄기 + 방향별 화살촉. */
    private void drawArrows(Canvas c, float cx, float top, int d, int color) {
        if (d == 0) d = 1;
        stroke.setColor(color);
        stroke.setStrokeWidth(10f);
        fill.setColor(color);
        float bottom = top + CELL_H - 16f;
        float mid = top + CELL_H * 0.55f;
        float head = top + 22f;
        path.reset();
        path.moveTo(cx, bottom);
        path.lineTo(cx, mid);
        c.drawPath(path, stroke);
        if ((d & 1) != 0) {
            line(c, cx, mid, cx, head + 14f);
            arrowHead(c, cx, head, 0f, -1f);
        }
        if ((d & 2) != 0) {
            curve(c, cx, mid, cx - 30f, mid - 26f);
            arrowHead(c, cx - 36f, mid - 26f, -1f, 0f);
        }
        if ((d & 4) != 0) {
            curve(c, cx, mid, cx + 30f, mid - 26f);
            arrowHead(c, cx + 36f, mid - 26f, 1f, 0f);
        }
        if ((d & 8) != 0) {
            path.reset();
            path.moveTo(cx, mid);
            path.lineTo(cx, head + 18f);
            path.quadTo(cx, head - 2f, cx - 18f, head - 2f);
            path.quadTo(cx - 36f, head - 2f, cx - 36f, head + 18f);
            path.lineTo(cx - 36f, mid - 10f);
            c.drawPath(path, stroke);
            arrowHead(c, cx - 36f, mid - 4f, 0f, 1f);
        }
    }

    private void line(Canvas c, float x0, float y0, float x1, float y1) {
        c.drawLine(x0, y0, x1, y1, stroke);
    }

    private void curve(Canvas c, float x0, float y0, float x1, float y1) {
        path.reset();
        path.moveTo(x0, y0);
        path.quadTo(x0, y1, x1, y1);
        c.drawPath(path, stroke);
    }

    /** (x, y) 끝점, (dx, dy) 진행 방향 단위벡터. */
    private void arrowHead(Canvas c, float x, float y, float dx, float dy) {
        float len = 22f, half = 16f;
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
