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

    private static final int W = 250;   // 가로 알약(등 2개 + 화살표 + 잔여초)
    private static final int H = 72;
    // HUD 는 신호등 그림을 302x192 칸에 비율을 지켜 꽉 채우므로, 칸과 같은 크기의 투명
    // 캔버스 오른쪽·세로 가운데에 원본 크기로 그려 HUD 에서 그 크기 그대로 보이게 한다.
    private static final int CANVAS_W = 302;
    private static final int CANVAS_H = 192;
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
        publish(color, remainSec, false);
    }

    /** leftGo: 좌회전 신호가 초록이면 화살표를 초록으로. */
    synchronized void publish(int color, int remainSec, boolean leftGo) {
        if (color <= 0) {
            clear();
            return;
        }
        String key = color + ":" + remainSec + ":" + leftGo;
        if (key.equals(lastKey)) return;   // 바뀔 때만 보냄(부하 절감)
        lastKey = key;
        try {
            byte[] png = render(color, remainSec, leftGo);
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

    private byte[] render(int color, int remainSec, boolean leftGo) {
        Bitmap bmp = Bitmap.createBitmap(CANVAS_W, CANVAS_H, Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(bmp);
        // 카카오 원본처럼 가로 알약: [빨강/노랑 등] [좌회전 화살표] [초록 등] 잔여초.
        c.translate(CANVAS_W - W, (CANVAS_H - H) / 2f);
        c.drawRoundRect(new android.graphics.RectF(0, 0, W, H), H / 2f, H / 2f, bg);
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
