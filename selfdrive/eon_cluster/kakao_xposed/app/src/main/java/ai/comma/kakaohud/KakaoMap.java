package ai.comma.kakaohud;

import android.content.Context;
import android.graphics.Bitmap;
import android.os.Handler;
import android.os.HandlerThread;

import java.io.ByteArrayOutputStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.util.Collections;
import java.util.List;

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
    // 2fps(500ms)는 위치 콜백 주기와 겹쳐 지도가 최대 1초 가까이 늦게 보였다.
    // 3fps 로 올리고, 아래 LEAD_S 만큼 진행 방향으로 앞당겨 그린다.
    private static final long INTERVAL_MS = 333;   // 3fps
    // 위치 콜백 주기 + 캡처/인코딩 + EON 중계 + HUD 표시까지의 지연을 보상하는 선행 시간.
    private static final double LEAD_S = 0.6;
    // 차량을 화면 가운데보다 아래(세로 68%)에 두어 앞쪽 도로를 더 보여준다.
    // 카카오 앱 주행 카메라도 anchor 를 써서 차량을 아래쪽에 둔다.
    private static final float ANCHOR_X = 0.5f;
    private static final float ANCHOR_Y = 0.68f;
    private static final long STATIONARY_HEARTBEAT_MS = 2000;
    private static final long INIT_RETRY_MS = 5000;
    // 카카오 지도 zoom 은 "작을수록 확대"인 배율값이다(네이버/구글 줌레벨과 반대).
    // 근거: 카카오 앱 KNUMapComponentKt 확대 = zoom/1.5, 축소 = zoom*1.5.
    // 앱도 criterionWorldSize 는 쓰지 않고 zoom 만 쓴다.
    //
    // 앱 주행 추적 카메라 zoom = KNUCameraScale 표[행][scaleIndex] (4.51.0 실측).
    // 여기서는 일반도로 DEFAULT 표 첫 행을 쓴다.
    private static final float[] DRIVE_ZOOM = {1.0f, 1.0f, 1.4f, 2.6f, 3.1f, 3.6f, 4.2f};
    // scaleIndex 결정(KNUMapLocationUseCase 실측):
    //  - 다음 안내지점까지 550m 미만: 150/250/350/450/550m 경계로 1~5
    //  - 그 외: 속도 20/40/60/80/100 km/h 경계로 1~6 (앱은 GPS 속도 사용)
    private static final int[] TURN_DIST_BOUNDS = {150, 250, 350, 450, 550};
    private static final int[] SPEED_BOUNDS_KPH = {20, 40, 60, 80, 100};
    private static final float TILT = 45f;

    private final KakaoNaviClient client;
    private ClassLoader cl;
    private Context context;

    private Object capturer;         // KNMMapCapturer 인스턴스
    private Method captureMethod;    // capture(int,int) -> Bitmap
    private Method moveCameraMethod; // moveCamera(KNMCameraUpdate)
    private Object cameraCompanion;  // KNMCameraUpdate.Companion (INSTANCE)
    private Method targetTo, bearingTo, tiltTo, zoomTo, anchorTo;
    private Method katecPoint;       // KNMPoint.Companion.katec(double,double)
    private Object pointCompanion;
    private Method setRoutesMethod;   // setRoutes(List<KNMRoute>)
    private Method convertRoutesMethod; // fl0.g.f(List<KNU route>) -> List<KNMRoute>

    private volatile Object pendingSdkRoute;
    private Object appliedSdkRoute;

    private volatile double curX = 0, curY = 0, curBearing = 0;
    // 속도는 KATEC(미터 단위) 위치 변화로 추정한다. SDK 속도 getter 는 난독화라 쓰지 않는다.
    private volatile double speedKph = 0;
    private double speedRefX = 0, speedRefY = 0;
    private long speedRefMs = 0;
    private volatile int turnDistanceM = -1;
    private int lastScaleIndex = -1;
    private volatile boolean hasPose = false;
    private double lastX = 0, lastY = 0, lastBearing = 0;
    private long lastFrameMs = 0;

    private HandlerThread thread;
    private Handler handler;
    private long nextInitAttemptMs = 0;
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

    private long maxFrameMs = 0;
    private final android.graphics.Paint markerFill = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
    private final android.graphics.Paint markerEdge = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
    private final android.graphics.Paint markerShadow = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
    private final android.graphics.Path markerPath = new android.graphics.Path();

    /**
     * 오프스크린 캡처러는 내 위치 마커를 그리지 않는다. 지도는 차량 방위 기준(heading-up)
     * 이므로 anchor 위치에 위쪽을 향한 화살표를 직접 그린다.
     */
    private void drawVehicleMarker(Bitmap bmp) {
        float cx = bmp.getWidth() * (anchorTo != null ? ANCHOR_X : 0.5f);
        float cy = bmp.getHeight() * (anchorTo != null ? ANCHOR_Y : 0.5f);
        float r = bmp.getHeight() * 0.055f;
        markerPath.reset();
        markerPath.moveTo(cx, cy - r * 1.25f);
        markerPath.lineTo(cx + r, cy + r);
        markerPath.lineTo(cx, cy + r * 0.45f);
        markerPath.lineTo(cx - r, cy + r);
        markerPath.close();
        android.graphics.Canvas c = new android.graphics.Canvas(bmp);
        markerShadow.setColor(0x55000000);
        c.drawCircle(cx, cy + r * 0.1f, r * 1.55f, markerShadow);
        markerEdge.setColor(0xFFFFFFFF);
        markerEdge.setStyle(android.graphics.Paint.Style.STROKE);
        markerEdge.setStrokeWidth(r * 0.28f);
        markerEdge.setStrokeJoin(android.graphics.Paint.Join.ROUND);
        c.drawPath(markerPath, markerEdge);
        markerFill.setColor(0xFF2F7BF5);
        markerFill.setStyle(android.graphics.Paint.Style.FILL);
        c.drawPath(markerPath, markerFill);
    }

    void updatePose(double katecX, double katecY, double bearing) {
        long now = android.os.SystemClock.elapsedRealtime();
        if (speedRefMs == 0) {
            speedRefX = katecX; speedRefY = katecY; speedRefMs = now;
        } else if (now - speedRefMs >= 1000) {
            double dist = Math.hypot(katecX - speedRefX, katecY - speedRefY);
            double kph = dist / ((now - speedRefMs) / 1000.0) * 3.6;
            if (kph < 250) {
                // 저역통과로 튐 완화
                speedKph = speedKph * 0.6 + kph * 0.4;
            }
            speedRefX = katecX; speedRefY = katecY; speedRefMs = now;
        }
        curX = katecX;
        curY = katecY;
        curBearing = bearing;
        hasPose = true;
    }

    /** 다음 안내지점까지 남은 거리(m). 모르면 음수. */
    void updateTurnDistance(int meters) {
        turnDistanceM = meters;
    }

    private int scaleIndex() {
        int d = turnDistanceM;
        if (d >= 0 && d < TURN_DIST_BOUNDS[TURN_DIST_BOUNDS.length - 1]) {
            for (int i = 0; i < TURN_DIST_BOUNDS.length; i++) {
                if (d < TURN_DIST_BOUNDS[i]) return i + 1;
            }
        }
        double v = speedKph;
        for (int i = 0; i < SPEED_BOUNDS_KPH.length; i++) {
            if (v < SPEED_BOUNDS_KPH[i]) return i + 1;
        }
        return 6;
    }

    /** 안내 중인 KNU 경로를 캡처 지도용 KNMRoute로 변환해 경로선을 표시한다. */
    void updateRoute(Object sdkRoute) {
        pendingSdkRoute = sdkRoute;
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

        long now = System.currentTimeMillis();
        if (capturer == null) {
            if (now < nextInitAttemptMs) return;
            nextInitAttemptMs = now + INIT_RETRY_MS;
            initCapturer();
        }
        if (capturer == null) return;

        // 정차 중에도 서버의 5초 stale watchdog보다 빠르게 새 프레임을 보낸다.
        // 첫 capture가 실패했을 때는 위치를 소비하지 않아 다음 tick에서 즉시 재시도한다.
        double dx = curX - lastX, dy = curY - lastY, dh = curBearing - lastBearing;
        boolean stationary = sentCount > 0
                && Math.abs(dx) < 1.0 && Math.abs(dy) < 1.0 && Math.abs(dh) < 1.0;
        if (stationary && now < lastFrameMs + STATIONARY_HEARTBEAT_MS) {
            return;
        }

        try {
            applyRouteIfNeeded();

            // KATEC 은 미터 단위(x 동쪽, y 북쪽), 방위는 북쪽 기준 시계방향이다.
            double leadM = speedKph >= 3.0 ? speedKph / 3.6 * LEAD_S : 0.0;
            double rad = Math.toRadians(curBearing);
            double drawX = curX + leadM * Math.sin(rad);
            double drawY = curY + leadM * Math.cos(rad);
            Object point = katecPoint.invoke(pointCompanion, drawX, drawY);
            Object update = targetTo.invoke(cameraCompanion, point);
            if (anchorTo != null) {
                update = anchorTo.invoke(update, new android.graphics.PointF(ANCHOR_X, ANCHOR_Y));
            }
            update = bearingTo.invoke(update, (float) curBearing);
            update = tiltTo.invoke(update, TILT);
            int scale = scaleIndex();
            if (scale != lastScaleIndex) {
                lastScaleIndex = scale;
                KakaoHudLog.status("map scale idx=" + scale + " zoom=" + DRIVE_ZOOM[scale]
                        + " spd=" + (int) speedKph + " turn=" + turnDistanceM);
            }
            update = zoomTo.invoke(update, DRIVE_ZOOM[scale]);
            moveCameraMethod.invoke(capturer, update);

            long t0 = android.os.SystemClock.elapsedRealtime();
            Object bmpObj = captureMethod.invoke(capturer, WIDTH, HEIGHT);
            if (bmpObj == null) {
                nullCount++;
                if (nullCount == 1 || nullCount % 20 == 0) {
                    KakaoHudLog.status("capture null x" + nullCount + " (SDK not ready?)");
                }
                return;
            }
            Bitmap bmp = (Bitmap) bmpObj;
            if (!bmp.isMutable()) {
                Bitmap copy = bmp.copy(Bitmap.Config.ARGB_8888, true);
                bmp.recycle();
                bmp = copy;
            }
            drawVehicleMarker(bmp);
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            bmp.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out);
            bmp.recycle();
            byte[] jpeg = out.toByteArray();
            long frameMs = android.os.SystemClock.elapsedRealtime() - t0;
            if (frameMs > maxFrameMs) {
                maxFrameMs = frameMs;
                KakaoHudLog.status("map frame cost max=" + frameMs + "ms (capture+marker+jpeg)");
            }
            client.sendMap(jpeg);
            lastX = curX;
            lastY = curY;
            lastBearing = curBearing;
            lastFrameMs = now;
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
            setRoutesMethod = capClass.getMethod("setRoutes", List.class);

            // 카카오 앱이 실제 주행 지도에 사용하는 동일 변환기.
            Class<?> routeConverter = cl.loadClass("com.kakaomobility.knmsdk.fl0.g");
            convertRoutesMethod = routeConverter.getMethod("f", List.class);

            Class<?> updClass = cl.loadClass("com.kakaomobility.knmsdk.camera.KNMCameraUpdate");
            moveCameraMethod = capClass.getMethod("moveCamera", updClass);

            // KNMCameraUpdate 정적 빌더(Companion). INSTANCE 필드로 접근.
            cameraCompanion = kotlinCompanion(updClass);
            Class<?> pointClass = cl.loadClass("com.kakaomobility.knmsdk.utils.KNMPoint");
            targetTo = cameraCompanion.getClass().getMethod("targetTo", pointClass);
            bearingTo = updClass.getMethod("bearingTo", float.class);
            tiltTo = updClass.getMethod("tiltTo", float.class);
            zoomTo = updClass.getMethod("zoomTo", float.class);
            try {
                anchorTo = updClass.getMethod("anchorTo", android.graphics.PointF.class);
            } catch (NoSuchMethodException noAnchor) {
                anchorTo = null;
                KakaoHudLog.line("anchorTo unsupported, vehicle stays at center");
            }
            KakaoHudLog.line("map scale mode: Kakao drive zoom table");

            pointCompanion = kotlinCompanion(pointClass);
            katecPoint = pointCompanion.getClass().getMethod("katec", double.class, double.class);

            // 테마: 실패해도 무시(기본 테마로 진행).
            tryTheme(capClass);

            KakaoHudLog.line("KNMMapCapturer created (density=" + density + ")");
        } catch (Throwable t) {
            KakaoHudLog.ex("initCapturer", t);
            capturer = null;
        }
    }

    private void applyRouteIfNeeded() {
        if (setRoutesMethod == null || convertRoutesMethod == null) return;
        Object route = pendingSdkRoute;
        if (route == appliedSdkRoute) return;
        try {
            List<?> mapRoutes;
            if (route == null) {
                mapRoutes = Collections.emptyList();
            } else {
                Object converted = convertRoutesMethod.invoke(
                        null, Collections.singletonList(route));
                if (!(converted instanceof List) || ((List<?>) converted).isEmpty()) {
                    KakaoHudLog.line("map route conversion returned empty");
                    return;
                }
                mapRoutes = (List<?>) converted;
            }
            setRoutesMethod.invoke(capturer, mapRoutes);
            appliedSdkRoute = route;
            KakaoHudLog.line("map route applied count=" + mapRoutes.size());
        } catch (Throwable t) {
            KakaoHudLog.ex("map route", t);
        }
    }

    private static Object kotlinCompanion(Class<?> owner) throws Exception {
        for (String field : new String[]{"Companion", "INSTANCE"}) {
            try {
                return owner.getField(field).get(null);
            } catch (NoSuchFieldException ignored) { }
        }
        throw new NoSuchFieldException(owner.getName() + ".Companion");
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
