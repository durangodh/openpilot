package ai.comma.naverhud;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.view.View;

import java.io.ByteArrayOutputStream;
import java.lang.ref.WeakReference;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 네이버 폰 안내 화면의 작은 표시 뷰를 그대로 그림으로 떠서 HUD 오버레이로 보낸다.
 *
 *  lane_bottom     NaviLaneControlView(차로 화살표 + 거리). LaneComponent 가
 *                  a(NaviLaneItem, boolean) 으로 갱신한다. k()(차로 목록)가 비면 숨김.
 *  traffic_signal  NaviTrafficSignalView(방향별 신호 아이콘 + 잔여초).
 *                  NaviTrafficSignalComponent 가 a(boolean, TrafficSignalInfo) 로 갱신한다.
 *                  첫 인자가 false 거나 k()(신호 목록)가 비면 숨김.
 * (6.10.0.16 디컴파일. 클래스·메서드 이름은 난독화되지 않은 실제 이름이다.)
 *
 * 그림(차로 그림은 서버에서 나중에 받아 오기도 하고, 잔여초는 매초 바뀐다)이 바뀌는지
 * 떠 있는 동안 500ms 마다 뷰를 다시 그려 보고, 바뀌었을 때만(+5초 재전송) 보낸다.
 * 작은 뷰라 메인 스레드 부담은 작다.
 */
final class NaverViewImage {
    static final String LANE_VIEW = "com.naver.map.feature.navigation.view.lane.NaviLaneControlView";
    static final String SIGNAL_VIEW = "com.naver.map.core.navigation.view.NaviTrafficSignalView";
    private static final long TICK_MS = 500;
    private static final long RESEND_MS = 5000;

    private final NaverNaviClient client;
    private final String name;
    // HUD 가 그리는 칸 크기(차로 530x84, 신호등 302x192)에 맞춰 줄여 보낸다. 폰 해상도
    // 그대로면 PNG 가 커서 EON·HUD 로 넘기는 동안 지도까지 밀렸다.
    private final int maxW, maxH;
    // 같은 그림이 아니어도 이 간격보다 자주는 안 보낸다(차로 띠는 거리 숫자가 자주 바뀐다).
    private final long minIntervalMs;
    // 내용이 그림 높이에서 차지하는 비율. HUD 는 그림을 칸에 꽉 맞춰 키우므로, 위쪽에
    // 투명 여백을 두어 실제 크기를 정한다(차로 띠는 거리 숫자까지 있어 꽉 채우면 너무 컸다).
    private final float fill;
    private volatile boolean used;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService encoder = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "naver-hud-view");
        t.setDaemon(true);
        t.setPriority(Thread.MIN_PRIORITY);
        return t;
    });
    private WeakReference<View> view;
    private boolean visible, ticking, shown, loggedFirst, loggedError;
    private Bitmap lastBitmap;
    private long lastSentAt;
    private long seq;

    NaverViewImage(NaverNaviClient client, String name, int maxW, int maxH, long minIntervalMs, float fill) {
        this.client = client;
        this.name = name;
        this.maxW = maxW;
        this.maxH = maxH;
        this.minIntervalMs = minIntervalMs;
        this.fill = Math.max(0.3f, Math.min(1f, fill));
    }

    /** 폰 뷰가 한 번이라도 갱신됐으면 true(이후 이 그림만 쓴다). */
    boolean used() {
        return used;
    }

    /** 메인 스레드: 폰 뷰의 갱신 메서드가 끝난 뒤. */
    void onUpdate(View phoneView, boolean show) {
        used = true;
        view = new WeakReference<>(phoneView);
        visible = show;
        if (!visible) {
            clear();
            return;
        }
        if (!ticking) {
            ticking = true;
            main.post(this::tick);
        }
    }

    /** 항목의 k() 목록이 비어 있지 않으면 true(차로·신호 둘 다 k()). */
    static boolean hasItems(Object item) {
        if (item == null) return false;
        try {
            Object units = item.getClass().getMethod("k").invoke(item);
            return units instanceof List && !((List<?>) units).isEmpty();
        } catch (Throwable t) {
            return false;
        }
    }

    private void tick() {
        View v = view == null ? null : view.get();
        if (!visible || v == null) {
            ticking = false;
            clear();
            return;
        }
        try {
            long now = SystemClock.elapsedRealtime();
            if (lastBitmap != null && now - lastSentAt < minIntervalMs) {
                main.postDelayed(this::tick, TICK_MS);
                return;
            }
            Bitmap bmp = draw(v, maxW, maxH, fill);
            if (bmp != null) {
                boolean same = lastBitmap != null && lastBitmap.sameAs(bmp);
                if (!same || now - lastSentAt >= RESEND_MS) {
                    if (lastBitmap != null) lastBitmap.recycle();
                    lastBitmap = bmp;
                    lastSentAt = now;
                    shown = true;
                    final Bitmap copy = bmp.copy(Bitmap.Config.ARGB_8888, false);
                    encoder.execute(() -> send(copy));
                } else {
                    bmp.recycle();
                }
            }
        } catch (Throwable t) {
            if (!loggedError) {
                loggedError = true;
                NaverHudLog.ex(name + " image", t);
            }
        }
        main.postDelayed(this::tick, TICK_MS);
    }

    /**
     * 뷰를 그린 뒤 투명 여백을 잘라내고 HUD 칸 크기로 줄인다. 폰 차로 뷰는 화면 폭만큼
     * 넓고 그림은 가운데에 작게 있어서, 여백째 줄이면 HUD 에서 차로가 아주 작게 보였다.
     */
    private static Bitmap draw(View v, int maxW, int maxH, float fill) {
        int w = v.getWidth(), h = v.getHeight();
        if (w <= 0 || h <= 0) return null;
        float s0 = Math.min(1f, 1080f / w);   // 여백 찾기용으로 너무 크지 않게만
        int fw = Math.max(1, Math.round(w * s0)), fh = Math.max(1, Math.round(h * s0));
        Bitmap full = Bitmap.createBitmap(fw, fh, Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(full);
        c.scale(s0, s0);
        v.draw(c);

        int[] px = new int[fw * fh];
        full.getPixels(px, 0, fw, 0, 0, fw, fh);
        int left = fw, top = fh, right = -1, bottom = -1;
        for (int y = 0; y < fh; y++) {
            int row = y * fw;
            for (int x = 0; x < fw; x++) {
                if ((px[row + x] >>> 24) > 16) {
                    if (x < left) left = x;
                    if (x > right) right = x;
                    if (y < top) top = y;
                    if (y > bottom) bottom = y;
                }
            }
        }
        if (right < left || bottom < top) {   // 아직 아무것도 안 그려짐
            full.recycle();
            return null;
        }
        int pad = 2;
        left = Math.max(0, left - pad);
        top = Math.max(0, top - pad);
        right = Math.min(fw - 1, right + pad);
        bottom = Math.min(fh - 1, bottom + pad);
        int cw = right - left + 1, ch = bottom - top + 1;
        float scale = Math.min(1f, Math.min(maxW / (float) cw, maxH * fill / (float) ch));
        int ow = Math.max(1, Math.round(cw * scale)), oh = Math.max(1, Math.round(ch * scale));
        int padTop = Math.round(oh / fill) - oh;   // 위쪽 여백: 내용은 아래에 붙는다
        Bitmap out = Bitmap.createBitmap(ow, oh + padTop, Bitmap.Config.ARGB_8888);
        Canvas oc = new Canvas(out);
        android.graphics.Paint paint = new android.graphics.Paint(android.graphics.Paint.FILTER_BITMAP_FLAG);
        oc.drawBitmap(full, new android.graphics.Rect(left, top, right + 1, bottom + 1),
                new android.graphics.Rect(0, padTop, ow, padTop + oh), paint);
        full.recycle();
        return out;
    }

    private void send(Bitmap bmp) {
        try {
            ByteArrayOutputStream png = new ByteArrayOutputStream(16384);
            bmp.compress(Bitmap.CompressFormat.PNG, 100, png);
            byte[] body = png.toByteArray();
            client.sendImage(name, frame(1, body, bmp.getWidth(), bmp.getHeight()));
            if (!loggedFirst) {
                loggedFirst = true;
                NaverHudLog.xposed(name + " image sent " + bmp.getWidth() + "x" + bmp.getHeight()
                        + " (" + body.length + " bytes)");
            }
        } catch (Throwable t) {
            NaverHudLog.ex(name + " encode", t);
        } finally {
            bmp.recycle();
        }
    }

    /** 메인 스레드. */
    private void clear() {
        if (lastBitmap != null) {
            lastBitmap.recycle();
            lastBitmap = null;
        }
        if (!shown) return;
        shown = false;
        encoder.execute(() -> client.sendImage(name, frame(4, null, 0, 0)));
    }

    /** 40바이트 CNV2 헤더(서버 BINARY_HEADER ">4sBBBBIIQQIHH") + PNG(format 0). */
    private synchronized byte[] frame(int messageType, byte[] body, int w, int h) {
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
