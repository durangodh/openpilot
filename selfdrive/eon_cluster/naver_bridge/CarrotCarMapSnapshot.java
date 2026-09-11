package com.naver.map.carrot;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Rect;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;

/**
 * HUD13: map_main via NaverMap.takeSnapshot from either the Android Auto MapProvider map
 * or the phone MapView (nMirror / virtual display), whichever exists.
 *
 * When Naver runs on Android Auto its navigation map (route, car marker, camera)
 * is rendered by {@code MapProvider}'s {@code MapSurface} into the vehicle
 * Surface. That Surface cannot be read with PixelCopy (HUD7 got black frames),
 * but the SDK can read its own GL framebuffer: {@code NaverMap.takeSnapshot()}
 * ({@code p2(boolean, SnapshotReadyCallback)} in 6.9.1.3) triggers a render
 * and returns a Bitmap through {@code SnapshotReadyCallback.a(Bitmap)}.
 *
 * {@code MapProvider.<init>} is patched to hand its instance to
 * {@link #provider(Object)}; {@code MapProvider.i()} returns the NaverMap once
 * it is ready. While that map exists this class owns map_main; otherwise the
 * bridge continues with the HUD6 phone capture.
 */
public final class CarrotCarMapSnapshot {
    private static final String TAG = "CarrotCarMapSnapshot";
    static final int WIDTH = 640;   // TMAP map_main size (was 960x576 in HUD13)
    static final int HEIGHT = 384;
    private static final long SNAPSHOT_TIMEOUT_MS = 3000;
    /** No callback for this long -> the AA renderer is gone; release map_main. */
    private static final long DEAD_AFTER_MS = 12000;
    private static final long STATUS_LOG_MS = 5000;
    /** TMAP's map_main JPEG quality. The EON NHUD1 quality relay was removed in g_hud 9475e5c. */
    private static final int JPEG_QUALITY = 65;

    private static volatile Object provider;
    private static volatile Object naverMap;
    private static volatile CarrotNaverBridge bridge;
    private static Handler main;
    private static Handler worker;
    private static volatile long requestedAt;
    private static volatile long lastBitmapAt;
    private static volatile long firstRequestAt;
    private static volatile long snapshots;
    private static volatile long sent;
    private static long lastStatusLogAt;
    private static long lastIdleLogAt;
    private static Object callbackProxy;

    private CarrotCarMapSnapshot() {
    }

    /** Called from the patched MapProvider constructor (classes5.dex). */
    public static void provider(Object mapProvider) {
        provider = mapProvider;
        naverMap = null;
        firstRequestAt = 0;
        lastBitmapAt = 0;
        CarrotHudLog.log(TAG, "MapProvider created " + (mapProvider == null ? "null" : mapProvider.getClass().getName()));
    }

    /** Bridge thread, every 500 ms. True while this path owns map_main. */
    public static boolean capture(CarrotNaverBridge b) {
        Object p = provider;
        if (b == null) {
            return false;
        }
        bridge = b;
        Object map = null;
        String source = null;
        if (p != null) {
            try {
                Method i = p.getClass().getMethod("i", new Class<?>[0]);
                map = i.invoke(p, new Object[0]);
                source = "AndroidAuto MapProvider";
            } catch (Throwable t) {
                CarrotHudLog.log(TAG, "MapProvider.i() failed: " + t);
            }
        }
        if (map == null) {
            // nMirror / virtual-display case: Naver runs as a normal Activity whose
            // com.naver.maps.map.MapView holds the NaverMap (MapView.a0 = delegate, delegate.f() = map).
            map = phoneMap();
            source = "phone MapView";
        }
        long now = SystemClock.elapsedRealtime();
        if (map == null) {
            if (lastIdleLogAt == 0 || now - lastIdleLogAt >= 60000) {
                lastIdleLogAt = now;
                CarrotHudLog.log(TAG, "HUD13.6 bridge polling, no NaverMap yet (provider=" + (p != null)
                        + ", activity=" + (activityObject() != null) + ") -> phone capture path");
            }
            return false;
        }
        if (map != naverMap) {
            naverMap = map;
            firstRequestAt = 0;
            lastBitmapAt = 0;
            requestedAt = 0;
            CarrotHudLog.log(TAG, "NaverMap available from " + source + " " + map.getClass().getName());
        }
        if (now - lastStatusLogAt >= STATUS_LOG_MS) {
            lastStatusLogAt = now;
            CarrotHudLog.log(TAG, "status snapshots=" + snapshots + " sent=" + sent
                    + " lastBitmapAgeMs=" + (lastBitmapAt > 0 ? now - lastBitmapAt : -1));
        }
        long reference = lastBitmapAt > 0 ? lastBitmapAt : firstRequestAt;
        boolean dead = reference > 0 && now - reference > DEAD_AFTER_MS;
        if (dead && !deadLogged) {
            deadLogged = true;
            CarrotHudLog.log(TAG, "renderer not answering for " + (now - reference) + " ms; releasing map_main but keep polling");
        }
        // Keep requesting even while "dead": the renderer pauses when the virtual
        // display idles (e.g. long red light) and answers again when it resumes.
        // HUD13.4 stopped requesting here, which froze the HUD map for good.
        if (requestedAt == 0 || now - requestedAt > SNAPSHOT_TIMEOUT_MS) {
            requestedAt = now;
            if (firstRequestAt == 0) {
                firstRequestAt = now;
            }
            final Object target = map;
            mainHandler().post(new Runnable() {
                @Override
                public void run() {
                    requestSnapshot(target);
                }
            });
        }
        return !dead;
    }

    private static volatile boolean deadLogged;

    private static final java.util.List<java.lang.ref.WeakReference<Object>> activities =
            new java.util.ArrayList<java.lang.ref.WeakReference<Object>>();

    /**
     * Called from the patched CarrotNaverBridge.setActivity() (MainActivity.onResume).
     * With nMirror there can be two MainActivity instances (car virtual display and
     * phone display, the latter created by the HUD app's `am start`); the bridge's
     * single `activity` field only remembers the most recently resumed one, which is
     * often the invisible phone-display copy. Keep all of them and pick the one whose
     * map is actually showing.
     */
    public static void registerActivity(Object activity) {
        if (activity == null) {
            return;
        }
        synchronized (activities) {
            for (int i = activities.size() - 1; i >= 0; i--) {
                Object a = activities.get(i).get();
                if (a == null || a == activity) {
                    activities.remove(i);
                }
            }
            activities.add(new java.lang.ref.WeakReference<Object>(activity));
        }
        CarrotHudLog.log(TAG, "activity resumed " + activity.getClass().getName() + " display="
                + displayId(activity) + " total=" + activities.size());
    }

    private static int displayId(Object activity) {
        try {
            android.view.Display d = ((android.app.Activity) activity).getWindowManager().getDefaultDisplay();
            return d == null ? -1 : d.getDisplayId();
        } catch (Throwable t) {
            return -1;
        }
    }

    private static java.util.List<Object> candidateActivities() {
        java.util.List<Object> out = new java.util.ArrayList<Object>();
        synchronized (activities) {
            for (int i = activities.size() - 1; i >= 0; i--) {
                Object a = activities.get(i).get();
                if (a instanceof android.app.Activity && !((android.app.Activity) a).isFinishing()
                        && !((android.app.Activity) a).isDestroyed()) {
                    out.add(a);
                }
            }
        }
        Object single = activityObject();
        if (single != null && !out.contains(single)) {
            out.add(single);
        }
        return out;
    }

    private static Object activityObject() {
        try {
            java.lang.reflect.Field f = CarrotNaverBridge.class.getDeclaredField("activity");
            f.setAccessible(true);
            return f.get(null);
        } catch (Throwable t) {
            return null;
        }
    }

    /** NaverMap of a showing com.naver.maps.map.MapView in any live MainActivity, or null. */
    private static Object phoneMap() {
        Object fallback = null;
        for (Object activity : candidateActivities()) {
            try {
                android.view.View root = ((android.app.Activity) activity).getWindow().getDecorView();
                android.view.View mapView = findMapView(root, 0);
                if (mapView == null) {
                    continue;
                }
                Object map = mapOf(mapView);
                if (map == null) {
                    continue;
                }
                if (mapView.isShown() && root.isAttachedToWindow()) {
                    if (activity != chosenActivity) {
                        chosenActivity = activity;
                        CarrotHudLog.log(TAG, "using MapView of activity on display " + displayId(activity)
                                + " (" + mapView.getWidth() + "x" + mapView.getHeight() + ")");
                    }
                    return map;
                }
                if (fallback == null) {
                    fallback = map;
                }
            } catch (Throwable t) {
                long now = SystemClock.elapsedRealtime();
                if (now - lastStatusLogAt >= STATUS_LOG_MS) {
                    lastStatusLogAt = now;
                    CarrotHudLog.log(TAG, "phone MapView lookup failed: " + t);
                }
            }
        }
        return fallback;
    }

    private static volatile Object chosenActivity;

    private static Object mapOf(android.view.View mapView) throws Exception {
        java.lang.reflect.Field delegateField = null;
        for (Class<?> c = mapView.getClass(); c != null && delegateField == null; c = c.getSuperclass()) {
            try {
                delegateField = c.getDeclaredField("a0");
            } catch (NoSuchFieldException ignored) {
            }
        }
        if (delegateField == null) {
            return null;
        }
        delegateField.setAccessible(true);
        Object delegate = delegateField.get(mapView);
        if (delegate == null) {
            return null;
        }
        Method f = delegate.getClass().getMethod("f", new Class<?>[0]);
        return f.invoke(delegate, new Object[0]);
    }

    private static android.view.View findMapView(android.view.View view, int depth) {
        if (view == null || depth > 40) {
            return null;
        }
        for (Class<?> c = view.getClass(); c != null; c = c.getSuperclass()) {
            if ("com.naver.maps.map.MapView".equals(c.getName())) {
                return view;
            }
        }
        if (view instanceof android.view.ViewGroup) {
            android.view.ViewGroup group = (android.view.ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) {
                android.view.View found = findMapView(group.getChildAt(i), depth + 1);
                if (found != null) {
                    return found;
                }
            }
        }
        return null;
    }

    private static Handler mainHandler() {
        Handler h = main;
        if (h == null) {
            h = new Handler(Looper.getMainLooper());
            main = h;
        }
        return h;
    }

    /** Main thread. */
    private static void requestSnapshot(Object map) {
        try {
            Object cb = callback(map.getClass().getClassLoader());
            Class<?> cbClass = Class.forName("com.naver.maps.map.NaverMap$SnapshotReadyCallback", true,
                    map.getClass().getClassLoader());
            Method take = map.getClass().getMethod("p2", new Class<?>[]{boolean.class, cbClass});
            take.invoke(map, new Object[]{Boolean.FALSE, cb});
            snapshots++;
        } catch (Throwable t) {
            CarrotHudLog.log(TAG, "takeSnapshot failed: " + t);
            requestedAt = 0;
        }
    }

    private static Object callback(ClassLoader loader) throws ClassNotFoundException {
        Object cb = callbackProxy;
        if (cb != null) {
            return cb;
        }
        Class<?> cbClass = Class.forName("com.naver.maps.map.NaverMap$SnapshotReadyCallback", true, loader);
        cb = Proxy.newProxyInstance(loader, new Class<?>[]{cbClass}, new InvocationHandler() {
            @Override
            public Object invoke(Object proxy, Method method, Object[] args) {
                String name = method.getName();
                if ("a".equals(name) && args != null && args.length == 1 && args[0] instanceof Bitmap) {
                    onSnapshot((Bitmap) args[0]);
                    return null;
                }
                if ("hashCode".equals(name)) {
                    return Integer.valueOf(System.identityHashCode(proxy));
                }
                if ("equals".equals(name)) {
                    return Boolean.valueOf(args != null && args.length == 1 && proxy == args[0]);
                }
                if ("toString".equals(name)) {
                    return "CarrotCarMapSnapshot.callback";
                }
                return null;
            }
        });
        callbackProxy = cb;
        return cb;
    }

    /**
     * Main thread, from the SDK. The socket write must NOT happen here: Android
     * throws NetworkOnMainThreadException for main-thread socket I/O and the
     * bridge's sendBitmap() swallows it, so HUD13 counted frames that never left
     * the phone. Crop here, encode+send on a worker thread.
     */
    private static void onSnapshot(Bitmap source) {
        long now = SystemClock.elapsedRealtime();
        requestedAt = 0;
        if (deadLogged) {
            deadLogged = false;
            CarrotHudLog.log(TAG, "renderer answering again after " + (lastBitmapAt > 0 ? now - lastBitmapAt : -1) + " ms");
        }
        lastBitmapAt = now;
        final CarrotNaverBridge b = bridge;
        if (b == null || source == null || source.isRecycled()) {
            return;
        }
        try {
            final Bitmap out = fitCenterCrop(source, WIDTH, HEIGHT);
            if (snapshots == 1 || sent == 0) {
                CarrotHudLog.log(TAG, "first snapshot " + source.getWidth() + "x" + source.getHeight());
            }
            workerHandler().post(new Runnable() {
                @Override
                public void run() {
                    try {
                        sendJpeg(b, out);
                        sent++;
                    } catch (Throwable t) {
                        CarrotHudLog.log(TAG, "sendBitmap failed: " + t);
                    }
                }
            });
        } catch (Throwable t) {
            CarrotHudLog.log(TAG, "snapshot handling failed: " + t);
        }
    }

    /**
     * Worker thread. Same JSON item_update the bridge's sendBitmap() produces, but
     * at TMAP's JPEG quality instead of the bridge's fixed 90.
     */
    private static void sendJpeg(CarrotNaverBridge b, Bitmap bitmap) throws Exception {
        java.io.ByteArrayOutputStream stream = new java.io.ByteArrayOutputStream(64 * 1024);
        try {
            if (!bitmap.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, stream)) {
                throw new IllegalStateException("JPEG compress failed");
            }
        } finally {
            bitmap.recycle();
        }
        byte[] jpeg = stream.toByteArray();
        String value = "{\"format\":\"jpeg\",\"width\":" + WIDTH + ",\"height\":" + HEIGHT
                + ",\"data\":\"" + android.util.Base64.encodeToString(jpeg, android.util.Base64.NO_WRAP) + "\"}";
        Method send = bridgeSend;
        if (send == null) {
            send = CarrotNaverBridge.class.getDeclaredMethod("send", String.class, String.class);
            send.setAccessible(true);
            bridgeSend = send;
        }
        send.invoke(b, "map_main", value);
        if (sent == 0) {
            CarrotHudLog.log(TAG, "first map_main sent " + jpeg.length + " bytes q" + JPEG_QUALITY);
        }
    }

    private static volatile Method bridgeSend;

    private static Handler workerHandler() {
        Handler h = worker;
        if (h == null) {
            android.os.HandlerThread thread = new android.os.HandlerThread("carrot-map-snapshot-send");
            thread.start();
            h = new Handler(thread.getLooper());
            worker = h;
        }
        return h;
    }

    static Bitmap fitCenterCrop(Bitmap src, int width, int height) {
        int sw = src.getWidth();
        int sh = src.getHeight();
        if (sw == width && sh == height) {
            return src.copy(Bitmap.Config.ARGB_8888, false);
        }
        float scale = Math.max(width / (float) sw, height / (float) sh);
        int cropW = Math.min(sw, Math.round(width / scale));
        int cropH = Math.min(sh, Math.round(height / scale));
        int left = (sw - cropW) / 2;
        // Portrait navigation views keep the vehicle in the lower part of the map;
        // bias the band towards the bottom so the car stays in frame.
        int top = sh > sw ? Math.round(sh * 0.62f - cropH / 2f) : (sh - cropH) / 2;
        top = Math.max(0, Math.min(sh - cropH, top));
        Bitmap out = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(out);
        Paint paint = new Paint(Paint.FILTER_BITMAP_FLAG);
        canvas.drawBitmap(src, new Rect(left, top, left + cropW, top + cropH), new Rect(0, 0, width, height), paint);
        return out;
    }
}
