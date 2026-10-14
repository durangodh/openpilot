package ai.comma.naverhud;

import android.content.Context;
import android.content.res.Configuration;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.media.Image;
import android.media.ImageReader;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.SystemClock;
import android.view.Surface;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.lang.reflect.Proxy;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.WeakHashMap;
import java.util.concurrent.atomic.AtomicReference;

import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.XposedHelpers;

/**
 * S9 HUD 네비모드 전용 네이버 지도(티맵 TmapMapRender 와 같은 방식).
 *
 * 폰 화면 지도를 찍어 쓰면(세로 화면의 가로 띠 하나) HUD 를 줄이려고 폰 지도까지 크게 줄여야
 * 했다. 대신 네이버 SDK 의 MapSurface(안드로이드 오토가 쓰는 화면 밖 지도)를 하나 더 만들어
 * 1080x506 ImageReader 에 그린다. 밀도를 낮춘 Context 로 만들어 글자·도로·아이콘이 모두
 * 함께 작아지고, 폰 화면 지도는 건드리지 않는다.
 *
 * 매 캡처 틱(메인 스레드)마다 안내 지도에서 복사한다:
 *  - 카메라(위치·줌·기울기·방향): NaverMap.M() → C1()
 *  - 경로선(Path/MultipartPath/ArrowheadPathOverlay): Overlay.o(setMap) 훅으로 안내 지도에 붙은
 *    것을 기억해 같은 종류를 HUD 지도에 하나씩 만들어 속성을 복사(굵기는 밀도 비율만큼)
 *  - 내 차 아이콘(LocationOverlay): 위치·방향·아이콘·크기
 *  - 지도 종류·야간·밝기·건물 높이·레이어(JNI 메서드, 이름이 난독화되지 않음)
 * SDK 클래스·메서드 이름은 6.10.0.16 과 6.11.1.2 에서 같았다(MapSurface h/f/n/l/k/o/i/r/q/s,
 * Overlay.o, NaverMap.M/C1/m0). 하나라도 없으면 쓰지 않고 예전 스냅샷 방식으로 돌아간다.
 */
final class NaverHudMap {
    static final int WIDTH = 1080, HEIGHT = 506;
    /** 지금 HUD 지도가 프레임을 내고 있으면 true(NaverZoomOffset 은 폰 지도를 줄이지 않는다). */
    static volatile boolean active;

    private static final String[] PATH_CLASSES = {
            "com.naver.maps.map.overlay.PathOverlay",
            "com.naver.maps.map.overlay.MultipartPathOverlay",
            "com.naver.maps.map.overlay.ArrowheadPathOverlay"};
    /** 그대로 복사하는 경로선 속성(게터·세터가 있는 것만). */
    private static final String[] COPY_PROPS = {
            "CoordParts", "ColorParts", "Coords", "Color", "OutlineColor", "PassedColor",
            "PassedOutlineColor", "Progress", "PatternImage", "PatternInterval", "ZIndex",
            "GlobalZIndex", "HideCollidedSymbols", "HideCollidedMarkers", "HideCollidedCaptions",
            "Visible"};
    /** 밀도 비율을 곱해 복사하는 픽셀 속성. */
    private static final String[] SCALED_PROPS = {"Width", "OutlineWidth"};

    /** 안내 지도에 붙은 경로선(Overlay.o 훅이 채운다). */
    private static final Map<Object, Object> attached = Collections.synchronizedMap(new WeakHashMap<>());
    private static volatile Field overlayMapField;
    private static volatile boolean overlayHooked;

    private final Handler imageHandler;
    private final AtomicReference<Bitmap> latest = new AtomicReference<>();
    private Object surface;          // MapSurface
    private Object hudMap;           // NaverMap
    private Object hudNative;        // NativeMapView
    private ImageReader reader;
    private float ratio = 1f;        // HUD 밀도 / 폰 밀도
    private boolean failed;
    private long lastStyleAt;
    private long createdAt;
    private final Map<Object, Object> mirrors = new WeakHashMap<>();   // 안내 경로선 → HUD 경로선
    private final Map<Object, Map<String, Object>> copied = new WeakHashMap<>();
    private final Set<String> layersOn = new HashSet<>();

    NaverHudMap() {
        HandlerThread thread = new HandlerThread("naver-hud-map-image");
        thread.start();
        imageHandler = new Handler(thread.getLooper());
    }

    /** NaverHudModule 에서 앱 시작 때 한 번: 안내 지도에 붙는 경로선을 기억한다. */
    static void installOverlayTracking(ClassLoader cl) {
        try {
            Class<?> overlay = cl.loadClass("com.naver.maps.map.overlay.Overlay");
            Class<?> naverMap = cl.loadClass("com.naver.maps.map.NaverMap");
            for (Field f : overlay.getDeclaredFields()) {
                if (f.getType() == naverMap && !Modifier.isStatic(f.getModifiers())) {
                    f.setAccessible(true);
                    overlayMapField = f;
                }
            }
            XposedHelpers.findAndHookMethod(overlay, "o", naverMap, new XC_MethodHook() {
                @Override protected void afterHookedMethod(MethodHookParam param) {
                    Object o = param.thisObject;
                    if (isPath(o)) attached.put(o, Boolean.TRUE);
                }
            });
            overlayHooked = overlayMapField != null;
            NaverHudLog.line("HUD map overlay tracking " + (overlayHooked ? "ready" : "without map field"));
        } catch (Throwable error) {
            NaverHudLog.ex("HUD map overlay tracking unavailable", error);
        }
    }

    private static boolean isPath(Object o) {
        if (o == null) return false;
        String name = o.getClass().getName();
        for (String c : PATH_CLASSES) if (c.equals(name)) return true;
        return false;
    }

    boolean failed() {
        return failed;
    }

    boolean started() {
        return surface != null;
    }

    /** 메인 스레드. 필요하면 만들고, 안내 지도(guided)를 따라 그린다. 새 프레임이 있으면 돌려준다. */
    Bitmap frame(Context app, Object guided) {
        if (failed || guided == null || app == null) return null;
        if (surface == null) {
            try {
                create(app, guided.getClass().getClassLoader());
            } catch (Throwable error) {
                fail("create", error);
                return null;
            }
        }
        if (!active && SystemClock.elapsedRealtime() - createdAt > 15000) {
            fail("start", new IllegalStateException("no HUD map frame within 15 s"));
            return null;
        }
        if (hudMap == null) return null;   // 지도 준비 중
        try {
            sync(guided);
        } catch (Throwable error) {
            fail("sync", error);
            return null;
        }
        Bitmap b = latest.getAndSet(null);
        if (b != null && !active) {
            active = true;
            NaverHudLog.line("HUD map first frame " + b.getWidth() + "x" + b.getHeight());
        }
        return b;
    }

    private void create(Context app, ClassLoader cl) throws Exception {
        createdAt = SystemClock.elapsedRealtime();
        Context base = app.getApplicationContext();
        float phoneDensity = base.getResources().getDisplayMetrics().density;
        int phoneWidth = Math.min(base.getResources().getDisplayMetrics().widthPixels,
                base.getResources().getDisplayMetrics().heightPixels);
        // 주행모드 HUD 는 폰 지도 폭(phoneWidth)을 760 으로 줄여 1:1 로 그린다. 네비모드에서는
        // 그보다 30%(네이버는 20% 더) 작게, HUD 가 1080 폭 프레임을 1536 으로 늘리는 것까지 뺀다.
        ratio = (760f / phoneWidth) * NaverZoomOffset.WIDE_SCALE * NaverZoomOffset.NAVER_EXTRA
                * (WIDTH / 1536f);
        Configuration config = new Configuration(base.getResources().getConfiguration());
        config.densityDpi = Math.max(60, Math.round(phoneDensity * ratio * 160f));
        ratio = config.densityDpi / 160f / phoneDensity;
        Context ctx = base.createConfigurationContext(config);

        Class<?> surfaceClass = cl.loadClass("com.naver.maps.map.MapSurface");
        Class<?> options = cl.loadClass("com.naver.maps.map.NaverMapOptions");
        Class<?> readyType = cl.loadClass("com.naver.maps.map.OnMapReadyCallback");
        Constructor<?> ctor = surfaceClass.getConstructor(Context.class, options);
        surface = ctor.newInstance(ctx, options.getConstructor().newInstance());
        call(surface, "h", new Class<?>[]{android.os.Bundle.class}, (Object) null);   // onCreate
        Object ready = Proxy.newProxyInstance(cl, new Class<?>[]{readyType}, (p, m, args) -> {
            if (args != null && args.length == 1 && args[0] != null
                    && "com.naver.maps.map.NaverMap".equals(args[0].getClass().getName())) {
                hudMap = args[0];
                hudNative = fieldOfClass(hudMap, "com.naver.maps.map.NativeMapView");
                NaverHudLog.line("HUD map ready at " + config.densityDpi + "dpi (x"
                        + String.format(java.util.Locale.US, "%.2f", ratio) + ")");
                return null;
            }
            if ("hashCode".equals(m.getName())) return System.identityHashCode(p);
            if ("equals".equals(m.getName())) return args != null && p == args[0];
            return null;
        });
        call(surface, "f", new Class<?>[]{readyType}, ready);   // getMapAsync
        call(surface, "n", new Class<?>[0]);                    // onStart
        call(surface, "l", new Class<?>[0]);                    // onResume
        reader = ImageReader.newInstance(WIDTH, HEIGHT, PixelFormat.RGBA_8888, 3);
        reader.setOnImageAvailableListener(this::onImage, imageHandler);
        Surface s = reader.getSurface();
        call(surface, "r", new Class<?>[]{Surface.class}, s);                           // created
        call(surface, "q", new Class<?>[]{Surface.class, int.class, int.class}, s, WIDTH, HEIGHT);
        NaverHudLog.line("HUD map surface " + WIDTH + "x" + HEIGHT + " started");
    }

    /** ImageReader 스레드: 최신 프레임만 Bitmap 으로 남긴다. */
    private void onImage(ImageReader r) {
        Image image = null;
        try {
            image = r.acquireLatestImage();
            if (image == null) return;
            Image.Plane plane = image.getPlanes()[0];
            int w = image.getWidth(), h = image.getHeight();
            int stride = plane.getRowStride() / plane.getPixelStride();
            Bitmap padded = Bitmap.createBitmap(stride, h, Bitmap.Config.ARGB_8888);
            padded.copyPixelsFromBuffer(plane.getBuffer());
            Bitmap frame = stride == w ? padded : Bitmap.createBitmap(padded, 0, 0, w, h);
            if (frame != padded) padded.recycle();
            Bitmap old = latest.getAndSet(frame);
            if (old != null) old.recycle();
        } catch (Throwable error) {
            NaverHudLog.status("HUD map frame: " + error);
        } finally {
            if (image != null) image.close();
        }
    }

    private void sync(Object guided) throws Exception {
        Object camera = invoke(guided, "M");
        if (camera != null && !Double.isNaN(camera.getClass().getField("zoom").getDouble(camera))) {
            call(hudMap, "C1", new Class<?>[]{camera.getClass()}, camera);
        }
        copyLocation(guided);
        copyPaths(guided);
        long now = SystemClock.elapsedRealtime();
        if (now - lastStyleAt >= 1000) {
            lastStyleAt = now;
            copyStyle(guided);
        }
    }

    private void copyLocation(Object guided) {
        Object src = invoke(guided, "m0"), dst = invoke(hudMap, "m0");
        if (src == null || dst == null) return;
        copyProp(src, dst, "Visible", 1f, src);
        copyProp(src, dst, "Position", 1f, src);
        copyProp(src, dst, "Bearing", 1f, src);
        copyProp(src, dst, "Icon", 1f, src);
        copyProp(src, dst, "IconWidth", ratio, src);
        copyProp(src, dst, "IconHeight", ratio, src);
        copyProp(src, dst, "Anchor", 1f, src);
    }

    private void copyPaths(Object guided) throws Exception {
        if (!overlayHooked) return;
        List<Object> sources;
        synchronized (attached) {
            sources = new ArrayList<>(attached.keySet());
        }
        Set<Object> live = Collections.newSetFromMap(new java.util.IdentityHashMap<>());
        for (Object src : sources) {
            if (overlayMapField.get(src) != guided) continue;
            live.add(src);
            Object dst = mirrors.get(src);
            if (dst == null) {
                dst = src.getClass().getConstructor().newInstance();
                mirrors.put(src, dst);
            }
            for (String p : COPY_PROPS) copyProp(src, dst, p, 1f, src);
            for (String p : SCALED_PROPS) copyProp(src, dst, p, ratio, src);
            if (overlayMapField.get(dst) != hudMap && hasCoords(dst)) {
                call(dst, "o", new Class<?>[]{hudMap.getClass()}, hudMap);
            }
        }
        // 안내 지도에서 떨어진 경로선은 HUD 지도에서도 뗀다.
        for (Iterator<Map.Entry<Object, Object>> it = mirrors.entrySet().iterator(); it.hasNext(); ) {
            Map.Entry<Object, Object> e = it.next();
            if (live.contains(e.getKey())) continue;
            try {
                call(e.getValue(), "o", new Class<?>[]{hudMap.getClass()}, (Object) null);
            } catch (Throwable ignored) { }
            copied.remove(e.getKey());
            it.remove();
        }
    }

    private static boolean hasCoords(Object overlay) {
        Object coords = invoke(overlay, "getCoordParts");
        if (coords == null) coords = invoke(overlay, "getCoords");
        return coords instanceof List && !((List<?>) coords).isEmpty();
    }

    /** src.getX() 값이 지난번과 다를 때만 dst.setX() 로 넣는다(경로 좌표를 매번 다시 넣지 않게). */
    private void copyProp(Object src, Object dst, String prop, float scale, Object key) {
        Method getter = getter(src.getClass(), prop);
        if (getter == null) return;
        try {
            Object value = getter.invoke(src);
            if (value == null) return;
            Map<String, Object> last = copied.get(key);
            if (last == null) copied.put(key, last = new java.util.HashMap<>());
            // 목록(경로 좌표·색 구간)은 앱이 같은 객체를 고쳐 쓸 수 있어 내용으로 비교한다.
            Object prev = last.get(prop);
            if (Objects.equals(prev, value)) return;
            Object put = value;
            if (scale != 1f && value instanceof Integer) put = Math.max(1, Math.round((Integer) value * scale));
            Method setter = setter(dst.getClass(), prop, getter.getReturnType());
            if (setter == null) return;
            if (value instanceof List && ((List<?>) value).isEmpty()) return;   // SDK 는 빈 좌표를 거부
            setter.invoke(dst, put);
            last.put(prop, value instanceof List ? new ArrayList<>((List<?>) value) : value);
        } catch (Throwable ignored) {
            // 좌표가 2개 미만인 순간 등은 다음 틱에 다시 넣는다.
        }
    }

    private void copyStyle(Object guided) {
        Object src = fieldOfClass(guided, "com.naver.maps.map.NativeMapView");
        if (src == null || hudNative == null) return;
        copyNative(src, "nativeGetMapType", "nativeSetMapType", String.class);
        copyNative(src, "nativeIsNightModeEnabled", "nativeSetNightModeEnabled", boolean.class);
        copyNative(src, "nativeGetLightness", "nativeSetLightness", float.class);
        copyNative(src, "nativeGetBuildingHeight", "nativeSetBuildingHeight", float.class);
        copyNative(src, "nativeGetSymbolPerspectiveRatio", "nativeSetSymbolPerspectiveRatio", float.class);
        copyNative(src, "nativeGetSymbolScale", "nativeSetSymbolScale", float.class);
        try {
            Object groups = nativeCall(src, "nativeGetEnabledLayerGroups", new Class<?>[0]);
            if (!(groups instanceof String[])) return;
            Set<String> want = new HashSet<>(Arrays.asList((String[]) groups));
            for (String g : want) {
                if (layersOn.add(g)) nativeCall(hudNative, "nativeSetLayerGroupEnabled",
                        new Class<?>[]{String.class, boolean.class}, g, true);
            }
            for (Iterator<String> it = layersOn.iterator(); it.hasNext(); ) {
                String g = it.next();
                if (want.contains(g)) continue;
                nativeCall(hudNative, "nativeSetLayerGroupEnabled",
                        new Class<?>[]{String.class, boolean.class}, g, false);
                it.remove();
            }
        } catch (Throwable error) {
            NaverHudLog.status("HUD map layers: " + error);
        }
    }

    private void copyNative(Object src, String get, String set, Class<?> type) {
        try {
            Object v = nativeCall(src, get, new Class<?>[0]);
            Object mine = nativeCall(hudNative, get, new Class<?>[0]);
            if (v != null && !v.equals(mine)) nativeCall(hudNative, set, new Class<?>[]{type}, v);
        } catch (Throwable error) {
            NaverHudLog.status("HUD map style " + set + ": " + error);
        }
    }

    /** 메인 스레드. 네비모드가 끝나면 화면 밖 지도를 내린다. */
    void release() {
        active = false;
        try {
            if (surface != null) {
                call(surface, "s", new Class<?>[0]);   // onSurfaceDestroyed
                call(surface, "k", new Class<?>[0]);   // onPause
                call(surface, "o", new Class<?>[0]);   // onStop
                call(surface, "i", new Class<?>[0]);   // onDestroy
                NaverHudLog.line("HUD map surface released");
            }
        } catch (Throwable error) {
            NaverHudLog.ex("HUD map release", error);
        }
        try { if (reader != null) reader.close(); } catch (Throwable ignored) { }
        surface = hudMap = hudNative = null;
        reader = null;
        mirrors.clear();
        copied.clear();
        layersOn.clear();
        Bitmap b = latest.getAndSet(null);
        if (b != null) b.recycle();
    }

    private void fail(String where, Throwable error) {
        NaverHudLog.ex("HUD map " + where + " failed (back to snapshot)", error);
        failed = true;
        release();
    }

    // ---- 리플렉션 ----

    private static final Map<String, Method> methods = Collections.synchronizedMap(new java.util.HashMap<>());

    private static Method getter(Class<?> c, String prop) {
        Method m = cached(c, "get" + prop);
        if (m == null) m = cached(c, "is" + prop);
        return m;
    }

    private static Method cached(Class<?> c, String name) {
        String key = c.getName() + "#" + name;
        if (methods.containsKey(key)) return methods.get(key);
        Method m = null;
        try {
            m = c.getMethod(name);
        } catch (Throwable ignored) { }
        methods.put(key, m);
        return m;
    }

    private static Method setter(Class<?> c, String prop, Class<?> type) {
        String key = c.getName() + "#set" + prop + "/" + type.getName();
        if (methods.containsKey(key)) return methods.get(key);
        Method m = null;
        try {
            m = c.getMethod("set" + prop, type);
        } catch (Throwable ignored) { }
        methods.put(key, m);
        return m;
    }

    private static Object invoke(Object target, String name) {
        if (target == null) return null;
        Method m = cached(target.getClass(), name);
        if (m == null) return null;
        try {
            return m.invoke(target);
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static Object call(Object target, String name, Class<?>[] types, Object... args) throws Exception {
        Method m = target.getClass().getMethod(name, types);
        return m.invoke(target, args);
    }

    private static Object nativeCall(Object target, String name, Class<?>[] types, Object... args) throws Exception {
        Method m = target.getClass().getDeclaredMethod(name, types);
        m.setAccessible(true);
        return m.invoke(target, args);
    }

    private static Object fieldOfClass(Object target, String className) {
        if (target == null) return null;
        for (Field f : target.getClass().getDeclaredFields()) {
            if (!f.getType().getName().equals(className) || Modifier.isStatic(f.getModifiers())) continue;
            try {
                f.setAccessible(true);
                return f.get(target);
            } catch (Throwable ignored) { }
        }
        return null;
    }
}
