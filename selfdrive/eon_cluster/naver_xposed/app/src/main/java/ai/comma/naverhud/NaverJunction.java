package ai.comma.naverhud;

import android.graphics.Bitmap;
import android.os.SystemClock;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/**
 * 네이버 교차로 확대 이미지(폰 안내 화면의 확대 팝업)를 EON 의 crossroad_expanded
 * 오버레이로 보낸다. 티맵 모듈의 crossroad_expanded 와 같은 자리에 뜬다.
 *
 * 출처: 안내 세션의 GuidanceSession.getJunction().getInfo().getImage(). NaviStore 가
 * 폰 화면 팝업(JunctionData)을 만들 때 쓰는 것과 같은 Bitmap 이다(세션에 junction 이
 * 있으면 팝업을 띄우고, null 이면 지운다). 이름은 난독화되지 않은 공개 API 다.
 *
 * 그림이 바뀔 때 JPEG 로 한 번 보내고, EON 재시작에 대비해 떠 있는 동안 5초마다
 * 다시 보낸다. 사라지면 CNV2 clear 를 한 번 보낸다.
 */
final class NaverJunction {
    private static final String NAME = "crossroad_expanded";
    private static final String GUIDANCE_CONTROL = "com.naver.maps.navi.v2.api.guidance.control.GuidanceControl";
    private static final int JPEG_QUALITY = 85;
    private static final long RESEND_MS = 5000;
    private static final int MAX_BYTES = 480 * 1024;   // 서버 MAX_LANE_FRAME_BYTES(512KB) 안쪽

    private final NaverNaviClient client;
    private Bitmap lastBitmap;
    private byte[] lastFrame;
    private long lastSentAt;
    private boolean shown;
    private long seq;
    private boolean loggedFirst;

    NaverJunction(NaverNaviClient client) {
        this.client = client;
    }

    /** 상태 스레드(250ms). guiding 이 아니면 지운다. */
    void publish(Object naviStore, boolean guiding) {
        Bitmap image = guiding ? currentImage(naviStore) : null;
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
            NaverHudLog.xposed("junction image sent " + image.getWidth() + "x" + image.getHeight()
                    + " (" + lastFrame.length + " bytes)");
        }
    }

    void clear() {
        lastBitmap = null;
        lastFrame = null;
        if (!shown) return;
        shown = false;
        client.sendImage(NAME, frame(4, null, 0, 0));
    }

    private static Bitmap currentImage(Object naviStore) {
        try {
            Object ui = NaverReflect.fieldOfType(naviStore, NaverReflect.NAVI_UI);
            Object control = NaverReflect.fieldOfType(ui, GUIDANCE_CONTROL);
            if (control == null) return null;
            Object session = get(control, "getCurrentSession");
            Object junction = get(session, "getJunction");
            Object info = get(junction, "getInfo");
            Object image = get(info, "getImage");
            return image instanceof Bitmap ? (Bitmap) image : null;
        } catch (Throwable error) {
            NaverHudLog.status("junction image: " + error);
            return null;
        }
    }

    /** 인자 없는 공개 메서드. 구현 클래스가 비공개여도 부를 수 있게 setAccessible. */
    private static Object get(Object target, String name) throws Exception {
        if (target == null) return null;
        java.lang.reflect.Method m = target.getClass().getMethod(name);
        m.setAccessible(true);
        return m.invoke(target);
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
            NaverHudLog.ex("junction encode", error);
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
