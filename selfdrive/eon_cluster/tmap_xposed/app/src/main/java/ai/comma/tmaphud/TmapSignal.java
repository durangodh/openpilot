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
    private static final int W = 132;   // 신호등 셀 3개 + 여백
    private static final int H = 96;
    // HUD 는 신호등 그림을 302x192 칸에 비율을 지켜 꽉 채운다. 132x96 그대로 보내면 2배
    // (264x192)로 커져 지도를 크게 가렸다(2026-10-06, 카카오와 같은 문제). 칸과 같은 크기의
    // 투명 캔버스에 CONTENT_SCALE 배로 오른쪽·세로 가운데에 그려, HUD 에서 그대로 보이게 한다.
    private static final int CANVAS_W = 302;
    private static final int CANVAS_H = 192;
    private static final float CONTENT_SCALE = 1.25f;   // HUD 에서 약 165x120

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
        if (color <= NONE || color > GREEN) {
            clear();
            return;
        }
        String key = color + ":" + remainSec;
        if (key.equals(lastKey)) return;   // 바뀔 때만 보냄(부하 절감)
        lastKey = key;
        try {
            byte[] png = render(color, remainSec);
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

    private byte[] render(int color, int remainSec) {
        Bitmap bmp = Bitmap.createBitmap(CANVAS_W, CANVAS_H, Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(bmp);
        c.translate(CANVAS_W - W * CONTENT_SCALE, (CANVAS_H - H * CONTENT_SCALE) / 2f);
        c.scale(CONTENT_SCALE, CONTENT_SCALE);
        // 둥근 검정 배경(가로형 신호등)
        c.drawRoundRect(new RectF(2, 2, W - 2, H - 2), 18, 18, bg);

        int r = 20;
        int cy = 34;
        int[] cx = {34, 66, 98};               // 빨강·노랑·초록 위치
        int[] on = {0xFFFF3B30, 0xFFFFCC00, 0xFF34C759};
        int[] off = {0xFF3A1412, 0xFF3A3410, 0xFF123A1C};
        for (int i = 0; i < 3; i++) {
            boolean active = (i == color - 1);
            fill.setColor(active ? on[i] : off[i]);
            c.drawCircle(cx[i], cy, r, fill);
            if (active) {
                fill.setColor(0x55FFFFFF);
                c.drawCircle(cx[i] - 5, cy - 6, 5, fill);  // 작은 하이라이트
            }
        }
        // 잔여초: 현재 켜진 색으로 크게
        if (remainSec >= 0 && remainSec < 200) {
            text.setColor(on[color - 1]);
            text.setTextSize(34);
            c.drawText(String.valueOf(remainSec), W / 2f, H - 12, text);
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        bmp.compress(Bitmap.CompressFormat.PNG, 100, out);
        bmp.recycle();
        return out.toByteArray();
    }
}
