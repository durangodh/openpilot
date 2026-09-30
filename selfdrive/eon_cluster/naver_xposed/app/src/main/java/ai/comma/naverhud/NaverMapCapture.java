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
    private long requestedAt, lastFrameAt, lastStatusAt, sequence;
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
            sequence++;
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
        final long ticket = ++sequence;
        try {
            ClassLoader loader = map.getClass().getClassLoader();
            Class<?> callbackType = loader.loadClass("com.naver.maps.map.NaverMap$SnapshotReadyCallback");
            Object callback = Proxy.newProxyInstance(loader, new Class<?>[]{callbackType}, (proxy, method, args) -> {
                if ("a".equals(method.getName()) && args != null && args.length == 1 && args[0] instanceof Bitmap) {
                    onSnapshot((Bitmap) args[0], ticket);
                } else if ("hashCode".equals(method.getName())) {
                    return System.identityHashCode(proxy);
                } else if ("equals".equals(method.getName())) {
                    return args != null && args.length == 1 && proxy == args[0];
                } else if ("toString".equals(method.getName())) {
                    return "NaverMapCapture.Callback";
                }
                return null;
            });
            Method take = map.getClass().getMethod("p2", boolean.class, callbackType);
            take.invoke(map, false, callback);
        } catch (Throwable error) {
            requestedAt = 0;
            NaverHudLog.ex("snapshot request", error);
        }
    }

    private void onSnapshot(Bitmap source, long ticket) {
        if (ticket != sequence || source == null || source.isRecycled()) return;
        requestedAt = 0;
        lastFrameAt = SystemClock.elapsedRealtime();
        try {
            Bitmap image = fitCenterCrop(source);
            encoder.execute(() -> {
                try {
                    // Reject a stale frame after a map switch or newer request.
                    if (ticket != sequence) return;
                    ByteArrayOutputStream bytes = new ByteArrayOutputStream(100000);
                    image.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, bytes);
                    if (ticket == sequence) {
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
                return call(field.get(mapView), "f");
            } catch (NoSuchFieldException ignored) { }
        }
        return null;
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
