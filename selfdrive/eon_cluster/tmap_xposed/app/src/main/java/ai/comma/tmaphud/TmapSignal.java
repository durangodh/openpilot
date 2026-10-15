package ai.comma.tmaphud;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Typeface;

import java.io.ByteArrayOutputStream;

/**
 * 티맵 C-ITS 신호등(색 + 잔여초)을 원형 신호등 PNG 로 그려 carrot_navi_server 의
 * traffic_signal 오버레이로 보낸다. 카카오/네이버 모듈과 같은 그림이라 HUD 앱은
 * 그대로 SIG1 자산으로 얹는다(HUD 수정 불필요).
 */
final class TmapSignal {

    private static final int NONE = TmapJson.COLOR_NONE, GREEN = TmapJson.COLOR_GREEN;
    private static final int W = 250;   // 가로 알약(등 2개 + 화살표 + 잔여초)
    private static final int H = 72;
    // HUD 는 신호등 그림을 302x192 칸에 비율을 지켜 꽉 채우므로, 칸과 같은 크기의 투명
    // 캔버스 오른쪽·세로 가운데에 원본 크기로 그려 HUD 에서 그 크기 그대로 보이게 한다.
    private static final int CANVAS_W = 302;
    private static final int CANVAS_H = 192;

    private final TmapNaviClient client;
    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint bg = new Paint(Paint.ANTI_ALIAS_FLAG);
    private long seq = 0;
    private boolean lastWasClear = true;
    private String lastKey = "";

    TmapSignal(TmapNaviClient client) {
        this.client = client;
        text.setColor(Color.WHITE);
        text.setTextAlign(Paint.Align.CENTER);
        text.setTypeface(Typeface.create(Typeface.DEFAULT, Typeface.BOLD));
        bg.setColor(0xE6101216);
    }

    /** color: TmapJson.COLOR_*(1 빨강·2 노랑·3 초록). remainSec<0 이면 숫자 숨김. */
    synchronized void publish(int color, int remainSec) {
        publish(color, remainSec, false);
    }

    /** leftGo: 좌회전 신호가 초록이면 화살표를 초록으로. */
    synchronized void publish(int color, int remainSec, boolean leftGo) {
        if (color <= NONE || color > GREEN) {
            clear();
            return;
        }
        String key = color + ":" + remainSec + ":" + leftGo;
        if (key.equals(lastKey)) return;   // 바뀔 때만 보냄(부하 절감)
        lastKey = key;
        try {
            byte[] png = render(color, remainSec, leftGo);
            client.sendSignal(Cnv2.frame(Cnv2.TYPE_IMAGE, Cnv2.FORMAT_PNG, seq++, png, CANVAS_W, CANVAS_H));
            lastWasClear = false;
        } catch (Throwable t) {
            TmapHudLog.ex("signal render", t);
        }
    }

    synchronized void clear() {
        if (lastWasClear) return;
        lastWasClear = true;
        lastKey = "";
        client.sendSignal(Cnv2.frame(Cnv2.TYPE_CLEAR, Cnv2.FORMAT_PNG, seq++, null, 0, 0));
    }

    private byte[] render(int color, int remainSec, boolean leftGo) {
        Bitmap bmp = Bitmap.createBitmap(CANVAS_W, CANVAS_H, Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(bmp);
        // 카카오 원본처럼 가로 알약: [빨강/노랑 등] [좌회전 화살표] [초록 등] 잔여초.
        c.translate(CANVAS_W - W, (CANVAS_H - H) / 2f);
        c.drawRoundRect(new RectF(0, 0, W, H), H / 2f, H / 2f, bg);
        int[] on = {0xFFFF3B30, 0xFFFFCC00, 0xFF34C759};
        int offColor = 0xFF4A4D52;
        float cy = H / 2f, r = 24;
        fill.setStyle(Paint.Style.FILL);
        fill.setColor(color == 1 || color == 2 ? on[color - 1] : offColor);
        c.drawCircle(40, cy, r, fill);
        // 좌회전 화살표(좌회전 신호가 초록일 때만 초록)
        fill.setColor(leftGo ? on[2] : offColor);
        android.graphics.Path arrow = new android.graphics.Path();
        arrow.moveTo(72, cy);
        arrow.lineTo(88, cy - 15);
        arrow.lineTo(88, cy - 6);
        arrow.lineTo(110, cy - 6);
        arrow.lineTo(110, cy + 6);
        arrow.lineTo(88, cy + 6);
        arrow.lineTo(88, cy + 15);
        arrow.close();
        c.drawPath(arrow, fill);
        fill.setColor(color == 3 ? on[2] : offColor);
        c.drawCircle(142, cy, r, fill);
        if (remainSec >= 0 && remainSec < 1000) {
            text.setColor(on[color - 1]);
            text.setTextSize(40);
            c.drawText(String.valueOf(remainSec), 210, cy + 14, text);
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        bmp.compress(Bitmap.CompressFormat.PNG, 100, out);
        bmp.recycle();
        return out.toByteArray();
    }
}
