package ai.comma.kakaohud;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/**
 * 카카오 C-ITS 신호등(색 + 잔여초)을 원형 신호등 PNG 로 그려, carrot_navi_server
 * 의 traffic_signal 오버레이로 보낸다. HUD 앱은 이 PNG 를 SIG1 자산으로 받아
 * 지도 패널 위에 그대로 얹는다(HUD 수정 불필요).
 *
 * 전송 포맷은 서버 _binary_payload 가 기대하는 40바이트 CNV2 헤더 + PNG 본문.
 *   message_type 1 = 이미지, 4 = clear(신호 사라짐).
 */
final class KakaoSignal {

    private static final int W = 132;   // 신호등 셀 3개 + 여백
    private static final int H = 96;
    // HUD 는 신호등 그림을 302x192 칸에 비율을 지켜 꽉 채운다. 132x96 그대로 보내면 2배
    // (264x192)로 커져 지도를 크게 가렸다(2026-10-06). 칸과 같은 크기의 투명 캔버스에
    // CONTENT_SCALE 배로 오른쪽·세로 가운데에 그려, HUD 에서 그 크기 그대로 보이게 한다.
    private static final int CANVAS_W = 302;
    private static final int CANVAS_H = 192;
    private static final float CONTENT_SCALE = 1.0f;    // HUD 에서 원본 크기 132x96
    private static final int PROTOCOL_VERSION = 2;

    private final KakaoNaviClient client;
    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint dim = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint bg = new Paint(Paint.ANTI_ALIAS_FLAG);
    private int seq = 0;
    private boolean lastWasClear = true;
    private String lastKey = "";

    KakaoSignal(KakaoNaviClient client) {
        this.client = client;
        text.setColor(Color.WHITE);
        text.setTextAlign(Paint.Align.CENTER);
        text.setTypeface(Typeface.create(Typeface.DEFAULT, Typeface.BOLD));
        bg.setColor(0xE6101216);
    }

    /** color: 0 없음/어두움, 1 빨강, 2 노랑, 3 초록. remainSec<0 이면 숫자 숨김. */
    synchronized void publish(int color, int remainSec) {
        if (color <= 0) {
            clear();
            return;
        }
        String key = color + ":" + remainSec;
        if (key.equals(lastKey)) return;   // 바뀔 때만 보냄(부하 절감)
        lastKey = key;
        try {
            byte[] png = render(color, remainSec);
            client.sendSignal(frame(1, png, CANVAS_W, CANVAS_H));
            lastWasClear = false;
        } catch (Throwable t) {
            KakaoHudLog.ex("signal render", t);
        }
    }

    synchronized void clear() {
        if (lastWasClear) return;
        lastWasClear = true;
        lastKey = "";
        client.sendSignal(frame(4, null, 0, 0));
    }

    private byte[] render(int color, int remainSec) {
        Bitmap bmp = Bitmap.createBitmap(CANVAS_W, CANVAS_H, Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(bmp);
        c.translate(CANVAS_W - W * CONTENT_SCALE, (CANVAS_H - H * CONTENT_SCALE) / 2f);
        c.scale(CONTENT_SCALE, CONTENT_SCALE);
        // 둥근 검정 배경(가로형 신호등)
        android.graphics.RectF box = new android.graphics.RectF(2, 2, W - 2, H - 2);
        c.drawRoundRect(box, 18, 18, bg);

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

    /** 40바이트 CNV2 헤더(서버 BINARY_HEADER ">4sBBBBIIQQIHH") + 본문. */
    private byte[] frame(int messageType, byte[] body, int w, int h) {
        int bodyLen = body == null ? 0 : body.length;
        ByteBuffer buf = ByteBuffer.allocate(40 + bodyLen).order(ByteOrder.BIG_ENDIAN);
        buf.put((byte) 'C').put((byte) 'N').put((byte) 'V').put((byte) '2');
        buf.put((byte) PROTOCOL_VERSION);   // protocol_version
        buf.put((byte) messageType);        // 1 image / 4 clear
        buf.put((byte) 0);                  // format_or_reason (0 = PNG)
        buf.put((byte) 0);                  // flags
        buf.putInt(0);                      // stream_handle
        buf.putInt(0);                      // revision
        buf.putLong(seq++);                 // sequence
        buf.putLong(System.currentTimeMillis()); // source_timestamp_ms
        buf.putInt(bodyLen);                // payload_length
        buf.putShort((short) w);            // width
        buf.putShort((short) h);            // height
        if (body != null) buf.put(body);
        return buf.array();
    }
}
