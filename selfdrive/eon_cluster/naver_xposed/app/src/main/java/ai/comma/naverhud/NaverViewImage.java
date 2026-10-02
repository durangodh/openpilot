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
    private static final int MAX_W = 640;

    private final NaverNaviClient client;
    private final String name;
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

    NaverViewImage(NaverNaviClient client, String name) {
        this.client = client;
        this.name = name;
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
            Bitmap bmp = draw(v);
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

    private static Bitmap draw(View v) {
        int w = v.getWidth(), h = v.getHeight();
        if (w <= 0 || h <= 0) return null;
        float scale = w > MAX_W ? MAX_W / (float) w : 1f;
        Bitmap bmp = Bitmap.createBitmap(Math.max(1, Math.round(w * scale)),
                Math.max(1, Math.round(h * scale)), Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(bmp);
        c.scale(scale, scale);
        v.draw(c);
        return bmp;
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
