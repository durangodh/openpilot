package ai.comma.kakaohud;

import android.app.AndroidAppHelper;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.os.Handler;
import android.os.Looper;
import android.view.View;

import java.io.ByteArrayOutputStream;
import java.lang.reflect.Method;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * 카카오 차로 그림을 HUD 지도 아래 차로 띠(lane_bottom, 티맵과 같은 자리)로 보낸다.
 *
 * 그림은 카카오가 HUD·안드로이드 오토용 차로 그림을 만들 때 쓰는 KNULaneView
 * (KNUBitmapGeneratorImpl, AABitmapGenerator 와 같은 방법)로 그린다. 그래서 차로
 * 화살표·추천 차로 표시가 카카오 그림 그대로다. 차로 목록(KNULaneInfo 리스트)은
 * KakaoLane 이 읽은 것을 그대로 넘긴다.
 *
 * 차로 구성이 바뀔 때만 그리고, 떠 있는 동안 5초마다 다시 보낸다(EON 재시작 대비).
 * 사라지면 CNV2 clear 를 한 번 보낸다.
 */
final class KakaoLaneImage {
    private static final String NAME = "lane_bottom";
    private static final String VIEW = "com.kakaomobility.navi.drive.core.feature.lane.KNULaneView";
    private static final long RESEND_MS = 5000;
    private static final long DISTANCE_MIN_MS = 1000;   // 거리 숫자만 바뀔 때는 1초에 한 번
    // 폰 주행 화면처럼 차로 상자 위에 남은 거리를 붙인다. 너무 크지 않게 줄여 보낸다
    // (HUD 가 칸에 맞춰 다시 키운다).
    private static final int MAX_W = 400, MAX_H = 120;

    private final KakaoNaviClient client;
    private final Handler main = new Handler(Looper.getMainLooper());
    private View view;
    private Method setLaneInfos, setIsDarkMode;
    private String lastKey;
    private int lastDistance = -1;
    private final android.graphics.Paint label = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
    private final android.graphics.Paint pill = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
    private byte[] lastFrame;
    private long lastSentAt;
    private boolean shown, loggedFirst;
    private volatile boolean disabled;   // 메인 스레드에서 쓰고 차로 스레드에서 읽는다
    private long seq;

    KakaoLaneImage(KakaoNaviClient client) {
        this.client = client;
    }

    /** 차로 상태 스레드(KakaoLane, 250ms)에서 호출. infos 가 null 이면 지운다. */
    void publish(List<?> infos, String key, int distanceM, long now) {
        if (disabled) return;
        if (infos == null || infos.isEmpty()) {
            clear();
            return;
        }
        // 폰 주행 화면의 차로 표시는 주야 상관없이 검은 상자다. 그래서 늘 어두운 모양으로 그린다.
        boolean layoutChanged = !key.equals(lastKey) || lastFrame == null;
        boolean distanceChanged = distanceM != lastDistance && now - lastSentAt >= DISTANCE_MIN_MS;
        if (layoutChanged || distanceChanged) {
            byte[] frame = render(infos, distanceM);
            if (frame == null) return;
            lastKey = key;
            lastDistance = distanceM;
            lastFrame = frame;
        } else if (now - lastSentAt < RESEND_MS) {
            return;
        }
        client.sendImage(NAME, lastFrame);
        lastSentAt = now;
        shown = true;
    }

    void clear() {
        lastKey = null;
        lastDistance = -1;
        lastFrame = null;
        if (!shown) return;
        shown = false;
        client.sendImage(NAME, frame(4, null, 0, 0));
    }

    /** 뷰는 메인 스레드에서 만들고 그린다. PNG 인코딩은 호출 스레드에서 한다. */
    private byte[] render(final List<?> infos, final int distanceM) {
        final Bitmap[] out = new Bitmap[1];
        final CountDownLatch done = new CountDownLatch(1);
        main.post(() -> {
            try {
                out[0] = draw(infos, distanceM);
            } catch (Throwable t) {
                disabled = true;
                KakaoHudLog.ex("lane image (disabled)", t);
            } finally {
                done.countDown();
            }
        });
        try {
            if (!done.await(1, TimeUnit.SECONDS)) return null;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return null;
        }
        Bitmap bmp = out[0];
        if (bmp == null) return null;
        try {
            ByteArrayOutputStream png = new ByteArrayOutputStream(16384);
            bmp.compress(Bitmap.CompressFormat.PNG, 100, png);
            byte[] body = png.toByteArray();
            if (!loggedFirst) {
                loggedFirst = true;
                KakaoHudLog.xposed("lane image sent " + bmp.getWidth() + "x" + bmp.getHeight()
                        + " (" + body.length + " bytes)");
            }
            return frame(1, body, bmp.getWidth(), bmp.getHeight());
        } finally {
            bmp.recycle();
        }
    }

    private Bitmap draw(List<?> infos, int distanceM) throws Exception {
        if (view == null) {
            Context app = AndroidAppHelper.currentApplication();
            if (app == null) return null;
            Class<?> cls = app.getClassLoader().loadClass(VIEW);
            view = (View) cls.getConstructor(Context.class).newInstance(app);
            setLaneInfos = cls.getMethod("setLaneInfos", List.class);
            setIsDarkMode = cls.getMethod("setIsDarkMode", boolean.class);
        }
        setLaneInfos.invoke(view, infos);
        setIsDarkMode.invoke(view, true);
        int spec = View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED);
        view.measure(spec, spec);
        int w = view.getMeasuredWidth(), h = view.getMeasuredHeight();
        if (w <= 0 || h <= 0) return null;
        view.layout(0, 0, w, h);

        // 위쪽에 남은 거리(폰처럼 "55m"): 흰 굵은 글자, 어두운 둥근 바탕.
        String text = distanceM >= 1000 ? String.format(java.util.Locale.US, "%.1fkm", distanceM / 1000f)
                : distanceM + "m";
        float textSize = h * 0.42f;
        label.setColor(0xFFFFFFFF);
        label.setTextSize(textSize);
        label.setTypeface(android.graphics.Typeface.DEFAULT_BOLD);
        label.setTextAlign(android.graphics.Paint.Align.CENTER);
        pill.setColor(0xE6202226);
        float textW = label.measureText(text);
        float padX = textSize * 0.35f, labelH = textSize * 1.25f, gap = textSize * 0.15f;
        int totalW = Math.max(w, Math.round(textW + padX * 2));
        int totalH = Math.round(labelH + gap) + h;

        float scale = Math.min(1f, Math.min(MAX_W / (float) totalW, MAX_H / (float) totalH));
        Bitmap bmp = Bitmap.createBitmap(Math.max(1, Math.round(totalW * scale)),
                Math.max(1, Math.round(totalH * scale)), Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(bmp);
        c.scale(scale, scale);
        float cx = totalW / 2f;
        c.drawRoundRect(new android.graphics.RectF(cx - textW / 2f - padX, 0, cx + textW / 2f + padX, labelH),
                labelH * 0.3f, labelH * 0.3f, pill);
        c.drawText(text, cx, labelH * 0.5f - (label.descent() + label.ascent()) / 2f, label);
        c.translate((totalW - w) / 2f, labelH + gap);
        view.draw(c);
        return bmp;
    }

    /** 40바이트 CNV2 헤더(서버 BINARY_HEADER ">4sBBBBIIQQIHH") + PNG(format 0). */
    private byte[] frame(int messageType, byte[] body, int w, int h) {
        int bodyLen = body == null ? 0 : body.length;
        ByteBuffer buf = ByteBuffer.allocate(40 + bodyLen).order(ByteOrder.BIG_ENDIAN);
        buf.put((byte) 'C').put((byte) 'N').put((byte) 'V').put((byte) '2');
        buf.put((byte) 2);                  // protocol_version
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
