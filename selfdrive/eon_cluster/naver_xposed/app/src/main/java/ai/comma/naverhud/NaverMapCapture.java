package ai.comma.naverhud;

import android.app.Activity;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Rect;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.view.View;
import android.view.ViewGroup;

import java.io.ByteArrayOutputStream;
import java.lang.ref.WeakReference;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Reads Naver's own rendered map via NaverMap.takeSnapshot; never captures a screen. */
final class NaverMapCapture {
    private static final int WIDTH = 640, HEIGHT = 384, JPEG_QUALITY = 65;
    private static final long REQUEST_TIMEOUT_MS = 1200;

    private final NaverNaviClient client;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService encoder = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "naver-hud-map-encode");
        t.setDaemon(true);
        return t;
    });
    private final List<WeakReference<Activity>> activities = new ArrayList<>();
    private volatile Object provider;
    private Object activeMap;
    // generation: 지도(NaverMap 객체)가 바뀔 때만 올린다. 이전 지도의 프레임만 버린다.
    // requestId  : 스냅샷 요청마다 올린다. 요청 중복 판단용이며 프레임 폐기 기준이 아니다.
    // 예전에는 한 번호로 둘 다 처리해서, 인코딩이 다음 요청(200ms)보다 늦으면
    // 멀쩡한 프레임까지 버려져 지도가 계속 안 나올 수 있었다.
    private volatile long generation;
    private long requestedAt, lastFrameAt, lastStatusAt, requestId;
    private long sent;

    NaverMapCapture(NaverNaviClient client) { this.client = client; }

    void setMapProvider(Object value) { provider = value; }

    void addActivity(Activity activity) {
        if (activity == null) return;
        synchronized (activities) {
            activities.removeIf(ref -> ref.get() == null || ref.get() == activity);
            activities.add(new WeakReference<>(activity));
        }
    }

    /** Called from the bridge's background poller. */
    void capture() {
        if (!client.ready()) return;
        main.post(this::captureOnMain);
    }

    private void captureOnMain() {
        Object map = chooseMap();
        long now = SystemClock.elapsedRealtime();
        if (map != activeMap) {
            activeMap = map;
            generation++;
            requestedAt = 0;
            lastFrameAt = 0;
            if (map != null) NaverHudLog.line("snapshot map selected: " + map.getClass().getName());
        }
        if (map == null) {
            if (now - lastStatusAt > 10000) {
                lastStatusAt = now;
                NaverHudLog.status("waiting for NaverMap");
            }
            return;
        }
        if (requestedAt != 0 && now - requestedAt < REQUEST_TIMEOUT_MS) return;
        requestedAt = now;
        final long gen = generation;
        final long req = ++requestId;
        try {
            ClassLoader loader = map.getClass().getClassLoader();
            Class<?> callbackType = loader.loadClass("com.naver.maps.map.NaverMap$SnapshotReadyCallback");
            Object callback = Proxy.newProxyInstance(loader, new Class<?>[]{callbackType}, (proxy, method, args) -> {
                // 콜백 메서드 이름은 난독화("a")라 버전마다 바뀔 수 있다. Bitmap 한 개를 받는 호출이면 스냅샷이다.
                if (args != null && args.length == 1 && args[0] instanceof Bitmap) {
                    onSnapshot((Bitmap) args[0], gen, req);
                } else if ("hashCode".equals(method.getName())) {
                    return System.identityHashCode(proxy);
                } else if ("equals".equals(method.getName())) {
                    return args != null && args.length == 1 && proxy == args[0];
                } else if ("toString".equals(method.getName())) {
                    return "NaverMapCapture.Callback";
                }
                return null;
            });
            Method take = snapshotMethod(map.getClass(), callbackType);
            if (take == null) throw new NoSuchMethodException("NaverMap snapshot method");
            if (take.getParameterTypes().length == 2) take.invoke(map, false, callback);
            else take.invoke(map, callback);
        } catch (Throwable error) {
            requestedAt = 0;
            NaverHudLog.ex("snapshot request", error);
        }
    }

    private void onSnapshot(Bitmap source, long gen, long req) {
        if (source == null || source.isRecycled()) return;
        // 최신 요청의 응답일 때만 다음 요청을 허용한다(늦게 온 옛 응답이 요청 흐름을 흔들지 않게).
        if (req == requestId) requestedAt = 0;
        if (gen != generation) return;
        lastFrameAt = SystemClock.elapsedRealtime();
        try {
            Bitmap image = fitCenterCrop(source);
            encoder.execute(() -> {
                try {
                    // 지도가 바뀐 경우만 버린다. 새 요청이 나갔다고 버리지 않는다.
                    if (gen != generation) return;
                    ByteArrayOutputStream bytes = new ByteArrayOutputStream(100000);
                    image.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, bytes);
                    if (gen == generation) {
                        client.sendMap(bytes.toByteArray());
                        if (++sent == 1) NaverHudLog.line("first direct map frame sent");
                    }
                } catch (Throwable error) {
                    NaverHudLog.ex("snapshot encode", error);
                } finally {
                    image.recycle();
                }
            });
        } catch (Throwable error) {
            NaverHudLog.ex("snapshot crop", error);
        }
    }

    private Object chooseMap() {
        Object car = call(provider, "i");
        if (!isNaverMap(car)) car = firstNaverMapGetter(provider);
        if (car != null) return car;
        synchronized (activities) {
            for (int i = activities.size() - 1; i >= 0; i--) {
                Activity activity = activities.get(i).get();
                if (activity == null || activity.isFinishing() || activity.isDestroyed()) {
                    activities.remove(i);
                    continue;
                }
                try {
                    View root = activity.getWindow().getDecorView();
                    View mapView = findMapView(root, 0);
                    if (mapView != null && mapView.isShown() && root.isAttachedToWindow()) {
                        Object map = mapOf(mapView);
                        if (map != null) return map;
                    }
                } catch (Throwable ignored) { }
            }
        }
        return null;
    }

    private static View findMapView(View view, int depth) {
        if (view == null || depth > 40) return null;
        for (Class<?> type = view.getClass(); type != null; type = type.getSuperclass()) {
            if ("com.naver.maps.map.MapView".equals(type.getName())) return view;
        }
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) {
                View found = findMapView(group.getChildAt(i), depth + 1);
                if (found != null) return found;
            }
        }
        return null;
    }

    private static Object mapOf(View mapView) throws Exception {
        for (Class<?> type = mapView.getClass(); type != null; type = type.getSuperclass()) {
            try {
                Field field = type.getDeclaredField("a0");
                field.setAccessible(true);
                Object map = call(field.get(mapView), "f");
                if (isNaverMap(map)) return map;
            } catch (NoSuchFieldException ignored) { }
        }
        // 6.10.0.16 이외 버전: 난독화 필드명이 바뀌어도 MapView 필드 중
        // NaverMap 을 돌려주는 인자 없는 메서드를 찾아 쓴다.
        for (Class<?> type = mapView.getClass(); type != null; type = type.getSuperclass()) {
            for (Field field : type.getDeclaredFields()) {
                if (field.getType().isPrimitive()) continue;
                try {
                    field.setAccessible(true);
                    Object holder = field.get(mapView);
                    if (isNaverMap(holder)) return holder;
                    Object map = firstNaverMapGetter(holder);
                    if (map != null) return map;
                } catch (Throwable ignored) { }
            }
        }
        return null;
    }

    private static final String NAVER_MAP_CLASS = "com.naver.maps.map.NaverMap";

    private static boolean isNaverMap(Object value) {
        if (value == null) return false;
        for (Class<?> t = value.getClass(); t != null; t = t.getSuperclass()) {
            if (NAVER_MAP_CLASS.equals(t.getName())) return true;
        }
        return false;
    }

    /** 인자 없는 public 메서드 중 반환형이 NaverMap 인 것을 호출한다. */
    private static Object firstNaverMapGetter(Object holder) {
        if (holder == null) return null;
        for (Method m : holder.getClass().getMethods()) {
            if (m.getParameterTypes().length != 0) continue;
            if (!NAVER_MAP_CLASS.equals(m.getReturnType().getName())) continue;
            try {
                Object map = m.invoke(holder);
                if (map != null) return map;
            } catch (Throwable ignored) { }
        }
        return null;
    }

    private static Method cachedSnapshot;

    /** 이름("p2")이 아니라 시그니처로 스냅샷 메서드를 찾는다. */
    private static Method snapshotMethod(Class<?> mapClass, Class<?> callbackType) {
        Method m = cachedSnapshot;
        if (m != null && m.getDeclaringClass().isAssignableFrom(mapClass)) return m;
        try {
            m = mapClass.getMethod("p2", boolean.class, callbackType);
        } catch (NoSuchMethodException ignored) {
            m = null;
            for (Method c : mapClass.getMethods()) {
                Class<?>[] p = c.getParameterTypes();
                if (p.length == 2 && p[0] == boolean.class && p[1] == callbackType) { m = c; break; }
            }
            if (m == null) {
                for (Method c : mapClass.getMethods()) {
                    Class<?>[] p = c.getParameterTypes();
                    if (p.length == 1 && p[0] == callbackType) { m = c; break; }
                }
            }
            if (m != null) NaverHudLog.line("snapshot method resolved by signature: " + m.getName());
        }
        cachedSnapshot = m;
        return m;
    }

    private static Object call(Object target, String name) {
        if (target == null) return null;
        try {
            Method method = target.getClass().getMethod(name);
            method.setAccessible(true);
            return method.invoke(target);
        } catch (Throwable ignored) { return null; }
    }

    private static Bitmap fitCenterCrop(Bitmap source) {
        int sw = source.getWidth(), sh = source.getHeight();
        float scale = Math.max(WIDTH / (float) sw, HEIGHT / (float) sh);
        int cropW = Math.min(sw, Math.round(WIDTH / scale));
        int cropH = Math.min(sh, Math.round(HEIGHT / scale));
        int left = (sw - cropW) / 2;
        int top = sh > sw ? Math.round(sh * 0.62f - cropH / 2f) : (sh - cropH) / 2;
        top = Math.max(0, Math.min(sh - cropH, top));
        Bitmap out = Bitmap.createBitmap(WIDTH, HEIGHT, Bitmap.Config.ARGB_8888);
        new Canvas(out).drawBitmap(source,
                new Rect(left, top, left + cropW, top + cropH),
                new Rect(0, 0, WIDTH, HEIGHT), new Paint(Paint.FILTER_BITMAP_FLAG));
        return out;
    }
}
