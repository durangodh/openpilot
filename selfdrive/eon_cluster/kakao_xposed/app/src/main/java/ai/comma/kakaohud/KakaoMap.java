package ai.comma.kakaohud;

import android.content.Context;
import android.graphics.Bitmap;
import android.os.Handler;
import android.os.HandlerThread;

import java.io.ByteArrayOutputStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;

/**
 * 카카오 SDK 내장 오프스크린 지도 캡처러(KNMMapCapturer)를 앱 밖(모듈)에서
 * 직접 생성해, HUD 지도패널용 프레임을 만든다. 화면/nMirror 가상화면에 의존하지
 * 않는다(네이버 HUD13 스냅샷 방식과 다름).
 *
 * 파이프라인:
 *   new KNMMapCapturer(ctx, density, 1.0)
 *   setTheme( KNMSDK.INSTANCE.getTheme("...") )   -- 실패해도 기본 테마로 진행
 *   전용 스레드 2fps:
 *     moveCamera( KNMCameraUpdate.targetTo(KNMPoint.katec(x,y)).bearingTo(h).tiltTo().zoomTo() )
 *     Bitmap bmp = capture(720, 432)
 *     JPEG q72 -> client.sendMap
 *
 * KNMSDK 초기화 전(getInitState != 2)엔 capture 가 null 을 준다. 그때는 조용히
 * 스킵하고 계속 재시도한다. 앱 밖 생성이 GL 프레임을 실제로 내주는지가 유일한
 * 미검증 지점이라, 상태를 로그로 남긴다.
 */
final class KakaoMap {

    private static final int WIDTH = 720;
    private static final int HEIGHT = 432;
    private static final int JPEG_QUALITY = 72;
    private static final long INTERVAL_MS = 500;   // 2fps
    private static final float ZOOM = 15.5f;
    private static final float TILT = 45f;

    private final KakaoNaviClient client;
    private ClassLoader cl;
    private Context context;

    private Object capturer;         // KNMMapCapturer 인스턴스
    private Method captureMethod;    // capture(int,int) -> Bitmap
    private Method moveCameraMethod; // moveCamera(KNMCameraUpdate)
    private Object cameraCompanion;  // KNMCameraUpdate.Companion (INSTANCE)
    private Method targetTo, bearingTo, tiltTo, zoomTo;
    private Method katecPoint;       // KNMPoint.Companion.katec(double,double)
    private Object pointCompanion;

    private volatile double curX = 0, curY = 0, curBearing = 0;
    private volatile boolean hasPose = false;
    private volatile boolean stationarySkip = false;
    private double lastX = 0, lastY = 0, lastBearing = 0;

    private HandlerThread thread;
    private Handler handler;
    private boolean initTried = false;
    private int nullCount = 0;
    private int sentCount = 0;

    KakaoMap(KakaoNaviClient client) {
        this.client = client;
    }

    void start(ClassLoader cl, Context ctx) {
        this.cl = cl;
        this.context = ctx;
        thread = new HandlerThread("kakao-map-capture");
        thread.start();
        handler = new Handler(thread.getLooper());
        handler.post(this::loop);
    }

    void updatePose(double katecX, double katecY, double bearing) {
        curX = katecX;
        curY = katecY;
        curBearing = bearing;
        hasPose = true;
    }

    private void loop() {
        try {
            tick();
        } catch (Throwable t) {
            KakaoHudLog.ex("map loop", t);
        } finally {
            if (handler != null) handler.postDelayed(this::loop, INTERVAL_MS);
        }
    }

    private void tick() {
        if (!client.ready() || !hasPose) return;

        if (!initTried) {
            initTried = true;
            initCapturer();
        }
        if (capturer == null) return;

        // 정차 스킵: 위치·방위가 사실상 안 바뀌면 새 프레임을 안 만든다(부하 절감).
        double dx = curX - lastX, dy = curY - lastY, dh = curBearing - lastBearing;
        if (Math.abs(dx) < 1.0 && Math.abs(dy) < 1.0 && Math.abs(dh) < 1.0 && stationarySkip) {
            return;
        }
        lastX = curX; lastY = curY; lastBearing = curBearing;
        stationarySkip = true;

        try {
            Object point = katecPoint.invoke(pointCompanion, curX, curY);
            Object update = targetTo.invoke(cameraCompanion, point);
            update = bearingTo.invoke(update, (float) curBearing);
            update = tiltTo.invoke(update, TILT);
            update = zoomTo.invoke(update, ZOOM);
            moveCameraMethod.invoke(capturer, update);

            Object bmpObj = captureMethod.invoke(capturer, WIDTH, HEIGHT);
            if (bmpObj == null) {
                nullCount++;
                if (nullCount == 1 || nullCount % 20 == 0) {
                    KakaoHudLog.status("capture null x" + nullCount + " (SDK not ready?)");
                }
                return;
            }
            Bitmap bmp = (Bitmap) bmpObj;
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            bmp.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out);
            bmp.recycle();
            byte[] jpeg = out.toByteArray();
            client.sendMap(jpeg);
            sentCount++;
            if (sentCount == 1) {
                KakaoHudLog.line("first map frame sent " + WIDTH + "x" + HEIGHT
                        + " " + jpeg.length + "B");
            } else {
                KakaoHudLog.status("map frames sent=" + sentCount);
            }
        } catch (Throwable t) {
            KakaoHudLog.ex("map tick", t);
        }
    }

    private void initCapturer() {
        try {
            float density = context.getResources().getDisplayMetrics().density;

            Class<?> capClass = cl.loadClass("com.kakaomobility.knmsdk.capturer.KNMMapCapturer");
            Constructor<?> ctor = capClass.getConstructor(Context.class, float.class, float.class);
            ctor.setAccessible(true);
            capturer = ctor.newInstance(context, density, 1.0f);
            captureMethod = capClass.getMethod("capture", int.class, int.class);

            Class<?> updClass = cl.loadClass("com.kakaomobility.knmsdk.camera.KNMCameraUpdate");
            moveCameraMethod = capClass.getMethod("moveCamera", updClass);

            // KNMCameraUpdate 정적 빌더(Companion). INSTANCE 필드로 접근.
            cameraCompanion = updClass.getField("INSTANCE").get(null);
            Class<?> pointClass = cl.loadClass("com.kakaomobility.knmsdk.utils.KNMPoint");
            targetTo = cameraCompanion.getClass().getMethod("targetTo", pointClass);
            bearingTo = updClass.getMethod("bearingTo", float.class);
            tiltTo = updClass.getMethod("tiltTo", float.class);
            zoomTo = updClass.getMethod("zoomTo", float.class);

            pointCompanion = pointClass.getField("INSTANCE").get(null);
            katecPoint = pointCompanion.getClass().getMethod("katec", double.class, double.class);

            // 테마: 실패해도 무시(기본 테마로 진행).
            tryTheme(capClass);

            KakaoHudLog.line("KNMMapCapturer created (density=" + density + ")");
        } catch (Throwable t) {
            KakaoHudLog.ex("initCapturer", t);
            capturer = null;
        }
    }

    private void tryTheme(Class<?> capClass) {
        try {
            Class<?> sdkClass = cl.loadClass("com.kakaomobility.knmsdk.KNMSDK");
            Object sdk = sdkClass.getField("INSTANCE").get(null);
            Method getTheme = sdkClass.getMethod("getTheme", String.class, String.class);
            // 테마/스타일 이름은 SDK 기본값을 모르면 null 이 온다. 그때는 setTheme 생략.
            Object theme = getTheme.invoke(sdk, "default", "day");
            if (theme != null) {
                Class<?> themeClass = cl.loadClass("com.kakaomobility.knmsdk.configurations.KNMTheme");
                Method setTheme = capClass.getMethod("setTheme", themeClass);
                setTheme.invoke(capturer, theme);
                KakaoHudLog.line("theme applied");
            } else {
                KakaoHudLog.line("theme null (default used)");
            }
        } catch (Throwable t) {
            KakaoHudLog.status("theme skip: " + t.getClass().getSimpleName());
        }
    }
}
