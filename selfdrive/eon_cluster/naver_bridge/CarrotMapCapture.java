package com.naver.map.carrot;

import android.app.Activity;
import android.content.pm.ActivityInfo;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Rect;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.SystemClock;
import android.view.PixelCopy;
import android.view.SurfaceView;
import android.view.TextureView;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewParent;

/** Capture only Naver's native map renderer, never the Activity window. */
public final class CarrotMapCapture {
    private static java.lang.ref.WeakReference<Activity> oriented = new java.lang.ref.WeakReference<>(null);
    private static int originalOrientation;
    private static boolean orientationSaved;
    private static Handler worker;
    private static volatile boolean inFlight;
    private static volatile long lastClear;

    private static synchronized Handler worker() {
        if (worker == null) {
            HandlerThread thread = new HandlerThread("carrot-map-capture");
            thread.start();
            worker = new Handler(thread.getLooper());
        }
        return worker;
    }

    // capture() is invoked on the Activity UI thread by the existing bridge.
    public static void capture(Object owner, CarrotNaverBridge bridge) {
        if (!(owner instanceof Activity) || bridge == null || inFlight) return;
        Activity activity = (Activity) owner;
        NaverHudSettings.start();
        NaverHudSettings.Values settings = NaverHudSettings.current;
        if (activity.isFinishing() || activity.isDestroyed() || activity.getWindow() == null) {
            clear(bridge);
            return;
        }
        if (oriented.get() != activity) {
            oriented = new java.lang.ref.WeakReference<>(activity);
            if (!orientationSaved) {
                originalOrientation = activity.getRequestedOrientation();
                orientationSaved = true;
            }
        }
        int orientation = settings.landscape ? ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE : originalOrientation;
        if (activity.getRequestedOrientation() != orientation) {
            try { activity.setRequestedOrientation(orientation); }
            catch (IllegalStateException ignored) { }
            clear(bridge);
            return; // Let the Activity lay out the real landscape map first.
        }
        View map = findMap(activity.getWindow().getDecorView());
        if (map == null) {
            clear(bridge);
            return;
        }
        int sourceWidth = map.getWidth();
        int sourceHeight = map.getHeight();
        if (map instanceof SurfaceView) {
            Rect frame = ((SurfaceView) map).getHolder().getSurfaceFrame();
            if (frame.width() > 0 && frame.height() > 0) {
                sourceWidth = frame.width();
                sourceHeight = frame.height();
            }
        }
        int[] size = MapCaptureGeometry.captureSize(sourceWidth, sourceHeight);
        // Never squeeze a portrait map into the landscape output bitmap.
        Bitmap bitmap = Bitmap.createBitmap(size[0], size[1], Bitmap.Config.ARGB_8888);
        inFlight = true;
        try {
            if (map instanceof TextureView) {
                // Window PixelCopy can omit a separately composited GL texture.
                Bitmap captured = ((TextureView) map).getBitmap(bitmap);
                if (captured == null) {
                    bitmap.recycle();
                    inFlight = false;
                    clear(bridge);
                    return;
                }
                worker().post(() -> finish(bridge, map, bitmap, true));
            } else {
                // SurfaceView has its own buffer: copy that buffer, not Window.
                PixelCopy.request((SurfaceView) map, bitmap,
                        result -> finish(bridge, map, bitmap, result == PixelCopy.SUCCESS), worker());
            }
        } catch (RuntimeException failure) {
            bitmap.recycle();
            inFlight = false;
            clear(bridge);
        }
    }

    private static void finish(CarrotNaverBridge bridge, View map, Bitmap bitmap, boolean ok) {
        try {
            if (ok && map.isAttachedToWindow() && map.isShown()) {
                NaverHudSettings.Values settings = NaverHudSettings.current;
                int[] crop = settings.fit ? new int[]{0, 0, bitmap.getWidth(), bitmap.getHeight()}
                        : MapCaptureGeometry.crop(bitmap.getWidth(), bitmap.getHeight());
                int[] destination = MapCaptureGeometry.destination(crop[2] - crop[0], crop[3] - crop[1], settings.scale);
                Bitmap output = Bitmap.createBitmap(MapCaptureGeometry.WIDTH, MapCaptureGeometry.HEIGHT,
                        Bitmap.Config.ARGB_8888);
                try {
                    Canvas canvas = new Canvas(output);
                    canvas.drawColor(0xff101820);
                    canvas.drawBitmap(bitmap, new Rect(crop[0], crop[1], crop[2], crop[3]),
                            new Rect(destination[0], destination[1], destination[2], destination[3]),
                            new Paint(Paint.FILTER_BITMAP_FLAG));
                    bridge.sendBitmap(output); // JPEG encoder owns/recycles it.
                } finally {
                    if (!output.isRecycled()) output.recycle();
                }
            } else {
                clear(bridge);
            }
        } finally {
            if (!bitmap.isRecycled()) bitmap.recycle();
            inFlight = false;
        }
    }

    private static void clear(CarrotNaverBridge bridge) {
        long now = SystemClock.elapsedRealtime();
        if (now - lastClear < 1000L) return;
        lastClear = now;
        worker().post(() -> {
            try { bridge.clearMap(); } catch (Exception ignored) { }
        });
    }

    private static boolean isMapRenderer(View view) {
        if (!(view instanceof TextureView) && !(view instanceof SurfaceView)) return false;
        // Verified renderer hierarchy in CarrotNaver 6.9.1.3. Do not choose an
        // ad/video SurfaceView simply because it is the largest on the page.
        for (Class<?> type = view.getClass(); type != null; type = type.getSuperclass()) {
            if (type.getName().equals("com.navercorp.android.vgx.lib.VgxGLTextureView")) return true;
        }
        // Naver MapView owns either a plain TextureView, GLSurfaceView, or
        // VulkanSurfaceView depending on its options/device capabilities.
        for (ViewParent parent = view.getParent(); parent != null; parent = parent.getParent()) {
            for (Class<?> type = parent.getClass(); type != null; type = type.getSuperclass()) {
                if (type.getName().equals("com.naver.maps.map.MapView")) return true;
            }
        }
        return false;
    }

    private static View findMap(View view) {
        if (view == null || !view.isShown() || view.getAlpha() <= 0f
                || view.getWidth() < 64 || view.getHeight() < 64) return null;
        if (isMapRenderer(view)) {
            Rect visible = new Rect();
            if (!view.getGlobalVisibleRect(visible) || visible.width() < view.getWidth() / 2
                    || visible.height() < view.getHeight() / 2) return null;
            if (view instanceof TextureView && ((TextureView) view).isAvailable()) return view;
            if (view instanceof SurfaceView && ((SurfaceView) view).getHolder().getSurface().isValid()) return view;
        }
        View best = null;
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) {
                View candidate = findMap(group.getChildAt(i));
                if (candidate != null && (best == null || (long) candidate.getWidth() * candidate.getHeight()
                        > (long) best.getWidth() * best.getHeight())) best = candidate;
            }
        }
        return best;
    }
}
