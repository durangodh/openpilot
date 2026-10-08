package ai.comma.kakaohud;

import android.content.Context;
import android.graphics.Bitmap;
import android.os.Handler;
import android.os.HandlerThread;

import java.io.ByteArrayOutputStream;
import java.lang.ref.WeakReference;
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
 *   전용 스레드 최대 5fps:
 *     moveCamera( KNMCameraUpdate.targetTo(KNMPoint.katec(x,y)).bearingTo(h).tiltTo().zoomTo() )
 *     Bitmap bmp = capture(720, 432)
 *     JPEG q65 -> client.sendMap
 *
 * KNMSDK 초기화 전(getInitState != 2)엔 capture 가 null 을 준다. 그때는 조용히
 * 스킵하고 계속 재시도한다. 앱 밖 생성이 GL 프레임을 실제로 내주는지가 유일한
 * 미검증 지점이라, 상태를 로그로 남긴다.
 */
final class KakaoMap {

    // 12.3인치 HUD 6:4 지도칸(760x720)과 1:1.
    private static final int WIDTH = 760;
    private static final int HEIGHT = 720;
    private static final int JPEG_QUALITY = 65;
    // 지도 엔진에 넘기는 화면 밀도. 폰 밀도(약 2.6~4)를 그대로 쓰면 760x720 지도에
    // 글자·도로가 2~3배 크게 그려진다. 티맵 HUD 지도(DPI 160)와 같은 크기로 맞춘다.
    private static final int MAP_DPI = 160;
    // Match TMAP's default 5fps cadence; latest-frame-only transport prevents
    // an overloaded link from turning this into a queue of stale pictures.
    private static final long INTERVAL_MS = 200;   // up to 5fps
    // 화면 카메라(위치·줌·방위)는 캡처보다 자주 읽는다. 캡처와 같은 200ms 로 따로
    // 돌면 두 주기가 어긋나 최대 0.2초 전 카메라로 찍혀 원본보다 늦게 따라간다.
    private static final long SCREEN_CAMERA_POLL_MS = 50;
    // 위치 콜백 주기 + 캡처/인코딩 + EON 중계 + HUD 표시까지의 지연을 보상하는 선행 시간.
    private static final double LEAD_S = 0.6;
    // 차량을 화면 가운데보다 아래(세로 68%)에 두어 앞쪽 도로를 더 보여준다.
    // 카카오 앱 주행 카메라도 anchor 를 써서 차량을 아래쪽에 둔다.
    private static final float ANCHOR_X = 0.5f;
    // 원본 화면 카메라를 읽지 못할 때만 쓰는 안전 기본 위치.
    private static final float ANCHOR_Y = 0.78f;
    // 720x432 지도가 HUD에서 확대되어도 카카오 원본과 같은 비율이 되도록
    // SDK 순정 마커를 쓰지 못할 때의 화살표를 기존보다 작게 그린다.
    private static final float FALLBACK_MARKER_RADIUS_HEIGHT = 0.035f;
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
    // KNU route 객체는 같은 인스턴스 안에서 경로를 갱신하므로 참조 비교만으로는
    // 파란 경로선 갱신을 놓칠 수 있다.
    private volatile long pendingRouteRevision;
    private long appliedRouteRevision = -1;

    private volatile double curX = 0, curY = 0, curBearing = 0;
    // 속도는 KATEC(미터 단위) 위치 변화로 추정한다. SDK 속도 getter 는 난독화라 쓰지 않는다.
    private volatile double speedKph = 0;
    private double speedRefX = 0, speedRefY = 0;
    private long speedRefMs = 0;
    private volatile int turnDistanceM = -1;
    private int lastScaleIndex = -1;
    private volatile boolean hasPose = false;
    private volatile WeakReference<Object> screenCameraSource;
    private volatile float markerAnchorX = ANCHOR_X;
    private volatile float markerAnchorY = ANCHOR_Y;
    private boolean screenCameraLogged = false;
    private boolean screenCameraErrorLogged = false;
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

    void setScreenCameraSource(Object source, boolean attached) {
        if (attached) {
            screenCameraSource = new WeakReference<>(source);
            screenCameraLogged = false;
            screenCameraErrorLogged = false;
        } else {
            WeakReference<Object> current = screenCameraSource;
            if (current != null && current.get() == source) {
                screenCameraSource = null;
                markerAnchorX = ANCHOR_X;
                markerAnchorY = ANCHOR_Y;
            }
        }
    }

    void start(ClassLoader cl, Context ctx) {
        this.cl = cl;
        this.context = ctx;
        thread = new HandlerThread("kakao-map-capture");
        thread.start();
        handler = new Handler(thread.getLooper());
        handler.post(this::loop);
        mainHandler.post(this::pollScreenCameraOnMain);
        mainHandler.post(this::surfaceLoop);
    }

    // 카카오 화면 지도의 카메라 게터(KNMScene.getCoordinate/getZoom/...)는 화면 scene
    // 핸들로 네이티브를 호출한다. 그 scene 은 카카오 메인/렌더 쪽 소유라, 캡처
    // 스레드에서 직접 읽으면 렌더와 동시 접근이 된다. 메인 스레드에서만 읽어
    // 최신값을 volatile 로 넘기고, 캡처 스레드는 그 스냅샷만 쓴다(최대 1주기 지연).
    private final Handler mainHandler = new Handler(android.os.Looper.getMainLooper());
    private volatile ScreenCamera latestScreenCamera;
    private volatile long latestScreenCameraAt;
    // 카카오 화면 지도가 지금 쓰는 테마(KNMTheme). 주/야 자동 전환을 그대로 따른다.
    private volatile Object latestScreenTheme;
    private volatile long latestScreenThemeAt;

    private void pollScreenCameraOnMain() {
        try {
            latestScreenCamera = readScreenCamera();
            latestScreenCameraAt = android.os.SystemClock.elapsedRealtime();
        } catch (Throwable t) {
            latestScreenCamera = null;
        }
        try {
            Object theme = readScreenTheme();
            if (theme != null) {
                latestScreenTheme = theme;
                latestScreenThemeAt = android.os.SystemClock.elapsedRealtime();
            }
        } catch (Throwable ignored) {
        } finally {
            mainHandler.postDelayed(this::pollScreenCameraOnMain, SCREEN_CAMERA_POLL_MS);
        }
    }

    private ScreenCamera screenCameraSnapshot() {
        ScreenCamera c = latestScreenCamera;
        // 메인 스레드가 막혀 오래된 값이면 쓰지 않고 자체 카메라로 대체한다.
        if (c == null || android.os.SystemClock.elapsedRealtime() - latestScreenCameraAt > 1000) return null;
        return c;
    }

    private long maxFrameMs = 0;
    private long copyCount = 0;
    private final android.graphics.Paint markerFill = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
    private final android.graphics.Paint markerEdge = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
    private final android.graphics.Paint markerShadow = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
    private final android.graphics.Path markerPath = new android.graphics.Path();
    private Object nativeUserLocation;
    private Method nativeLocationPointMethod;
    private Method nativeLocationBearingMethod;
    private Method nativeLocationCombinedMethod;
    private Method nativeLocationVisibleMethod;
    private volatile boolean nativeMarkerActive;
    private boolean nativeMarkerErrorLogged;

    /**
     * 오프스크린 캡처러는 내 위치 마커를 그리지 않는다. 지도는 차량 방위 기준(heading-up)
     * 이므로 anchor 위치에 위쪽을 향한 화살표를 직접 그린다.
     */
    private void drawVehicleMarker(Bitmap bmp) {
        float cx = bmp.getWidth() * (anchorTo != null ? markerAnchorX : 0.5f);
        float cy = bmp.getHeight() * (anchorTo != null ? markerAnchorY : 0.5f);
        float r = bmp.getHeight() * FALLBACK_MARKER_RADIUS_HEIGHT;
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

    private static final class ScreenCamera {
        final double x, y;
        final Float zoom, tilt, bearing;
        final float anchorX, anchorY;

        ScreenCamera(double x, double y, Float zoom, Float tilt, Float bearing,
                     float anchorX, float anchorY) {
            this.x = x; this.y = y; this.zoom = zoom; this.tilt = tilt;
            this.bearing = bearing; this.anchorX = anchorX; this.anchorY = anchorY;
        }
    }

    private ScreenCamera readScreenCamera() {
        WeakReference<Object> sourceRef = screenCameraSource;
        Object source = sourceRef == null ? null : sourceRef.get();
        if (source == null || cl == null) return null;
        try {
            Object api = source.getClass().getMethod("q").invoke(source);
            if (api == null) return null;
            Class<?> apiClass = cl.loadClass("com.kakaomobility.knmsdk.KNMMapApi");
            Object coordinate = apiClass.getMethod("getCoordinate").invoke(api);
            if (coordinate == null) return null;
            Class<?> pointClass = cl.loadClass("com.kakaomobility.knmsdk.utils.KNMPoint");
            Object katec = pointClass.getMethod("toKatec").invoke(coordinate);
            double x = ((Number) pointClass.getMethod("getX").invoke(katec)).doubleValue();
            double y = ((Number) pointClass.getMethod("getY").invoke(katec)).doubleValue();
            Float zoom = (Float) apiClass.getMethod("getZoom").invoke(api);
            Float tilt = (Float) apiClass.getMethod("getTilt").invoke(api);
            Float bearing = (Float) apiClass.getMethod("getBearing").invoke(api);
            float ax = ANCHOR_X, ay = ANCHOR_Y;
            Object anchor = apiClass.getMethod("getAnchor").invoke(api);
            if (anchor != null) {
                double rawX = ((Number) pointClass.getMethod("getX").invoke(anchor)).doubleValue();
                double rawY = ((Number) pointClass.getMethod("getY").invoke(anchor)).doubleValue();
                if (rawX >= 0.0 && rawX <= 1.0 && rawY >= 0.0 && rawY <= 1.0) {
                    ax = (float) rawX;
                    ay = (float) rawY;
                }
            }
            return new ScreenCamera(x, y, zoom, tilt, bearing, ax, ay);
        } catch (Throwable t) {
            if (!screenCameraErrorLogged) {
                screenCameraErrorLogged = true;
                KakaoHudLog.ex("readScreenCamera", t);
            }
            return null;
        }
    }

    /** 안내 중인 KNU 경로를 캡처 지도용 KNMRoute로 변환해 경로선을 표시한다. */
    void updateRoute(Object sdkRoute) {
        pendingSdkRoute = sdkRoute;
        pendingRouteRevision++;
    }

    private void loop() {
        long start = android.os.SystemClock.elapsedRealtime();
        try {
            tick();
        } catch (Throwable t) {
            KakaoHudLog.ex("map loop", t);
        } finally {
            if (handler != null) {
                // 고정 200ms 재귀는 capture 가 200ms 넘게 걸리면 밀려서 끊긴다.
                // 처리시간을 빼고 남은 시간만 쉬어, 실제 프레임 간격을 고르게 한다.
                long cost = android.os.SystemClock.elapsedRealtime() - start;
                long delay = Math.max(33, INTERVAL_MS - cost);   // 최소 33ms(최대 ~30fps 상한)
                handler.postDelayed(this::loop, delay);
            }
        }
    }

    private void tick() {
        if (surfaceMode) return;   // KNMMapSurface 렌더러가 담당(실패하면 false 로 바뀐다)
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
            applyThemeIfNeeded();

            Object update = buildCameraUpdate();
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
            boolean copied = false;
            if (!bmp.isMutable()) {
                Bitmap copy = bmp.copy(Bitmap.Config.ARGB_8888, true);
                bmp.recycle();
                bmp = copy;
                copied = true;
                copyCount++;
                if (copyCount == 1 || copyCount % 100 == 0) {
                    KakaoHudLog.status("capture immutable, copying each frame x" + copyCount);
                }
            }
            drawVehicleMarker(bmp);
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            bmp.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out);
            if (copied) bmp.recycle();
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

    /** 화면 카메라(가까우면) 또는 위치·속도 추정으로 이번 프레임의 카메라를 만든다. */
    private Object buildCameraUpdate() throws Exception {
        // Prefer Kakao's attached screen camera, as TMAP app_sync does.
        // A distant camera is likely a preview/search map, not the driving map.
        ScreenCamera screen = screenCameraSnapshot();
        boolean screenSync = screen != null && Double.isFinite(screen.x)
                && Double.isFinite(screen.y)
                && Math.hypot(screen.x - curX, screen.y - curY) < 300.0;
        if (screenSync && !screenCameraLogged) {
            screenCameraLogged = true;
            KakaoHudLog.line("screen camera sync active zoom=" + screen.zoom
                    + " tilt=" + screen.tilt + " bearing=" + screen.bearing
                    + " anchor=" + screen.anchorX + "," + screen.anchorY
                    + " poseDeltaM=" + (int) Math.hypot(screen.x - curX, screen.y - curY));
        }
        markerAnchorX = screenSync ? screen.anchorX : ANCHOR_X;
        // 카카오 원본 주행 화면을 잡았을 때는 X/Y anchor 모두 그대로 따른다.
        // 이전에 Y만 0.78로 강제해 원본과 차량 위치·앞쪽 시야가 달라졌다.
        markerAnchorY = screenSync ? screen.anchorY : ANCHOR_Y;

        // KATEC 은 미터 단위(x 동쪽, y 북쪽), 방위는 북쪽 기준 시계방향이다.
        double leadM = speedKph >= 3.0 ? speedKph / 3.6 * LEAD_S : 0.0;
        double rad = Math.toRadians(curBearing);
        double drawX = screenSync ? screen.x : curX + leadM * Math.sin(rad);
        double drawY = screenSync ? screen.y : curY + leadM * Math.cos(rad);
        Object point = katecPoint.invoke(pointCompanion, drawX, drawY);
        Object update = targetTo.invoke(cameraCompanion, point);
        if (anchorTo != null) {
            update = anchorTo.invoke(update,
                    new android.graphics.PointF(markerAnchorX, markerAnchorY));
        }
        float bearing = screenSync && screen.bearing != null
                && Float.isFinite(screen.bearing) ? screen.bearing : (float) curBearing;
        float tilt = screenSync && screen.tilt != null
                && Float.isFinite(screen.tilt) ? screen.tilt : TILT;
        update = bearingTo.invoke(update, bearing);
        update = tiltTo.invoke(update, tilt);
        int scale = scaleIndex();
        if (scale != lastScaleIndex) {
            lastScaleIndex = scale;
            KakaoHudLog.status("map scale idx=" + scale + " zoom=" + DRIVE_ZOOM[scale]
                    + " spd=" + (int) speedKph + " turn=" + turnDistanceM);
        }
        float zoom = screenSync && screen.zoom != null && Float.isFinite(screen.zoom)
                && screen.zoom > 0.0f ? screen.zoom : DRIVE_ZOOM[scale];
        update = zoomTo.invoke(update, zoom);
        return update;
    }

    /** 지도 엔진만 MAP_DPI 로 그리게 한 Context(앱 화면 밀도는 그대로 둔다). */
    private Context mapDensityContext() {
        try {
            android.content.res.Configuration config =
                    new android.content.res.Configuration(context.getResources().getConfiguration());
            config.densityDpi = MAP_DPI;
            return context.createConfigurationContext(config);
        } catch (Throwable t) {
            KakaoHudLog.ex("map density context", t);
            return context;
        }
    }

    private void initCapturer() {
        try {
            float density = MAP_DPI / 160f;

            Class<?> capClass = cl.loadClass("com.kakaomobility.knmsdk.capturer.KNMMapCapturer");
            Constructor<?> ctor = capClass.getConstructor(Context.class, float.class, float.class);
            ctor.setAccessible(true);
            capturer = ctor.newInstance(context, density, 1.0f);
            captureMethod = capClass.getMethod("capture", int.class, int.class);
            setRoutesMethod = capClass.getMethod("setRoutes", List.class);
            initCameraReflection();
            Class<?> updClass = cl.loadClass("com.kakaomobility.knmsdk.camera.KNMCameraUpdate");
            moveCameraMethod = capClass.getMethod("moveCamera", updClass);
            setThemeMethod = null;
            appliedSdkRoute = null;
            appliedRouteRevision = -1;

            // 새 캡처러에는 테마를 다시 적용한다(applyThemeIfNeeded).
            appliedTheme = null;

            KakaoHudLog.line("KNMMapCapturer created (density=" + density + ")");
        } catch (Throwable t) {
            KakaoHudLog.ex("initCapturer", t);
            capturer = null;
        }
    }

    /** 카메라·좌표·경로 변환 리플렉션(캡처러·서피스 공용). */
    private void initCameraReflection() throws Exception {
        {
            // 카카오 앱이 실제 주행 지도에 사용하는 동일 변환기.
            Class<?> routeConverter = cl.loadClass("com.kakaomobility.knmsdk.fl0.g");
            convertRoutesMethod = routeConverter.getMethod("f", List.class);

            Class<?> updClass = cl.loadClass("com.kakaomobility.knmsdk.camera.KNMCameraUpdate");

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
        }
    }

    private void applyRouteIfNeeded() {
        if (setRoutesMethod == null || convertRoutesMethod == null) return;
        Object route = pendingSdkRoute;
        long revision = pendingRouteRevision;
        if (route == appliedSdkRoute && revision == appliedRouteRevision) return;
        boolean identityChanged = route != appliedSdkRoute;
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
            setRoutesMethod.invoke(mapTarget(), mapRoutes);
            appliedSdkRoute = route;
            appliedRouteRevision = revision;
            if (identityChanged) {
                KakaoHudLog.line("map route applied count=" + mapRoutes.size());
            } else {
                KakaoHudLog.status("map route refreshed count=" + mapRoutes.size());
            }
        } catch (Throwable t) {
            KakaoHudLog.ex("map route", t);
        }
    }

    // ---- 지도 엔진 직접 렌더(KNMMapSurface) ----
    // 안드로이드 오토 지도(NPMapSurfaceV2)가 쓰는 KNMMapSurface 를 ImageReader 표면에 하나
    // 더 만든다. 엔진이 자기 렌더 스레드에서 계속 그리고, 우리는 카메라·경로·테마만
    // 넣는다(티맵 TmapMapRender 와 같은 방식). 매 프레임
    // capture() 로 찍는 캡처러보다 가볍고 끊김이 적다. 실패하면 캡처러로 돌아간다.
    private static final long SURFACE_TICK_MS = 100;
    private static final long SURFACE_FRAME_MS = 200;        // 최대 5fps 전송
    private static final long SURFACE_READY_TIMEOUT_MS = 8000;
    private static final long SURFACE_FRAME_TIMEOUT_MS = 8000;
    private volatile boolean surfaceMode = true;
    private Object mapSurface;                 // KNMMapSurface
    private android.media.ImageReader reader;
    private HandlerThread imageThread;
    private Handler imageHandler;
    private volatile boolean surfaceReady;
    private long surfaceCreatedAt;
    private volatile long surfaceReadyAt;
    // 초기화 직후 프레임은 기본 카메라·덜 받은 타일일 수 있어 보내지 않는다.
    private static final long SURFACE_WARMUP_MS = 700L;
    private volatile long surfaceFrameAt;
    private volatile byte[] lastSurfaceJpeg;
    private volatile byte[] warmSurfaceJpeg;
    private long lastSurfaceEncodeAt;
    private long surfaceFrames;
    private long nextSurfaceAttemptMs;

    private boolean sdkReadyLogged;
    private long sdkWaitSince;

    /** KNMSDK 초기화 완료 여부. 확인할 방법이 없으면 true(바로 시도). */
    private boolean sdkReady() {
        try {
            Class<?> sdkClass = cl.loadClass("com.kakaomobility.knmsdk.KNMSDK");
            Object sdk = sdkClass.getField("INSTANCE").get(null);
            boolean ready;
            try {
                ready = Boolean.TRUE.equals(sdkClass.getMethod("isInitialized").invoke(sdk));
            } catch (NoSuchMethodException noFlag) {
                Object state = sdkClass.getMethod("getInitState$knmsdk_knmsdkPublicRelease").invoke(sdk);
                ready = state instanceof Integer && (Integer) state == 2;
            }
            if (ready && !sdkReadyLogged) {
                sdkReadyLogged = true;
                KakaoHudLog.line("map render: KNMSDK initialized");
            }
            return ready;
        } catch (Throwable t) {
            return true;
        }
    }

    private Object mapTarget() {
        return surfaceMode ? mapSurface : capturer;
    }

    /** 메인 스레드 100ms 주기: 서피스 생성·카메라·경로·테마. */
    private void surfaceLoop() {
        if (!surfaceMode) return;   // 캡처러로 넘어갔다
        try {
            surfaceTick();
        } catch (Throwable t) {
            failSurface("tick", t);
        }
        if (surfaceMode) mainHandler.postDelayed(this::surfaceLoop, SURFACE_TICK_MS);
    }

    private void surfaceTick() throws Exception {
        if (!client.ready() || !hasPose) return;
        long now = android.os.SystemClock.elapsedRealtime();
        if (mapSurface == null) {
            // 지도 SDK 초기화 전에 만들면 실패해 캡처러로 영영 넘어가 버린다. 준비를 기다린다.
            // 초기화 표시를 20초 넘게 못 읽으면(표시 방식이 다를 수 있다) 그냥 시도한다.
            if (sdkWaitSince == 0) sdkWaitSince = now;
            if (now >= nextSurfaceAttemptMs && (sdkReady() || now - sdkWaitSince > 20000)) {
                nextSurfaceAttemptMs = now + INIT_RETRY_MS;
                initSurface();
            }
            return;
        }
        if (!surfaceReady) {
            if (now - surfaceCreatedAt > SURFACE_READY_TIMEOUT_MS) {
                failSurface("init timeout", null);
            }
            return;
        }
        applyRouteIfNeeded();
        applyThemeIfNeeded();
        moveCameraMethod.invoke(mapSurface, buildCameraUpdate(), false);
        updateNativeUserMarker();
        if (surfaceFrames == 0 && now - surfaceReadyAt > SURFACE_WARMUP_MS + SURFACE_FRAME_TIMEOUT_MS) {
            failSurface("no frames", null);
        }
    }

    private void initSurface() throws Exception {
        if (cl == null || context == null) return;
        initCameraReflection();
        Class<?> surfaceClass = cl.loadClass("com.kakaomobility.knmsdk.KNMMapSurface");
        Class<?> sceneClass = cl.loadClass("com.kakaomobility.knmsdk.scene.KNMScene");
        Class<?> pointClass = cl.loadClass("com.kakaomobility.knmsdk.utils.KNMPoint");
        Class<?> themeClass = cl.loadClass("com.kakaomobility.knmsdk.configurations.KNMTheme");
        Class<?> updClass = cl.loadClass("com.kakaomobility.knmsdk.camera.KNMCameraUpdate");
        Class<?> function1 = cl.loadClass("kotlin.jvm.functions.Function1");

        imageThread = new HandlerThread("kakao-hud-map-render");
        imageThread.start();
        imageHandler = new Handler(imageThread.getLooper());
        reader = android.media.ImageReader.newInstance(WIDTH, HEIGHT,
                android.graphics.PixelFormat.RGBA_8888, 3);
        reader.setOnImageAvailableListener(this::onSurfaceImage, imageHandler);

        Context mapContext = mapDensityContext();
        Object scene = sceneClass.getConstructor(Context.class, boolean.class).newInstance(mapContext, true);
        mapSurface = surfaceClass.getConstructor(android.view.Surface.class, Context.class, sceneClass)
                .newInstance(reader.getSurface(), mapContext, scene);
        surfaceCreatedAt = android.os.SystemClock.elapsedRealtime();
        surfaceReady = false;
        surfaceFrames = 0;
        moveCameraMethod = surfaceClass.getMethod("moveCamera", updClass, boolean.class);
        setRoutesMethod = surfaceClass.getMethod("setRoutes", List.class);
        setThemeMethod = null;
        appliedTheme = null;
        appliedSdkRoute = null;
        appliedRouteRevision = -1;

        final Object target = mapSurface;
        Object complete = java.lang.reflect.Proxy.newProxyInstance(cl, new Class<?>[]{function1},
                (proxy, method, args) -> {
                    if ("invoke".equals(method.getName())) {
                        final Object error = args == null || args.length == 0 ? null : args[0];
                        mainHandler.post(() -> onSurfaceInit(target, error));
                        return kotlinUnit();
                    }
                    switch (method.getName()) {
                        case "hashCode": return System.identityHashCode(proxy);
                        case "equals": return args != null && args.length == 1 && proxy == args[0];
                        default: return "KakaoHudSurfaceInit";
                    }
                });
        Object start = katecPoint.invoke(pointCompanion, curX, curY);
        surfaceClass.getMethod("init", pointClass, themeClass, Float.class, function1)
                .invoke(mapSurface, start, null, null, complete);
        KakaoHudLog.xposed("map render surface created " + WIDTH + "x" + HEIGHT);
    }

    private void onSurfaceInit(Object target, Object error) {
        if (target != mapSurface || !surfaceMode) return;
        if (error != null) {
            failSurface("init error " + error, null);
            return;
        }
        try {
            mapSurface.getClass().getMethod("resume").invoke(mapSurface);
        } catch (Throwable ignored) {
            // resume 은 렌더 루프를 깨우는 보조 호출이다.
        }
        prepareNativeUserMarker();
        surfaceReady = true;
        surfaceReadyAt = android.os.SystemClock.elapsedRealtime();
        imageHandler.postDelayed(this::surfaceRepeatLoop, SURFACE_FRAME_MS);
        KakaoHudLog.xposed("map render surface ready");
    }

    /** ImageReader 스레드. 최대 5fps 로 JPEG 인코딩·전송. */
    private void onSurfaceImage(android.media.ImageReader r) {
        android.media.Image image = null;
        try {
            image = r.acquireLatestImage();
            if (image == null || !surfaceMode) return;
            long now = android.os.SystemClock.elapsedRealtime();
            if (now - lastSurfaceEncodeAt < (SURFACE_FRAME_MS * 9) / 10) return;
            long ready = surfaceReadyAt;
            if (!surfaceReady || ready == 0L) return;
            boolean warmingUp = now - ready < SURFACE_WARMUP_MS;
            lastSurfaceEncodeAt = now;
            android.media.Image.Plane plane = image.getPlanes()[0];
            int w = image.getWidth(), h = image.getHeight();
            Bitmap padded = Bitmap.createBitmap(plane.getRowStride() / plane.getPixelStride(), h,
                    Bitmap.Config.ARGB_8888);
            padded.copyPixelsFromBuffer(plane.getBuffer());
            Bitmap frame = padded.getWidth() == w ? padded : Bitmap.createBitmap(padded, 0, 0, w, h);
            if (frame == padded) {
                if (!nativeMarkerActive) drawVehicleMarker(frame);
            } else {
                frame = frame.copy(Bitmap.Config.ARGB_8888, true);
                if (!nativeMarkerActive) drawVehicleMarker(frame);
            }
            ByteArrayOutputStream out = new ByteArrayOutputStream(80000);
            frame.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out);
            if (frame != padded) frame.recycle();
            padded.recycle();
            byte[] jpeg = out.toByteArray();
            if (warmingUp) {
                // 아직 보내지 않는다. 정차 중이면 이후 새 프레임이 없을 수 있어 마지막
                // 것을 남겨 두고, 워밍업이 끝나면 repeat 루프가 첫 프레임으로 보낸다.
                warmSurfaceJpeg = jpeg;
                return;
            }
            publishSurfaceFrame(jpeg, now, w, h);
        } catch (Throwable t) {
            KakaoHudLog.status("map render frame: " + t);
        } finally {
            if (image != null) image.close();
        }
    }

    /** 이미지 스레드. */
    private void publishSurfaceFrame(byte[] jpeg, long now, int w, int h) {
        warmSurfaceJpeg = null;
        lastSurfaceJpeg = jpeg;
        surfaceFrameAt = now;
        if (client.ready()) client.sendMap(jpeg);
        if (++surfaceFrames == 1) {
            KakaoHudLog.xposed("first rendered map frame sent " + w + "x" + h + " " + jpeg.length + "B");
        } else if (surfaceFrames % 300 == 0) {
            KakaoHudLog.line("rendered map frames: " + surfaceFrames);
        }
    }

    /** 지도가 멈춰 있으면 엔진이 새로 안 그린다. 마지막 프레임을 다시 보내 EON 이 지우지 않게. */
    private void surfaceRepeatLoop() {
        if (!surfaceMode) return;
        long now = android.os.SystemClock.elapsedRealtime();
        byte[] warm = warmSurfaceJpeg;
        if (surfaceFrames == 0 && warm != null && surfaceReadyAt != 0L
                && now - surfaceReadyAt >= SURFACE_WARMUP_MS) {
            publishSurfaceFrame(warm, now, WIDTH, HEIGHT);
        }
        byte[] last = lastSurfaceJpeg;
        if (last != null && client.ready()
                && android.os.SystemClock.elapsedRealtime() - surfaceFrameAt >= SURFACE_FRAME_MS * 2) {
            client.sendMap(last);
        }
        Handler h = imageHandler;
        if (h != null) h.postDelayed(this::surfaceRepeatLoop, SURFACE_FRAME_MS);
    }

    /** 서피스 렌더를 접고 캡처러(기존 방식)로 넘긴다. 메인 스레드. */
    private void failSurface(String where, Throwable error) {
        KakaoHudLog.xposed("map render " + where + " failed (falling back to KNMMapCapturer)"
                + (error == null ? "" : ": " + error));
        surfaceMode = false;
        surfaceReady = false;
        Object surface = mapSurface;
        mapSurface = null;
        try { if (surface != null) surface.getClass().getMethod("destroySurface").invoke(surface); } catch (Throwable ignored) { }
        try { if (surface != null) surface.getClass().getMethod("destroy").invoke(surface); } catch (Throwable ignored) { }
        try { if (reader != null) reader.close(); } catch (Throwable ignored) { }
        try { if (imageThread != null) imageThread.quitSafely(); } catch (Throwable ignored) { }
        reader = null;
        imageThread = null;
        imageHandler = null;
        lastSurfaceJpeg = null;
        warmSurfaceJpeg = null;
        // 캡처러 쪽 상태를 처음부터 다시 잡는다.
        moveCameraMethod = null;
        setRoutesMethod = null;
        setThemeMethod = null;
        appliedTheme = null;
        appliedSdkRoute = null;
        appliedRouteRevision = -1;
        nativeUserLocation = null;
        nativeLocationPointMethod = null;
        nativeLocationBearingMethod = null;
        nativeLocationCombinedMethod = null;
        nativeLocationVisibleMethod = null;
        nativeMarkerActive = false;
    }

    /**
     * KNMMapSurface의 순정 내 위치 마커를 쓸 수 있는 SDK에서는 원본 표시를 쓴다.
     * 카카오 SDK 버전별로 난독화 메서드가 달라지므로, 위치와 방위 setter를 둘 다
     * 안전하게 찾은 경우에만 켠다. 하나라도 부족하면 축소한 자체 마커로 폴백한다.
     */
    private void prepareNativeUserMarker() {
        nativeMarkerActive = false;
        try {
            Object user = mapSurface.getClass().getMethod("getUserLocation").invoke(mapSurface);
            if (user == null) return;
            Class<?> pointClass = cl.loadClass("com.kakaomobility.knmsdk.utils.KNMPoint");
            Method visible = user.getClass().getMethod("setVisible", boolean.class);
            Method point = null, bearing = null, combined = null;
            for (Method method : user.getClass().getMethods()) {
                String name = method.getName().toLowerCase(java.util.Locale.US);
                Class<?>[] args = method.getParameterTypes();
                boolean locationName = name.contains("location") || name.contains("position")
                        || name.contains("coordinate") || name.contains("point");
                boolean bearingName = name.contains("bearing") || name.contains("heading")
                        || name.contains("direction") || name.contains("angle");
                if (locationName && args.length == 1 && args[0].isAssignableFrom(pointClass)) {
                    point = method;
                } else if (bearingName && args.length == 1 && isAngleType(args[0])) {
                    bearing = method;
                } else if (locationName && args.length == 2
                        && args[0].isAssignableFrom(pointClass)
                        && isAngleType(args[1])) {
                    combined = method;
                }
            }
            nativeUserLocation = user;
            nativeLocationVisibleMethod = visible;
            nativeLocationPointMethod = point;
            nativeLocationBearingMethod = bearing;
            nativeLocationCombinedMethod = combined;
            if (combined == null && (point == null || bearing == null)) {
                visible.invoke(user, false);
                KakaoHudLog.line("map render: native user marker setters unavailable; using fallback");
                return;
            }
            updateNativeUserMarker();
            if (nativeMarkerActive) KakaoHudLog.line("map render: native user marker active");
        } catch (Throwable t) {
            hideNativeUserMarker();
            KakaoHudLog.line("map render: native user marker unavailable: " + t);
        }
    }

    private void updateNativeUserMarker() {
        Object user = nativeUserLocation;
        if (user == null || nativeLocationVisibleMethod == null) return;
        try {
            Object point = katecPoint.invoke(pointCompanion, curX, curY);
            float bearing = (float) curBearing;
            if (nativeLocationCombinedMethod != null) {
                Class<?> angleType = nativeLocationCombinedMethod.getParameterTypes()[1];
                nativeLocationCombinedMethod.invoke(user, point, angleValue(angleType, bearing));
            } else {
                nativeLocationPointMethod.invoke(user, point);
                Class<?> angleType = nativeLocationBearingMethod.getParameterTypes()[0];
                nativeLocationBearingMethod.invoke(user, angleValue(angleType, bearing));
            }
            nativeLocationVisibleMethod.invoke(user, true);
            nativeMarkerActive = true;
        } catch (Throwable t) {
            hideNativeUserMarker();
            if (!nativeMarkerErrorLogged) {
                nativeMarkerErrorLogged = true;
                KakaoHudLog.ex("native user marker", t);
            }
        }
    }

    private void hideNativeUserMarker() {
        nativeMarkerActive = false;
        try {
            if (nativeUserLocation != null && nativeLocationVisibleMethod != null) {
                nativeLocationVisibleMethod.invoke(nativeUserLocation, false);
            }
        } catch (Throwable ignored) { }
    }

    private static boolean isAngleType(Class<?> type) {
        return type == float.class || type == Float.class
                || type == double.class || type == Double.class;
    }

    private static Object angleValue(Class<?> type, float value) {
        if (type == double.class || type == Double.class) return Double.valueOf(value);
        return Float.valueOf(value);
    }

    private Object kotlinUnit() {
        try {
            return cl.loadClass("kotlin.Unit").getField("INSTANCE").get(null);
        } catch (Throwable t) {
            return null;
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

    // ---- 주/야 테마 ----
    // 1) 카카오 화면 지도의 현재 테마 객체를 그대로 캡처러에 적용한다.
    // 2) 그걸 못 읽으면 해 뜨고 지는 시각으로 day/night 테마를 고른다.
    //    (예전엔 "day" 테마를 처음 한 번만 넣어 야간에도 낮 지도였다.)
    private static final long SCREEN_THEME_MAX_AGE_MS = 5000;
    private Object appliedTheme;
    private Method setThemeMethod;
    private Method screenThemeGetter;
    private boolean screenThemeSearched, themeLogged, nightThemeMissingLogged;
    private Object dayTheme, nightTheme;
    private boolean sdkThemesLoaded;

    private Object readScreenTheme() throws Exception {
        WeakReference<Object> sourceRef = screenCameraSource;
        Object source = sourceRef == null ? null : sourceRef.get();
        if (source == null || cl == null) return null;
        Object api = source.getClass().getMethod("q").invoke(source);
        if (api == null) return null;
        if (!screenThemeSearched) {
            screenThemeSearched = true;
            Class<?> themeClass = cl.loadClass("com.kakaomobility.knmsdk.configurations.KNMTheme");
            for (Method m : api.getClass().getMethods()) {
                if (m.getParameterTypes().length == 0 && themeClass.isAssignableFrom(m.getReturnType())) {
                    screenThemeGetter = m;
                    break;
                }
            }
            KakaoHudLog.line(screenThemeGetter != null
                    ? "theme: screen map getter " + screenThemeGetter.getName()
                    : "theme: screen map has no KNMTheme getter, using sun times");
        }
        return screenThemeGetter == null ? null : screenThemeGetter.invoke(api);
    }

    private void applyThemeIfNeeded() {
        try {
            if (setThemeMethod == null) {
                Class<?> themeClass = cl.loadClass("com.kakaomobility.knmsdk.configurations.KNMTheme");
                setThemeMethod = mapTarget().getClass().getMethod("setTheme", themeClass);
            }
            Object screen = latestScreenTheme;
            boolean fromScreen = screen != null && android.os.SystemClock.elapsedRealtime()
                    - latestScreenThemeAt <= SCREEN_THEME_MAX_AGE_MS;
            Object desired;
            boolean night = false;
            if (fromScreen) {
                desired = screen;
            } else {
                loadSdkThemes();
                night = SunTimes.isNight(System.currentTimeMillis());
                desired = night && nightTheme != null ? nightTheme : dayTheme;
                if (night && nightTheme == null && !nightThemeMissingLogged) {
                    nightThemeMissingLogged = true;
                    KakaoHudLog.line("theme: SDK night theme not found");
                }
            }
            if (desired == null || desired == appliedTheme) return;
            setThemeMethod.invoke(mapTarget(), desired);
            appliedTheme = desired;
            KakaoHudLog.status("theme applied: " + (fromScreen ? "screen" : (night ? "night(sun)" : "day(sun)")));
        } catch (Throwable t) {
            if (!themeLogged) {
                themeLogged = true;
                KakaoHudLog.ex("theme", t);
            }
        }
    }

    private void loadSdkThemes() {
        if (sdkThemesLoaded) return;
        sdkThemesLoaded = true;
        try {
            Class<?> sdkClass = cl.loadClass("com.kakaomobility.knmsdk.KNMSDK");
            Object sdk = sdkClass.getField("INSTANCE").get(null);
            Method getTheme = sdkClass.getMethod("getTheme", String.class, String.class);
            dayTheme = getTheme.invoke(sdk, "default", "day");
            nightTheme = getTheme.invoke(sdk, "default", "night");
            KakaoHudLog.line("theme: sdk day=" + (dayTheme != null) + " night=" + (nightTheme != null));
        } catch (Throwable t) {
            KakaoHudLog.status("theme sdk skip: " + t.getClass().getSimpleName());
        }
    }
}
