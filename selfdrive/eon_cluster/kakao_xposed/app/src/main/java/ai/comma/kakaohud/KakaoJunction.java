package ai.comma.kakaohud;

import android.graphics.Bitmap;
import android.os.SystemClock;

import java.io.ByteArrayOutputStream;
import java.lang.ref.WeakReference;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * 카카오 교차로 확대 이미지(폰 주행 화면의 JC 팝업)를 EON 의 crossroad_expanded
 * 오버레이로 보낸다. 티맵·네이버 모듈과 같은 자리에 뜬다.
 *
 * 출처(4.51.1 디컴파일): KNUJCViewModel.getJcUIState() 의 값이 KNUJCUIState.Data
 * (실제 이름 navi.drive.core.feature.jc.c$a)이면 폰에 팝업이 떠 있다. 그 안의 KNUJC
 * (knmsdk.hi0.a, 필드 a)의 a() 가 팝업 Bitmap 이다(안드로이드 오토 JcImageProvider 도
 * 같은 값을 쓴다). Data 가 아니면(None) 팝업이 닫힌 것이다.
 *
 * 그림이 바뀔 때 JPEG 로 한 번 보내고, 떠 있는 동안 5초마다 다시 보낸다. 사라지면
 * CNV2 clear 를 한 번 보낸다. 250ms 마다 상태를 읽는다(읽기만 한다).
 */
final class KakaoJunction {
    static final String VIEW_MODEL = "com.kakaomobility.navi.drive.core.feature.jc.KNUJCViewModel";
    private static final String DATA_STATE = "com.kakaomobility.navi.drive.core.feature.jc.c$a";
    private static final String NAME = "crossroad_expanded";
    private static final int JPEG_QUALITY = 85;
    private static final long POLL_MS = 250;
    private static final long RESEND_MS = 5000;
    private static final int MAX_BYTES = 480 * 1024;   // 서버 MAX_LANE_FRAME_BYTES(512KB) 안쪽

    private final KakaoNaviClient client;
    private final ScheduledExecutorService poller = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "kakao-hud-junction");
        t.setDaemon(true);
        return t;
    });
    private volatile WeakReference<Object> viewModel;
    private boolean started;
    private Bitmap lastBitmap;
    private byte[] lastFrame;
    private long lastSentAt;
    private boolean shown;
    private long seq;
    private boolean loggedFirst, loggedError;

    KakaoJunction(KakaoNaviClient client) {
        this.client = client;
    }

    /** KNUJCViewModel 생성자 후킹에서 호출. 가장 최근 것을 쓴다(주행 화면마다 새로 생긴다). */
    synchronized void setViewModel(Object vm) {
        viewModel = new WeakReference<>(vm);
        if (!started) {
            started = true;
            poller.scheduleWithFixedDelay(this::tick, POLL_MS, POLL_MS, TimeUnit.MILLISECONDS);
            KakaoHudLog.line("junction view model captured");
        }
    }

    private void tick() {
        try {
            if (!client.ready()) return;
            Bitmap image = currentImage();
            if (image == null || image.isRecycled()) {
                clear();
                return;
            }
            long now = SystemClock.elapsedRealtime();
            if (image != lastBitmap || lastFrame == null) {
                byte[] frame = encode(image);
                if (frame == null) return;
                lastBitmap = image;
                lastFrame = frame;
            } else if (now - lastSentAt < RESEND_MS) {
                return;
            }
            client.sendImage(NAME, lastFrame);
            lastSentAt = now;
            shown = true;
            if (!loggedFirst) {
                loggedFirst = true;
                KakaoHudLog.xposed("junction image sent " + image.getWidth() + "x" + image.getHeight()
                        + " (" + lastFrame.length + " bytes)");
            }
        } catch (Throwable t) {
            if (!loggedError) {
                loggedError = true;
                KakaoHudLog.ex("junction", t);
            }
        }
    }

    private void clear() {
        lastBitmap = null;
        lastFrame = null;
        if (!shown) return;
        shown = false;
        client.sendImage(NAME, frame(4, null, 0, 0));
    }

    private Bitmap currentImage() throws Exception {
        WeakReference<Object> ref = viewModel;
        Object vm = ref == null ? null : ref.get();
        if (vm == null) return null;
        Object flow = call(vm, "getJcUIState");
        Object state = call(flow, "getValue");
        if (state == null || !DATA_STATE.equals(state.getClass().getName())) return null;
        Object jc = field(state, "a");                  // KNUJCUIState.Data.jc
        Object image = call(jc, "a");                   // KNUJC.getImage()
        return image instanceof Bitmap ? (Bitmap) image : null;
    }

    private static Object call(Object target, String name) throws Exception {
        if (target == null) return null;
        Method m = target.getClass().getMethod(name);
        m.setAccessible(true);
        return m.invoke(target);
    }

    private static Object field(Object target, String name) throws Exception {
        if (target == null) return null;
        Field f = target.getClass().getDeclaredField(name);
        f.setAccessible(true);
        return f.get(target);
    }

    private byte[] encode(Bitmap image) {
        try {
            int quality = JPEG_QUALITY;
            byte[] jpeg;
            do {
                ByteArrayOutputStream out = new ByteArrayOutputStream(120000);
                image.compress(Bitmap.CompressFormat.JPEG, quality, out);
                jpeg = out.toByteArray();
                quality -= 15;
            } while (jpeg.length > MAX_BYTES && quality >= 40);
            if (jpeg.length > MAX_BYTES) return null;
            return frame(1, jpeg, image.getWidth(), image.getHeight());
        } catch (Throwable error) {
            KakaoHudLog.ex("junction encode", error);
            return null;
        }
    }

    /** 40바이트 CNV2 헤더(서버 BINARY_HEADER ">4sBBBBIIQQIHH") + JPEG(format 2). */
    private byte[] frame(int messageType, byte[] body, int w, int h) {
        int bodyLen = body == null ? 0 : body.length;
        ByteBuffer buf = ByteBuffer.allocate(40 + bodyLen).order(ByteOrder.BIG_ENDIAN);
        buf.put((byte) 'C').put((byte) 'N').put((byte) 'V').put((byte) '2');
        buf.put((byte) 2);                  // protocol_version
        buf.put((byte) messageType);        // 1 image / 4 clear
        buf.put((byte) 2);                  // format_or_reason (2 = JPEG)
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
