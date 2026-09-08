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
 * HUD12: map_main from the Android Auto navigation map via NaverMap.takeSnapshot.
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
    static final int WIDTH = 960;
    static final int HEIGHT = 576;
    private static final long SNAPSHOT_TIMEOUT_MS = 3000;
    /** No callback for this long -> the AA renderer is gone; release map_main. */
    private static final long DEAD_AFTER_MS = 12000;
    private static final long STATUS_LOG_MS = 5000;

    private static volatile Object provider;
    private static volatile Object naverMap;
    private static volatile CarrotNaverBridge bridge;
    private static Handler main;
    private static volatile long requestedAt;
    private static volatile long lastBitmapAt;
    private static volatile long firstRequestAt;
    private static volatile long snapshots;
    private static volatile long sent;
    private static long lastStatusLogAt;
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
        if (b == null || p == null) {
            return false;
        }
        bridge = b;
        Object map = null;
        try {
            Method i = p.getClass().getMethod("i", new Class<?>[0]);
            map = i.invoke(p, new Object[0]);
        } catch (Throwable t) {
            CarrotHudLog.log(TAG, "MapProvider.i() failed: " + t);
            return false;
        }
        long now = SystemClock.elapsedRealtime();
        if (map == null) {
            if (now - lastStatusLogAt >= STATUS_LOG_MS) {
                lastStatusLogAt = now;
                CarrotHudLog.log(TAG, "MapProvider present, NaverMap not ready yet");
            }
            return false;
        }
        if (map != naverMap) {
            naverMap = map;
            firstRequestAt = 0;
            lastBitmapAt = 0;
            requestedAt = 0;
            CarrotHudLog.log(TAG, "AA NaverMap available " + map.getClass().getName());
        }
        if (now - lastStatusLogAt >= STATUS_LOG_MS) {
            lastStatusLogAt = now;
            CarrotHudLog.log(TAG, "status snapshots=" + snapshots + " sent=" + sent
                    + " lastBitmapAgeMs=" + (lastBitmapAt > 0 ? now - lastBitmapAt : -1));
        }
        long reference = lastBitmapAt > 0 ? lastBitmapAt : firstRequestAt;
        if (reference > 0 && now - reference > DEAD_AFTER_MS) {
            // Renderer stopped answering (AA disconnected?) -> let phone capture run.
            return false;
        }
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
        return true;
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

    /** Main thread, from the SDK. */
    private static void onSnapshot(Bitmap source) {
        long now = SystemClock.elapsedRealtime();
        requestedAt = 0;
        lastBitmapAt = now;
        CarrotNaverBridge b = bridge;
        if (b == null || source == null || source.isRecycled()) {
            return;
        }
        try {
            Bitmap out = fitCenterCrop(source, WIDTH, HEIGHT);
            if (sent == 0) {
                CarrotHudLog.log(TAG, "first snapshot " + source.getWidth() + "x" + source.getHeight());
            }
            sent++;
            b.sendBitmap(out); // JPEG-encodes at the live quality setting and recycles.
        } catch (Throwable t) {
            CarrotHudLog.log(TAG, "snapshot handling failed: " + t);
        }
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
        int top = (sh - cropH) / 2;
        Bitmap out = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(out);
        Paint paint = new Paint(Paint.FILTER_BITMAP_FLAG);
        canvas.drawBitmap(src, new Rect(left, top, left + cropW, top + cropH), new Rect(0, 0, width, height), paint);
        return out;
    }
}
