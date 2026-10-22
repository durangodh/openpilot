package ai.comma.tmaphud;

import android.content.Context;
import android.content.res.Configuration;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.graphics.Point;
import android.media.Image;
import android.media.ImageReader;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.os.SystemClock;
import android.view.Surface;
import android.view.View;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.lang.reflect.Array;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.nio.ByteBuffer;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * map_main 을 캐롯 패치판(CarrotMapRenderStream)과 같은 방식으로 만든다.
 *
 * 티맵 지도 엔진(NaviMapEngine)을 하나 더 만들어 640×384 ImageReader 표면에 직접
 * 그리고, NavigationManager.attachMapView 로 붙여 경로선(setDrawRouteData)과 차량
 * 위치(ArrayLocationProvider)를 받는다. 화면 지도가 보이면 그 카메라(레벨·기울기·
 * 회전·중심·FOV)를 그대로 따르고, 티맵이 백그라운드면 엔진 자체 주행 모드로 차량을
 * 따라간다. 화면이 꺼져도 지도가 나온다.
 *
 * 모든 호출은 리플렉션이다(난독화되지 않은 VSM/엔진 공개 API). 티맵 내부에 지도
 * 보기를 하나 더 등록하므로 확인한 11.8 계열에서만 켠다(TmapHudModule.mapRenderAllowed).
 * 시작에 실패하면 TmapMapCapture(PixelCopy)로 돌아간다.
 */
final class TmapMapRender {
    // carrot_navi_server manifest(map_main) + CarrotStreamConfig 기본값
    // 12.3인치 HUD 6:4 지도칸(760x720)과 1:1. DPI 는 그대로라 글자·차 아이콘 크기는 같다.
    private static final int WIDTH = 760, HEIGHT = 720, DPI = 160, FPS = 5, JPEG_QUALITY = 65;
    // HUD 네비모드(지도 1536x720 칸): 같은 화소 수로 가로를 넓힌다(EON 전송량 그대로).
    private static final int WIDE_WIDTH = 1080, WIDE_HEIGHT = 506;
    /** 엔진이 지금 그리는 크기. EON 이 알려 준 HUD 모드가 바뀌면 syncSize 가 바꾼다. */
    private int renderW = WIDTH, renderH = HEIGHT;
    /** 엔진에 넣은 밀도. EON 설정(지도 크기)이 바뀌면 syncSize 가 화면을 다시 만든다. */
    private int renderDpi = DPI;
    private volatile int frameW = WIDTH, frameH = HEIGHT;

    /** 휴대폰 화면 밀도(dpi). 티맵 앱 원본 지도(MapViewStreaming)가 이 밀도로 그려진다. */
    private static volatile int phoneDpi = 420;

    /**
     * HUD 지도 엔진 밀도. 주행모드는 DPI(160) 그대로. 네비모드는 EON 설정
     * (EonClusterHudMapScaleTmap, 휴대폰 앱 원본 축척 대비 %)이고, HUD 가 1080 폭 프레임을
     * 1536 폭으로 늘리는 것까지 뺀다. 예: 420dpi 폰, 70% → 420 × 0.7 × 1080/1536 ≈ 207dpi.
     */
    private static int mapDpi() {
        return EonDiscovery.wideMap
                ? Math.round(phoneDpi * EonDiscovery.mapScale * WIDE_WIDTH / 1536f) : DPI;
    }

    private static int targetWidth() {
        return EonDiscovery.wideMap ? WIDE_WIDTH : WIDTH;
    }

    private static int targetHeight() {
        return EonDiscovery.wideMap ? WIDE_HEIGHT : HEIGHT;
    }
    private static final float FOV = 40f, SCREEN_CENTER_Y = 0.8f;
    private static final int OBJECT_THEME_DAY = 5, OBJECT_THEME_NIGHT = 6;
    private static final int ROUTE_LINE_WIDTH = 166;

    private final TmapNaviClient client;
    private final TmapMapCapture screen;
    private final Handler main = new Handler(Looper.getMainLooper());
    private volatile boolean running;
    private ClassLoader cl;
    private Context context;
    private Context engineContext;
    private Object mapProxy;             // NavigationManager 에 붙인 MapViewInterface
    private Class<?> mapViewInterfaceClass;
    private int loopGeneration;

    private Object engine;              // NaviMapEngine
    private Object provider;            // ArrayLocationProvider<MatchedLocation>
    private Class<?> matchedLocationClass;
    private Class<?> locationArrayClass;
    private ImageReader reader;
    private HandlerThread imageThread;
    private boolean themesReady;
    private Boolean routeTrafficVisible;
    private boolean background = true;
    private long lastLocationAt;
    private long lastEncodeAt;
    private volatile byte[] lastFrame;
    private volatile long lastFrameAt;
    private Handler imageHandler;
    private long sequence;
    private long frames;
    // 엔진 지도가 타일을 받고 카메라를 맞출 때까지는 화면 캡처를 계속 보낸다.
    // 그 뒤 첫 엔진 프레임을 보내는 순간 캡처를 멈춰, 반쯤 그려진 지도나 기본
    // 카메라 장면이 HUD 에 끼어들지 않게 한다.
    private static final long WARMUP_MS = 700L;
    private volatile long startedAt;
    private volatile boolean night;

    TmapMapRender(TmapNaviClient client, TmapMapCapture screen) {
        this.client = client;
        this.screen = screen;
    }

    private int failures;
    private long nextRetryAt;

    /**
     * 주행 데이터가 들어오면(지도 SDK 초기화 뒤) 상태 스레드에서 부른다. 메인 스레드에서
     * 엔진을 만들고, 실패하면 화면 캡처로 돌아간 뒤 30초 후 다시 해 본다(최대 3번).
     */
    void maybeStart(final Context ctx, final ClassLoader loader) {
        if (running || ctx == null || loader == null || failures >= 3) return;
        if (SystemClock.elapsedRealtime() < nextRetryAt) return;
        // EON 방송(1초 주기)을 받기 전에 시작하면 HUD 네비모드를 모른 채 주행모드 크기·밀도로
        // 엔진이 만들어진다. 이 엔진은 시작 뒤 밀도 변경을 무시해 네비모드 지도가 크게 나왔다.
        if (!EonDiscovery.received) return;
        try {
            int dpi = ctx.getResources().getDisplayMetrics().densityDpi;
            if (dpi > 0) phoneDpi = dpi;
        } catch (Throwable ignored) {
            // 기본값(420) 사용
        }
        start(ctx, loader);
    }

    private void start(final Context ctx, final ClassLoader loader) {
        running = true;
        main.post(() -> {
            try {
                initialize(ctx.getApplicationContext(), loader);
                startedAt = SystemClock.elapsedRealtime();
                TmapHudLog.xposed("map render engine started " + renderW + "x" + renderH + "@" + FPS);
            } catch (Throwable error) {
                running = false;
                failures++;
                nextRetryAt = SystemClock.elapsedRealtime() + 30000;
                screen.setSuspended(false);
                TmapHudLog.xposed("map render engine failed (falling back to screen capture): " + error);
                release();
            }
        });
    }

    private void initialize(Context ctx, ClassLoader loader) throws Exception {
        cl = loader;
        context = ctx;
        imageThread = new HandlerThread("tmap-hud-map-render");
        imageThread.start();
        imageHandler = new Handler(imageThread.getLooper());
        renderW = targetWidth();
        renderH = targetHeight();
        renderDpi = mapDpi();
        reader = ImageReader.newInstance(renderW, renderH, PixelFormat.RGBA_8888, 3);
        reader.setOnImageAvailableListener(this::onImageAvailable, imageHandler);

        // The native renderer may cache density in its constructor. Changing only
        // ViewSetting afterwards is too late for those versions of the engine.
        Configuration config = new Configuration(ctx.getResources().getConfiguration());
        config.densityDpi = mapDpi();
        engineContext = ctx.createConfigurationContext(config);
        engine = cl.loadClass("com.skt.tmap.vsm.map.NaviMapEngine").getConstructor(Context.class)
                .newInstance(engineContext);
        initializeObjectThemes(ctx);
        inv(engine, "setNaviMoveMode", 0);
        inv(engine, "setNaviViewMode", 3);
        syncRouteTraffic(mainEngine());

        provider = cl.loadClass("com.skt.tmap.navirenderer.location.ArrayLocationProvider").getConstructor().newInstance();
        matchedLocationClass = cl.loadClass("com.skt.tmap.navirenderer.location.MatchedLocation");
        locationArrayClass = Array.newInstance(cl.loadClass("com.skt.tmap.vsm.location.VSMLocationData"), 0).getClass();
        Object locationManager = inv(engine, "getLocationManager");
        if (locationManager != null) {
            invTyped(locationManager, "setLocationProvider",
                    new Class<?>[]{cl.loadClass("com.skt.tmap.vsm.location.VSMLocationProvider")}, provider);
            Object component = inv(locationManager, "getLocationComponent");
            if (component != null) {
                float icon = vehicleIconSize();
                inv(component, "setIconSize", icon, icon);
                inv(component, "setIconVisible", true);
                inv(component, "setAccuracyVisible", false);
            }
        }
        Object settings = inv(engine, "getViewSetting");
        if (settings != null) {
            inv(settings, "setFov", FOV);
            inv(settings, "setDensityDpi", mapDpi());
            Object camera = cl.loadClass("com.skt.tmap.vsm.map.CameraConfig").getConstructor().newInstance();
            float[] angles = new float[25];
            for (int i = 0; i < angles.length; i++) angles[i] = i < 6 ? 30f : 64f;
            inv(camera, "setMax3dAngles", (Object) angles);
            inv(settings, "setCameraConfig", camera);
        }
        Object surface = inv(engine, "getSurface");
        invTyped(surface, "surfaceCreated", new Class<?>[]{Surface.class}, reader.getSurface());
        inv(surface, "surfaceChanged", renderW, renderH);
        inv(engine, "setScreenCenter", new Point(renderW / 2, Math.round(renderH * SCREEN_CENTER_Y)));
        invTyped(engine, "onResume", new Class<?>[0]);

        Object nav = navigationManager();
        night = Boolean.TRUE.equals(inv(inv(nav, "getNaviConfigData"), "getNightMode"));
        applyMapStyle();

        Class<?> mapViewInterface = cl.loadClass("com.skt.tmap.engine.navigation.MapViewInterface");
        Object proxy = Proxy.newProxyInstance(cl, new Class<?>[]{mapViewInterface},
                (p, method, args) -> onMapViewCall(p, method, args));
        invTyped(nav, "attachMapView", new Class<?>[]{mapViewInterface, boolean.class}, proxy, false);
        mapProxy = proxy;
        mapViewInterfaceClass = mapViewInterface;

        syncCamera();
        // 엔진을 다시 만들면 예전 주기 작업은 세대 번호가 달라 스스로 멈춘다.
        final int gen = ++loopGeneration;
        main.postDelayed(() -> syncLoop(gen), 1000 / FPS);
        final Handler h = imageHandler;
        h.postDelayed(() -> repeatLoop(gen, h), 1000 / FPS);
    }

    /**
     * 지도가 멈춰 있으면 엔진이 새 프레임을 그리지 않을 수 있다. 패치판(emitFrameLoop)처럼
     * 마지막 프레임을 같은 간격으로 다시 보내 EON 이 지도를 오래된 것으로 지우지 않게 한다.
     */
    private void repeatLoop(int gen, Handler h) {
        if (!running || gen != loopGeneration) return;
        byte[] last = lastFrame;
        if (last != null && client.ready() && SystemClock.elapsedRealtime() - lastFrameAt >= 2000L / FPS) {
            client.sendMap(Cnv2.frame(Cnv2.TYPE_IMAGE, Cnv2.FORMAT_JPEG, sequence++, last, frameW, frameH));
        }
        h.postDelayed(() -> repeatLoop(gen, h), 1000 / FPS);
    }

    // ---- MapViewInterface(NavigationManager 콜백) ----

    private Object onMapViewCall(Object proxy, Method method, Object[] args) {
        String name = method.getName();
        try {
            switch (name) {
                case "getName":
                    return "TmapHudMapRender";
                case "hashCode":
                    return System.identityHashCode(proxy);
                case "equals":
                    return args != null && args.length == 1 && proxy == args[0];
                case "toString":
                    return "TmapHudMapRender";
                case "clearRouteRenderData":
                    inv(engine, "drawRouteCancel", false);
                    return null;
                case "onLocationChanged":
                    onLocationChanged(args != null ? args[0] : null);
                    return null;
                case "setNightMode":
                    night = Boolean.TRUE.equals(args[0]);
                    applyMapStyle();
                    return null;
                case "setRouteRenderData":
                    setRouteRenderData((Integer) args[0], (Boolean) args[1], (Object[]) args[2], (Boolean) args[4]);
                    return null;
                case "setRouteResult":
                    setRouteResult((Integer) args[0], (Boolean) args[1], args[2], (Boolean) args[3]);
                    return null;
                case "updatePosition":
                    main.post(this::syncCamera);
                    return null;
                default:
                    return null;   // setAlternativeRoute, setTilt, setZoomLevel, updateSDI: 패치판도 무시
            }
        } catch (Throwable error) {
            TmapHudLog.ex("map render " + name, error);
            return null;
        }
    }

    /** TmapVsmMapViewWrapper.b 와 같은 변환: 맵매칭 위치들 → MatchedLocation[]. */
    private void onLocationChanged(Object data) throws Exception {
        if (!(data instanceof Object[]) || ((Object[]) data).length == 0 || provider == null) return;
        Object[] points = (Object[]) data;
        Object first = points[0];
        double lat0 = TmapBridge.doubleField(first, "latitude"), lon0 = TmapBridge.doubleField(first, "longitude");
        if (lat0 == 0.0 && lon0 == 0.0) return;
        long now = System.currentTimeMillis();
        long interval = lastLocationAt == 0 ? (long) TmapBridge.intField(first, "intervalMilliseconds") * points.length
                : now - lastLocationAt;
        if (interval > 2000) interval = 1000;
        int tvasId = TmapBridge.intField(first, "tvasId");
        Object array = Array.newInstance(matchedLocationClass, points.length);
        java.lang.reflect.Constructor<?> ctor = matchedLocationClass.getConstructor(
                double.class, double.class, float.class, float.class, int.class, int.class);
        for (int i = 0; i < points.length; i++) {
            Object p = points[i];
            Array.set(array, i, ctor.newInstance(TmapBridge.doubleField(p, "longitude"),
                    TmapBridge.doubleField(p, "latitude"), (float) TmapBridge.doubleField(p, "angle"),
                    (float) TmapBridge.doubleField(p, "accuracy"), TmapBridge.intField(p, "index"), tvasId));
        }
        // ArrayLocationProvider<T extends VSMLocationData>.setLocationData(T[], long): 지워진 타입은 VSMLocationData[].
        invTyped(provider, "setLocationData", new Class<?>[]{locationArrayClass, long.class}, array, interval);
        lastLocationAt = now;
    }

    private void setRouteRenderData(int routeIndex, boolean reroute, Object[] routeData, boolean hasAlternative) {
        if (engine == null || routeData == null) return;
        syncRouteTraffic(mainEngine());
        ByteBuffer[] buffers = new ByteBuffer[routeData.length];
        for (int i = 0; i < routeData.length; i++) {
            Object b = routeData[i] == null ? null : inv(routeData[i], "getBuffer");
            buffers[i] = b instanceof ByteBuffer ? (ByteBuffer) b : null;
        }
        invTyped(engine, "setDrawRouteData", new Class<?>[]{ByteBuffer[].class, boolean.class}, buffers, reroute);
        int selected = hasAlternative ? 0 : routeIndex;
        inv(engine, "selectRouteLine", selected);
        inv(engine, "applySelectRouteLine", selected);
        inv(engine, "setShowRoute", true, ROUTE_LINE_WIDTH);
    }

    /** TmapVsmMapViewWrapper.a: RouteResult.routeInfos[i].renderData. */
    private void setRouteResult(int routeIndex, boolean reroute, Object routeResult, boolean hasAlternative) {
        Object infos = TmapBridge.field(routeResult, "routeInfos");
        if (!(infos instanceof List) || ((List<?>) infos).isEmpty()) return;
        List<?> list = (List<?>) infos;
        Object[] data = new Object[list.size()];
        for (int i = 0; i < data.length; i++) data[i] = TmapBridge.field(list.get(i), "renderData");
        setRouteRenderData(routeIndex, reroute, data, hasAlternative);
    }

    // ---- 카메라 ----

    private void syncLoop(int gen) {
        if (!running || gen != loopGeneration) return;
        try {
            syncSize();
            syncRouteTraffic(mainEngine());
            syncNightMode();
            syncCamera();
        } catch (Throwable error) {
            TmapHudLog.status("map render sync: " + error);
        }
        if (gen == loopGeneration) main.postDelayed(() -> syncLoop(gen), 1000 / FPS);
    }

    /**
     * 메인 스레드. HUD 주행/네비모드나 지도 크기 설정(밀도)이 바뀌면 엔진을 새로 만든다.
     * VSM 엔진은 만들어진 뒤에는 밀도 변경을 무시해서(2026-10-14 실차: 크기 설정을 키워도 그대로,
     * 예전에는 "시작할 때만 작게 나왔다"), 화면(ImageReader)만 바꿔서는 크기가 바뀌지 않았다.
     */
    private void syncSize() {
        int w = targetWidth(), h = targetHeight();
        int dpi = mapDpi();
        if (engine == null || (w == renderW && h == renderH && dpi == renderDpi)) return;
        TmapHudLog.line("map render rebuild " + renderW + "x" + renderH + "@" + renderDpi
                + " -> " + w + "x" + h + "@" + dpi);
        teardown();
        try {
            initialize(context, cl);
            startedAt = SystemClock.elapsedRealtime();
        } catch (Throwable error) {
            running = false;
            failures++;
            nextRetryAt = SystemClock.elapsedRealtime() + 30000;
            screen.setSuspended(false);
            TmapHudLog.xposed("map render rebuild failed (falling back to screen capture): " + error);
            release();
        }
    }

    /** 메인 스레드. 지금 엔진을 안내에서 떼고 정리한다(이 엔진 하나만, 티맵 화면 지도는 그대로). */
    private void teardown() {
        try {
            if (mapProxy != null) {
                invTyped(navigationManager(), "detachMapView", new Class<?>[]{mapViewInterfaceClass}, mapProxy);
            }
        } catch (Throwable error) {
            TmapHudLog.status("map render detach: " + error);
        }
        mapProxy = null;
        Object e = engine;
        if (e != null) {
            inv(inv(e, "getSurface"), "surfaceDestroyed");
            invTyped(e, "onPause", new Class<?>[0]);
            invTyped(e, "onDestroy", new Class<?>[0]);
        }
        release();
    }

    /** 메인 스레드. 화면 지도가 보이면 그 카메라를, 아니면 엔진 주행 모드를 쓴다. */
    private void syncCamera() {
        if (engine == null) return;
        View sourceView = screen.largestMapView(false);
        Object source = sourceView == null ? null : inv(sourceView, "mapEngine");
        boolean visible = sourceView != null && source != null && sourceView.isShown()
                && sourceView.getWindowVisibility() == View.VISIBLE;
        if (visible) {
            if (background) {
                background = false;
                inv(engine, "setNaviMoveMode", 0);
                inv(engine, "setNaviViewMode", 3);
                TmapHudLog.line("map render: app camera sync");
            }
            copyCamera(source, sourceView, true);
        } else if (!background) {
            background = true;
            if (source != null) copyCamera(source, sourceView, false);
            int move = source == null ? 1 : TmapBridge.number(inv(source, "getNaviMoveMode"));
            int view = source == null ? 3 : TmapBridge.number(inv(source, "getNaviViewMode"));
            inv(engine, "setNaviMoveMode", source == null ? 1 : move);
            inv(engine, "setNaviViewMode", source == null ? 3 : view);
            TmapHudLog.line("map render: background vehicle camera");
        }
        inv(inv(engine, "getSurface"), "requestRender");
    }

    private void copyCamera(Object source, View sourceView, boolean full) {
        inv(engine, "setViewLevel", TmapBridge.number(inv(source, "getViewLevel")),
                TmapBridge.number(inv(source, "getViewSubLevel")), false);
        inv(engine, "setTiltAngle", floatValue(inv(source, "getTiltAngle")), false);
        if (full) {
            inv(engine, "setRotationAngle", floatValue(inv(source, "getRotationAngle")), false);
            Object center = inv(source, "getMapCenterGEO");
            if (center != null) {
                inv(engine, "setMapCenter", TmapBridge.doubleValue(inv(center, "getLongitude")),
                        TmapBridge.doubleValue(inv(center, "getLatitude")), false);
            }
        }
        Object pt = inv(source, "getScreenCenter");
        if (pt instanceof Point && sourceView.getWidth() > 0 && sourceView.getHeight() > 0) {
            Point p = (Point) pt;
            inv(engine, "setScreenCenter", new Point(Math.round(renderW * p.x / (float) sourceView.getWidth()),
                    Math.round(renderH * p.y / (float) sourceView.getHeight())));
        }
        Object from = inv(source, "getViewSetting"), to = inv(engine, "getViewSetting");
        if (from != null && to != null) {
            inv(to, "setFov", floatValue(inv(from, "getFov")));
            inv(to, "setDensityDpi", mapDpi());
        }
    }

    private void syncRouteTraffic(Object source) {
        if (engine == null) return;
        boolean visible = source != null && Boolean.TRUE.equals(inv(source, "getShowTrafficInfoOnRouteLine"));
        if (routeTrafficVisible == null || routeTrafficVisible != visible) {
            inv(engine, "setShowTrafficInfoOnRouteLine", visible);
            routeTrafficVisible = visible;
        }
    }

    /**
     * 메인 스레드. NavigationManager 는 attachMapView 로 붙은 지도에 setNightMode 를 보내지
     * 않는다(11.8.3: 화면 지도는 MapViewStreaming.setMapStyle 이 따로 바꾼다). 안내 엔진이
     * 위치마다 갱신하는 naviConfigData.nightMode(자동 주야 포함)를 따라 스타일을 바꾼다.
     */
    private void syncNightMode() throws Exception {
        Object config = inv(navigationManager(), "getNaviConfigData");
        Object value = inv(config, "getNightMode");
        if (!(value instanceof Boolean) || (Boolean) value == night) return;
        night = (Boolean) value;
        applyMapStyle();
        TmapHudLog.line("map render: " + (night ? "night" : "day") + " style (TMAP guidance)");
    }

    private void applyMapStyle() {
        if (engine == null) return;
        invTyped(engine, "setMapStyle", new Class<?>[]{String.class}, night ? "TMAP_DRIVE:NIGHT" : "TMAP_DRIVE:DEFAULT");
        if (themesReady) inv(engine, "setObjectTheme", night ? OBJECT_THEME_NIGHT : OBJECT_THEME_DAY);
    }

    private void initializeObjectThemes(Context ctx) {
        try {
            inv(engine, "createObjectTheme", OBJECT_THEME_DAY, 1);
            inv(engine, "updateObjectTheme", OBJECT_THEME_DAY, readAsset(ctx, "theme_navi_day.json"));
            inv(engine, "createObjectTheme", OBJECT_THEME_NIGHT, 2);
            inv(engine, "updateObjectTheme", OBJECT_THEME_NIGHT, readAsset(ctx, "theme_navi_night.json"));
            themesReady = true;
        } catch (Throwable error) {
            themesReady = false;
            TmapHudLog.ex("navigation object themes", error);
        }
    }

    private Object mainEngine() {
        View view = screen.largestMapView(false);
        return view == null ? null : inv(view, "mapEngine");
    }

    private Object navigationManager() throws Exception {
        Class<?> type = cl.loadClass("com.skt.tmap.engine.navigation.NavigationManager");
        return type.getMethod("getInstance").invoke(null);
    }

    /** CarrotMapRenderStream.calculateVehicleIconSize(640, 384, 160) = 54 (지도 크기를 키워도 아이콘은 그대로). */
    private static float vehicleIconSize() {
        int maxPixels = Math.max(1, Math.min(Math.round(384 * 0.14f), Math.round(640 * 0.12f)));
        return maxPixels * 160f / DPI;
    }

    // ---- 프레임 ----

    private void onImageAvailable(ImageReader r) {
        Image image = null;
        try {
            image = r.acquireLatestImage();
            if (image == null || !running) return;
            long now = SystemClock.elapsedRealtime();
            if (now - lastEncodeAt < 900L / FPS || !client.ready()) return;
            if (startedAt == 0L || now - startedAt < WARMUP_MS) return;   // 캡처가 아직 담당
            lastEncodeAt = now;
            Image.Plane plane = image.getPlanes()[0];
            int pixelStride = plane.getPixelStride(), rowStride = plane.getRowStride();
            int w = image.getWidth(), h = image.getHeight();
            Bitmap padded = Bitmap.createBitmap(rowStride / pixelStride, h, Bitmap.Config.ARGB_8888);
            padded.copyPixelsFromBuffer(plane.getBuffer());
            Bitmap frame = padded.getWidth() == w ? padded : Bitmap.createBitmap(padded, 0, 0, w, h);
            ByteArrayOutputStream jpeg = new ByteArrayOutputStream(80000);
            frame.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, jpeg);
            if (frame != padded) frame.recycle();
            padded.recycle();
            byte[] bytes = jpeg.toByteArray();
            frameW = w;
            frameH = h;
            lastFrame = bytes;
            lastFrameAt = now;
            client.sendMap(Cnv2.frame(Cnv2.TYPE_IMAGE, Cnv2.FORMAT_JPEG, sequence++, bytes, w, h));
            if (++frames == 1) {
                screen.setSuspended(true);   // 이제부터 엔진 프레임만
                TmapHudLog.line("first rendered map frame sent; screen capture stopped");
            }
        } catch (Throwable error) {
            TmapHudLog.status("map render frame: " + error);
        } finally {
            if (image != null) image.close();
        }
    }

    private void release() {
        startedAt = 0L;
        frames = 0;
        try { if (reader != null) reader.close(); } catch (Throwable ignored) { }
        try { if (imageThread != null) imageThread.quitSafely(); } catch (Throwable ignored) { }
        reader = null;
        imageThread = null;
        imageHandler = null;
        engine = null;
        engineContext = null;
        lastFrame = null;
    }

    // ---- 리플렉션 ----

    private static final Map<String, Method> METHODS = new ConcurrentHashMap<>();

    private static String readAsset(Context ctx, String name) throws Exception {
        try (InputStream in = ctx.getAssets().open(name)) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            return out.toString("UTF-8");
        }
    }

    private static float floatValue(Object v) {
        return v instanceof Number ? ((Number) v).floatValue() : 0f;
    }

    /** 이름과 인자 수·타입이 맞는 공개 메서드를 찾아 부른다. 실패하면 null(로그 1회). */
    static Object inv(Object target, String name, Object... args) {
        if (target == null) return null;
        Method m = resolve(target.getClass(), name, args);
        if (m == null) return null;
        try {
            return m.invoke(target, args);
        } catch (Throwable error) {
            TmapHudLog.status("invoke " + name + ": " + error);
            return null;
        }
    }

    /** 오버로드가 있는 메서드는 매개변수 타입을 정해서 부른다. */
    static Object invTyped(Object target, String name, Class<?>[] types, Object... args) {
        if (target == null) return null;
        try {
            Method m = target.getClass().getMethod(name, types);
            return m.invoke(target, args);
        } catch (NoSuchMethodException error) {
            // 배열 인자(Object[] 로 넘긴 경우)는 실제 타입으로 다시 찾는다.
            for (Method m : target.getClass().getMethods()) {
                if (m.getName().equals(name) && m.getParameterTypes().length == types.length) {
                    try {
                        return m.invoke(target, args);
                    } catch (IllegalArgumentException ignored) {
                        // 다음 오버로드
                    } catch (Throwable other) {
                        TmapHudLog.status("invoke " + name + ": " + other);
                        return null;
                    }
                }
            }
            TmapHudLog.line("method missing: " + target.getClass().getName() + "#" + name);
            return null;
        } catch (Throwable error) {
            TmapHudLog.status("invoke " + name + ": " + error);
            return null;
        }
    }

    private static Method resolve(Class<?> type, String name, Object[] args) {
        int n = args == null ? 0 : args.length;
        String key = type.getName() + '#' + name + '/' + n;
        Method cached = METHODS.get(key);
        if (cached != null) return cached;
        for (Method m : type.getMethods()) {
            if (!m.getName().equals(name) || m.getParameterTypes().length != n) continue;
            if (!compatible(m.getParameterTypes(), args)) continue;
            METHODS.put(key, m);
            return m;
        }
        TmapHudLog.line("method missing: " + type.getName() + "#" + name + "/" + n);
        return null;
    }

    private static boolean compatible(Class<?>[] params, Object[] args) {
        for (int i = 0; i < params.length; i++) {
            Object a = args[i];
            Class<?> p = params[i];
            if (a == null) {
                if (p.isPrimitive()) return false;
                continue;
            }
            if (p.isPrimitive()) {
                if (p == int.class && !(a instanceof Integer)) return false;
                if (p == float.class && !(a instanceof Float)) return false;
                if (p == double.class && !(a instanceof Double)) return false;
                if (p == boolean.class && !(a instanceof Boolean)) return false;
                if (p == long.class && !(a instanceof Long)) return false;
            } else if (!p.isInstance(a)) {
                return false;
            }
        }
        return true;
    }
}
