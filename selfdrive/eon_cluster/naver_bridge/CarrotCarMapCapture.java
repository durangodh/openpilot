package com.naver.map.carrot;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Rect;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.SystemClock;
import android.util.Log;
import android.view.PixelCopy;
import android.view.Surface;

/** Reads Naver's existing Android Auto map surface; never owns/releases it. */
public final class CarrotCarMapCapture {
    private static final Object lock = new Object();
    private static Surface surface;
    private static Object owner;
    private static int width, height;
    private static long generation, lastGood, lastWarning;
    private static boolean inFlight, hasFrame;
    private static Handler handler;

    private static synchronized Handler worker() {
        if (handler == null) {
            HandlerThread thread = new HandlerThread("carrot-car-map");
            thread.start();
            handler = new Handler(thread.getLooper());
        }
        return handler;
    }

    // Called after MapProvider has attached/resized the Android Auto surface.
    public static void available(Object callback, Surface next, int w, int h) {
        if (next == null || !next.isValid() || w <= 0 || h <= 0) return;
        synchronized (lock) {
            boolean replacement = owner != callback || surface != next;
            owner = callback;
            surface = next;
            width = w;
            height = h;
            generation++;
            if (replacement) hasFrame = false;
            // This is only a retry grace timestamp. A valid Surface is not a
            // valid map until PixelCopy has returned the first real frame.
            lastGood = SystemClock.elapsedRealtime();
        }
        Log.i("CarrotCarMap", "car map surface available: " + w + "x" + h);
    }

    // Compare callback owners so a late destroy cannot clear a replacement.
    public static void destroyed(Object callback) {
        synchronized (lock) {
            if (owner != callback) return;
            surface = null;
            owner = null;
            hasFrame = false;
            generation++;
        }
        Log.i("CarrotCarMap", "car map surface destroyed; using phone map");
    }

    public static boolean active() {
        synchronized (lock) {
            return surface != null && surface.isValid() && hasFrame;
        }
    }

    /** True only after the car Surface has delivered at least one real frame. */
    public static boolean capture(CarrotNaverBridge bridge) {
        final Surface source;
        final long ticket;
        final int[] size;
        synchronized (lock) {
            if (surface == null || !surface.isValid()) return false;
            if (inFlight) return hasFrame;
            source = surface;
            ticket = generation;
            size = MapCaptureGeometry.captureSize(width, height);
            inFlight = true;
        }
        NaverHudSettings.start();
        Bitmap bitmap = null;
        try {
            bitmap = Bitmap.createBitmap(size[0], size[1], Bitmap.Config.ARGB_8888);
            final Bitmap captured = bitmap;
            PixelCopy.request(source, captured,
                    result -> finish(bridge, source, ticket, captured, result), worker());
        } catch (RuntimeException failure) {
            if (bitmap != null) bitmap.recycle();
            synchronized (lock) { inFlight = false; }
            failed(bridge, ticket, -1);
        }
        synchronized (lock) {
            return generation == ticket && hasFrame;
        }
    }

    private static void failed(CarrotNaverBridge bridge, long ticket, int result) {
        boolean clear;
        boolean warn;
        long now = SystemClock.elapsedRealtime();
        synchronized (lock) {
            if (generation != ticket) return;
            clear = now - lastGood >= 2000L;
            warn = now - lastWarning >= 10000L;
            if (warn) lastWarning = now;
            if (clear) {
                lastGood = now;
                hasFrame = false;
            }
        }
        if (warn) Log.w("CarrotCarMap", "car map copy failed: " + result);
        if (clear) {
            try { bridge.clearMap(); } catch (Exception ignored) { }
        }
    }

    private static void finish(CarrotNaverBridge bridge, Surface source, long ticket,
                               Bitmap captured, int result) {
        Bitmap output = null;
        try {
            synchronized (lock) {
                if (generation != ticket || surface != source || !source.isValid()) return;
            }
            if (result != PixelCopy.SUCCESS) {
                failed(bridge, ticket, result);
                return;
            }
            NaverHudSettings.Values settings = NaverHudSettings.current;
            int[] crop = settings.fit ? new int[]{0, 0, captured.getWidth(), captured.getHeight()}
                    : MapCaptureGeometry.crop(captured.getWidth(), captured.getHeight());
            int[] dst = MapCaptureGeometry.destination(crop[2] - crop[0], crop[3] - crop[1], settings.scale);
            output = Bitmap.createBitmap(MapCaptureGeometry.WIDTH, MapCaptureGeometry.HEIGHT, Bitmap.Config.ARGB_8888);
            new Canvas(output).drawBitmap(captured, new Rect(crop[0], crop[1], crop[2], crop[3]),
                    new Rect(dst[0], dst[1], dst[2], dst[3]), new Paint(Paint.FILTER_BITMAP_FLAG));
            synchronized (lock) { if (generation != ticket) return; }
            bridge.sendBitmap(output);
            synchronized (lock) {
                if (generation == ticket) {
                    hasFrame = true;
                    lastGood = SystemClock.elapsedRealtime();
                }
            }
        } finally {
            if (output != null) output.recycle();
            captured.recycle();
            synchronized (lock) { inFlight = false; }
        }
    }
}
