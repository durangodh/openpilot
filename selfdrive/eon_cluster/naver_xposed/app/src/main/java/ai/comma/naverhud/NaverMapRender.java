package ai.comma.naverhud;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.media.Image;
import android.media.ImageReader;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.os.SystemClock;
import android.view.Surface;

import java.io.ByteArrayOutputStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.lang.reflect.Proxy;

/**
 * map_main 을 화면 스냅샷 대신 네이버 지도 엔진으로 직접 그린다(티맵 모듈의
 * TmapMapRender 와 같은 원리).
 *
 * 네이버 지도 SDK 의 MapSurface(Android Auto 차량 화면용, View 없이 임의 Surface 에
 * 그림)를 앱과 같은 옵션(NaverMapOptionsUtilsKt + AppInfo)으로 만들어 640×384
 * ImageReader 에 그린다. 지도가 준비되면 네이버 내비 SDK 의 NaverNaviUI 를 우리 지도용
 * 으로 하나 더 만든다. NaverNaviUI 는 GuidanceControl 의 이벤트 흐름을 구독해 경로선·
 * 차량·카메라 추적을 그리므로, 폰 화면의 안내 UI 와 별개로 같은 안내를 받는다.
 *
 * NaviStore.w1/NaviEngine.q 로 지도를 붙이면 안 된다: NaviStore 는 안내 UI 를 하나만
 * 들고 있어 폰 화면 지도에서 경로·차량이 떨어진다. 그래서 GuidanceControl 만 빌린다.
 *
 * 앱 내부에 안내 렌더러를 하나 더 만들고 난독화 이름(6.10.0.16 기준)을 쓰므로 확인한
 * 버전에서만 켠다. 실패하면 NaverMapCapture(스냅샷)로 돌아간다.
 */
final class NaverMapRender {
    private static final int WIDTH = 640, HEIGHT = 384, FPS = 5, JPEG_QUALITY = 65;
    private static final long FRAME_MS = 1000 / FPS;
    static final String NAVI_UI = "com.naver.maps.navi.ui.map.NaverNaviUI";
    private static final String GUIDANCE_CONTROL = "com.naver.maps.navi.v2.api.guidance.control.GuidanceControl";
    private static final String RENDER_CONFIG = "com.naver.maps.navi.ui.map.config.GuidanceRenderingConfiguration";
    static final String NAVER_MAP = "com.naver.maps.map.NaverMap";
    private static final String RENDERER = "com.naver.maps.navi.ui.map.renderer.Renderer";
    private static final String GUIDANCE_SESSION = "com.naver.maps.navi.v2.api.GuidanceSession";
    private static final long SYNC_MS = 250;
    // 안내 렌더러가 이 시간 안에 안 생기면 스냅샷으로 돌아간다(경로·차량 없는 지도 방지).
    private static final long RENDERER_TIMEOUT_MS = 6000;
    // 우리 지도 카메라가 폰 지도와 이만큼 떨어진 채 이 시간 이상 지나면 폰 카메라를 따라간다.
    private static final double FAR_METERS = 300;
    private static final long FAR_MS = 2000;

    private final NaverNaviClient client;
    private final NaverMapCapture snapshot;
    private final Handler main = new Handler(Looper.getMainLooper());
    private volatile boolean running;
    private int failures;
    private long nextRetryAt;

    private ClassLoader cl;
    private Object store;
    private Object surface;     // MapSurface
    private Object map;         // 우리 NaverMap
    private Object naviUi;      // 우리 NaverNaviUI
    private ImageReader reader;
    private HandlerThread imageThread;
    private Handler imageHandler;
    private volatile byte[] lastJpeg;
    private volatile long lastFrameAt;
    private long lastEncodeAt;
    private long frames;
    private Boolean night;
    private Object mapType;
    private volatile long readyAt;
    private long farSince;
    // 안내 렌더러가 붙고 카메라가 폰 지도에 맞춰질 때까지는 스냅샷을 계속 보낸다.
    // 그 뒤 첫 엔진 프레임을 보내는 순간 스냅샷을 멈춘다(반쯤 그려진 지도 방지).
    private static final long WARMUP_MS = 700L;
    private boolean rendererKicked, followPhone;
    private int syncTicks;

    NaverMapRender(NaverNaviClient client, NaverMapCapture snapshot) {
        this.client = client;
        this.snapshot = snapshot;
    }

    /** 상태 스레드에서 부른다. 폰 화면의 안내 UI 가 생긴 뒤(안내 중)에만 시작한다. */
    void maybeStart(final Context app, final Object naviStore) {
        if (running || app == null || naviStore == null || failures >= 3) return;
        if (SystemClock.elapsedRealtime() < nextRetryAt) return;
        if (fieldOfType(naviStore, NAVI_UI) == null) return;   // 아직 안내 UI 없음
        running = true;
        main.post(() -> {
            try {
                start(app, naviStore);
            } catch (Throwable error) {
                fail("start", error);
            }
        });
    }

    private void fail(String where, Throwable error) {
        running = false;
        failures++;
        nextRetryAt = SystemClock.elapsedRealtime() + 30000;
        snapshot.setSuspended(false);
        NaverHudLog.xposed("map render " + where + " failed (falling back to snapshots): " + error);
        release();
    }

    private void start(Context app, Object naviStore) throws Exception {
        store = naviStore;
        cl = naviStore.getClass().getClassLoader();
        Object options = appMapOptions(app);
        surface = cl.loadClass("com.naver.maps.map.MapSurface")
                .getConstructor(Context.class, cl.loadClass("com.naver.maps.map.NaverMapOptions"))
                .newInstance(app, options);
        Class<?> callbackType = cl.loadClass("com.naver.maps.map.OnMapReadyCallback");
        Object callback = Proxy.newProxyInstance(cl, new Class<?>[]{callbackType}, (proxy, method, args) -> {
            if (args != null && args.length == 1 && isType(args[0], NAVER_MAP)) {
                onMapReady(args[0]);
                return null;
            }
            switch (method.getName()) {
                case "hashCode": return System.identityHashCode(proxy);
                case "equals": return args != null && args.length == 1 && proxy == args[0];
                case "toString": return "NaverHudMapRender";
                default: return null;
            }
        });
        // MapProvider 의 생명주기 순서: onCreate(h) → getMapAsync(f) → onStart(n) → onResume(l)
        must(surface, "h", new Class<?>[]{Bundle.class}, (Object) null);
        must(surface, "f", new Class<?>[]{callbackType}, callback);
        must(surface, "n", new Class<?>[0]);
        must(surface, "l", new Class<?>[0]);

        imageThread = new HandlerThread("naver-hud-map-render");
        imageThread.start();
        imageHandler = new Handler(imageThread.getLooper());
        reader = ImageReader.newInstance(WIDTH, HEIGHT, PixelFormat.RGBA_8888, 3);
        reader.setOnImageAvailableListener(this::onImageAvailable, imageHandler);
        // SurfaceCallback.onSurfaceAvailable 과 같은 순서: surfaceCreated(r) → surfaceChanged(q)
        must(surface, "r", new Class<?>[]{Surface.class}, reader.getSurface());
        must(surface, "q", new Class<?>[]{Surface.class, int.class, int.class}, reader.getSurface(), WIDTH, HEIGHT);
        imageHandler.postDelayed(this::repeatLoop, FRAME_MS);
        // 지도 준비 신호가 오지 않으면 기본 카메라(서울) 지도만 나간다. 그때는 스냅샷으로.
        final Object startedSurface = surface;
        main.postDelayed(() -> {
            if (running && surface == startedSurface && naviUi == null) {
                fail("map ready timeout", new IllegalStateException("OnMapReadyCallback was not called"));
            }
        }, 8000);
        NaverHudLog.xposed("map render surface started " + WIDTH + "x" + HEIGHT);
    }

    /** NaverMapOptionsUtilsKt.a(Context, AppInfo): 폰·Android Auto 지도와 같은 스타일. */
    private Object appMapOptions(Context app) throws Exception {
        Class<?> optionsType = cl.loadClass("com.naver.maps.map.NaverMapOptions");
        try {
            Class<?> appInfoType = cl.loadClass("com.naver.map.core.common.api.AppInfo");
            Object appInfo = appInfoType.getMethod("getInstance").invoke(null);
            if (appInfo != null) {
                Class<?> util = cl.loadClass("com.naver.map.core.common.util.NaverMapOptionsUtilsKt");
                for (Method m : util.getDeclaredMethods()) {
                    Class<?>[] p = m.getParameterTypes();
                    if (Modifier.isStatic(m.getModifiers()) && m.getReturnType() == optionsType
                            && p.length == 2 && p[0] == Context.class && p[1] == appInfoType) {
                        m.setAccessible(true);
                        return m.invoke(null, app, appInfo);
                    }
                }
            }
            NaverHudLog.line("map render: app map options unavailable, using defaults");
        } catch (Throwable error) {
            NaverHudLog.ex("map render options", error);
        }
        return optionsType.getConstructor().newInstance();
    }

    /** 메인 스레드(OnMapReadyCallback). 우리 지도에 안내 렌더러를 붙인다. */
    private void onMapReady(Object naverMap) {
        try {
            map = naverMap;
            // MapSurface 는 표면이 생긴 뒤에야 NaverMap 을 만든다. 그 전에 부른 onStart(n)는
            // 지도에 닿지 않아 엔진이 정지 상태(nativeStart 안 됨)로 남고, 첫 프레임(SDK 기본
            // 카메라, 서울시청) 한 장만 그린다. 지도에 직접 onStart 를 건다.
            must(naverMap, "d1", new Class<?>[0]);   // NaverMap.onStart → NativeMapView.nativeStart
            Object phoneUi = fieldOfType(store, NAVI_UI);
            Object control = fieldOfType(phoneUi, GUIDANCE_CONTROL);
            if (control == null) throw new IllegalStateException("GuidanceControl not found");
            Object config = renderingConfiguration();
            Class<?> uiType = cl.loadClass(NAVI_UI);
            Constructor<?> ctor = uiType.getConstructor(cl.loadClass(NAVER_MAP),
                    cl.loadClass(GUIDANCE_CONTROL), cl.loadClass(RENDER_CONFIG));
            // 첫 프레임부터 폰과 같은 곳을 보여 준다(SDK 기본 카메라는 서울시청).
            mirrorPhoneCamera();
            naviUi = ctor.newInstance(naverMap, control, config);
            readyAt = SystemClock.elapsedRealtime();
            farSince = 0;
            rendererKicked = false;
            followPhone = false;
            syncFromPhone();
            main.postDelayed(this::syncLoop, SYNC_MS);
            NaverHudLog.xposed("map render: guidance UI attached");
        } catch (Throwable error) {
            fail("map ready", error);
        }
    }

    /** NaviSettingManagerKt.a(Context): NaviStore 가 NaverNaviUI 를 만들 때 쓰는 설정. */
    private Object renderingConfiguration() throws Exception {
        Class<?> configType = cl.loadClass(RENDER_CONFIG);
        Class<?> util = cl.loadClass("com.naver.map.core.navigation.setting.NaviSettingManagerKt");
        Object context = must(map, "T", new Class<?>[0]);   // NaverMap.getContext()
        for (Method m : util.getDeclaredMethods()) {
            Class<?>[] p = m.getParameterTypes();
            if (Modifier.isStatic(m.getModifiers()) && m.getReturnType() == configType
                    && p.length == 1 && p[0] == Context.class) {
                m.setAccessible(true);
                return m.invoke(null, context);
            }
        }
        throw new IllegalStateException("rendering configuration factory not found");
    }

    private void syncLoop() {
        if (!running || naviUi == null) return;
        try {
            if (!checkGuidanceRenderer()) return;   // 스냅샷으로 돌아갔다
            followCamera();
            if (++syncTicks % 2 == 0) syncFromPhone();
        } catch (Throwable error) {
            NaverHudLog.status("map render sync: " + error);
        }
        main.postDelayed(this::syncLoop, SYNC_MS);
    }

    /**
     * NaverNaviUI 는 생성될 때와 Started 이벤트 때 경로·차량 렌더러를 만든다. 안 생겼으면
     * 현재 안내 세션으로 한 번 직접 만들어 보고, 그래도 없으면 스냅샷으로 돌아간다.
     * false 면 멈췄다.
     */
    private boolean checkGuidanceRenderer() {
        if (fieldOfType(naviUi, RENDERER) != null) return true;
        long since = SystemClock.elapsedRealtime() - readyAt;
        if (!rendererKicked && since > 1500) {
            rendererKicked = true;
            try {
                Object control = fieldOfType(naviUi, GUIDANCE_CONTROL);
                Object session = control == null ? null
                        : must(control, "getCurrentSession", new Class<?>[0]);
                if (session != null) {
                    must(naviUi, "t", new Class<?>[]{cl.loadClass(GUIDANCE_SESSION)}, session);
                }
                NaverHudLog.xposed("map render: guidance renderer missing, started it from the current session"
                        + (fieldOfType(naviUi, RENDERER) != null ? " (ok)" : " (still missing)"));
            } catch (Throwable error) {
                NaverHudLog.xposed("map render: guidance renderer start failed: " + error);
            }
        }
        if (fieldOfType(naviUi, RENDERER) == null && since > RENDERER_TIMEOUT_MS) {
            fail("no guidance renderer", new IllegalStateException("route renderer was not created"));
            return false;
        }
        return true;
    }

    /**
     * 안내 렌더러가 우리 지도 카메라를 차량에 맞춰 움직여야 한다. 폰 지도와 멀리 떨어진
     * 채로 있으면(카메라 추적이 안 붙음) 그때부터는 폰 지도 카메라를 그대로 따라간다.
     */
    private void followCamera() {
        if (followPhone) {
            mirrorPhoneCamera();
            return;
        }
        double meters = distanceMeters(cameraTarget(phoneMap()), cameraTarget(map));
        long now = SystemClock.elapsedRealtime();
        if (Double.isNaN(meters) || meters < FAR_METERS) {
            farSince = 0;
            return;
        }
        if (farSince == 0) {
            farSince = now;
        } else if (now - farSince >= FAR_MS) {
            followPhone = true;
            mirrorPhoneCamera();
            NaverHudLog.xposed("map render: camera " + Math.round(meters)
                    + " m away from the phone map; following the phone camera");
        }
    }

    private Object phoneMap() {
        return fieldOfType(fieldOfType(store, NAVI_UI), NAVER_MAP);
    }

    /** 폰 지도의 카메라(위치·줌·기울기·방향)를 우리 지도에 그대로 옮긴다. 메인 스레드. */
    private void mirrorPhoneCamera() {
        Object phone = phoneMap();
        if (phone == null || map == null) return;
        Object position = call(phone, "M", new Class<?>[0]);                 // getCameraPosition
        if (position == null || position.equals(call(map, "M", new Class<?>[0]))) return;
        try {
            Class<?> updateType = cl.loadClass("com.naver.maps.map.CameraUpdate");
            Object update = updateType.getMethod("x", position.getClass()).invoke(null, position);  // toCameraPosition
            call(map, "Y0", new Class<?>[]{updateType}, update);            // moveCamera
        } catch (Throwable error) {
            NaverHudLog.status("map render camera: " + error);
        }
    }

    private static Object cameraTarget(Object naverMap) {
        Object position = call(naverMap, "M", new Class<?>[0]);
        if (position == null) return null;
        try {
            return position.getClass().getField("target").get(position);
        } catch (Throwable error) {
            return null;
        }
    }

    private static double distanceMeters(Object a, Object b) {
        if (a == null || b == null) return Double.NaN;
        try {
            double lat1 = a.getClass().getField("latitude").getDouble(a);
            double lon1 = a.getClass().getField("longitude").getDouble(a);
            double lat2 = b.getClass().getField("latitude").getDouble(b);
            double lon2 = b.getClass().getField("longitude").getDouble(b);
            double x = Math.toRadians(lon2 - lon1) * Math.cos(Math.toRadians((lat1 + lat2) / 2));
            double y = Math.toRadians(lat2 - lat1);
            return Math.sqrt(x * x + y * y) * 6371000.0;
        } catch (Throwable error) {
            return Double.NaN;
        }
    }

    /**
     * 폰 화면 안내 UI 의 보기 모드·차량 아이콘 설정, 폰 지도의 지도 종류·야간 모드를
     * 우리 지도에 맞춘다. 타입으로 getter/setter 를 찾는다.
     */
    private void syncFromPhone() {
        Object phoneUi = fieldOfType(store, NAVI_UI);
        if (phoneUi == null || naviUi == null) return;
        copyByType(phoneUi, naviUi, "com.naver.maps.navi.ui.map.model.RenderingMode");
        copyByType(phoneUi, naviUi, "com.naver.maps.navi.ui.map.model.ViewMode");
        copyByType(phoneUi, naviUi, "com.naver.maps.navi.ui.map.config.group.CarvatarConfiguration");
        Object phoneMap = fieldOfType(phoneUi, NAVER_MAP);
        if (phoneMap == null || map == null) return;
        Object type = getterOfType(phoneMap, NAVER_MAP + "$MapType");
        if (type != null && type != mapType) {
            if (setterOfType(map, NAVER_MAP + "$MapType", type)) mapType = type;
        }
        Object nightValue = call(phoneMap, "U0", new Class<?>[0]);      // isNightModeEnabled
        if (nightValue instanceof Boolean && !nightValue.equals(night)) {
            call(map, "b2", new Class<?>[]{boolean.class}, nightValue);  // setNightModeEnabled
            night = (Boolean) nightValue;
        }
    }

    private static void copyByType(Object from, Object to, String typeName) {
        Object value = getterOfType(from, typeName);
        if (value != null && !value.equals(getterOfType(to, typeName))) setterOfType(to, typeName, value);
    }

    // ---- 프레임 ----

    private void onImageAvailable(ImageReader r) {
        Image image = null;
        try {
            image = r.acquireLatestImage();
            if (image == null || !running) return;
            long now = SystemClock.elapsedRealtime();
            if (now - lastEncodeAt < (FRAME_MS * 9) / 10) return;
            long ready = readyAt;
            if (ready == 0L || naviUi == null || now - ready < WARMUP_MS) return;   // 스냅샷이 아직 담당
            lastEncodeAt = now;
            Image.Plane plane = image.getPlanes()[0];
            int w = image.getWidth(), h = image.getHeight();
            Bitmap padded = Bitmap.createBitmap(plane.getRowStride() / plane.getPixelStride(), h, Bitmap.Config.ARGB_8888);
            padded.copyPixelsFromBuffer(plane.getBuffer());
            Bitmap frame = padded.getWidth() == w ? padded : Bitmap.createBitmap(padded, 0, 0, w, h);
            ByteArrayOutputStream jpeg = new ByteArrayOutputStream(80000);
            frame.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, jpeg);
            if (frame != padded) frame.recycle();
            padded.recycle();
            byte[] bytes = jpeg.toByteArray();
            lastJpeg = bytes;
            lastFrameAt = now;
            if (client.ready()) client.sendMap(bytes);
            if (++frames == 1) {
                snapshot.setSuspended(true);   // 이제부터 엔진 프레임만
                NaverHudLog.xposed("first rendered map frame sent; snapshots stopped");
            }
            if (frames % 300 == 0) NaverHudLog.line("rendered map frames: " + frames);
        } catch (Throwable error) {
            NaverHudLog.status("map render frame: " + error);
        } finally {
            if (image != null) image.close();
        }
    }

    /**
     * 지도가 멈춰 있으면 엔진이 새 프레임을 그리지 않는다. 패치판 티맵처럼 마지막
     * 프레임을 같은 간격으로 다시 보내 EON 이 지도를 오래된 것으로 지우지 않게 한다.
     */
    private void repeatLoop() {
        if (!running) return;
        byte[] last = lastJpeg;
        if (last != null && client.ready() && SystemClock.elapsedRealtime() - lastFrameAt >= FRAME_MS * 2) {
            client.sendMap(last);
        }
        Handler h = imageHandler;
        if (h != null) h.postDelayed(this::repeatLoop, FRAME_MS);
    }

    private void release() {
        readyAt = 0L;
        frames = 0;
        try { if (naviUi != null) call(naviUi, "v", new Class<?>[0]); } catch (Throwable ignored) { }
        // s = surfaceDestroyed, o = onStop, i = onDestroy
        try { if (surface != null) call(surface, "s", new Class<?>[0]); } catch (Throwable ignored) { }
        try { if (surface != null) call(surface, "o", new Class<?>[0]); } catch (Throwable ignored) { }
        try { if (surface != null) call(surface, "i", new Class<?>[0]); } catch (Throwable ignored) { }
        try { if (reader != null) reader.close(); } catch (Throwable ignored) { }
        try { if (imageThread != null) imageThread.quitSafely(); } catch (Throwable ignored) { }
        naviUi = null;
        surface = null;
        map = null;
        reader = null;
        imageThread = null;
        imageHandler = null;
        lastJpeg = null;
    }

    // ---- 리플렉션 ----

    private static boolean isType(Object value, String typeName) {
        for (Class<?> t = value == null ? null : value.getClass(); t != null; t = t.getSuperclass()) {
            if (t.getName().equals(typeName)) return true;
        }
        return false;
    }

    private static boolean assignable(Class<?> type, String typeName) {
        for (Class<?> t = type; t != null; t = t.getSuperclass()) {
            if (t.getName().equals(typeName)) return true;
            for (Class<?> i : t.getInterfaces()) if (i.getName().equals(typeName)) return true;
        }
        return false;
    }

    /** 선언 타입이 typeName 인 첫 필드 값(상위 클래스 포함). */
    static Object fieldOfType(Object target, String typeName) {
        if (target == null) return null;
        for (Class<?> t = target.getClass(); t != null; t = t.getSuperclass()) {
            for (Field f : t.getDeclaredFields()) {
                if (Modifier.isStatic(f.getModifiers()) || !assignable(f.getType(), typeName)) continue;
                try {
                    f.setAccessible(true);
                    Object v = f.get(target);
                    if (v != null) return v;
                } catch (Throwable ignored) {
                    // 다음 필드
                }
            }
        }
        return null;
    }

    /** 인자 없는 공개 메서드 중 반환 타입이 typeName 인 것. */
    private static Object getterOfType(Object target, String typeName) {
        if (target == null) return null;
        for (Method m : target.getClass().getMethods()) {
            if (m.getParameterTypes().length != 0 || !m.getReturnType().getName().equals(typeName)) continue;
            try {
                return m.invoke(target);
            } catch (Throwable ignored) {
                return null;
            }
        }
        return null;
    }

    /** 인자 하나(typeName)를 받는 void 공개 메서드. */
    private static boolean setterOfType(Object target, String typeName, Object value) {
        if (target == null) return false;
        for (Method m : target.getClass().getMethods()) {
            Class<?>[] p = m.getParameterTypes();
            if (p.length != 1 || m.getReturnType() != void.class || !p[0].getName().equals(typeName)) continue;
            try {
                m.invoke(target, value);
                return true;
            } catch (Throwable error) {
                NaverHudLog.status("map render set " + typeName + ": " + error);
                return false;
            }
        }
        return false;
    }

    /** 실패하면 예외(시작 단계용). */
    private static Object must(Object target, String name, Class<?>[] types, Object... args) throws Exception {
        Method m = target.getClass().getMethod(name, types);
        m.setAccessible(true);
        return m.invoke(target, args);
    }

    private static Object call(Object target, String name, Class<?>[] types, Object... args) {
        if (target == null) return null;
        try {
            Method m = target.getClass().getMethod(name, types);
            m.setAccessible(true);
            return m.invoke(target, args);
        } catch (Throwable error) {
            NaverHudLog.status("map render call " + name + ": " + error);
            return null;
        }
    }
}
