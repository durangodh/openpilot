package com.naver.map.carrot;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.os.SystemClock;
import android.view.View;

import java.io.ByteArrayOutputStream;
import java.lang.ref.WeakReference;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;

/** Publishes NAVER's native traffic-light/countdown widget as a transparent PNG. */
public final class CarrotTrafficSignalCapture {
    private static final String TAG = "CarrotTrafficSignalCapture";
    private static final long CAPTURE_INTERVAL_MS = 400;
    private static final int MAX_WIDTH = 512;
    private static final int MAX_HEIGHT = 256;

    private static final List<WeakReference<View>> views = new ArrayList<WeakReference<View>>();
    private static Handler main;
    private static Handler worker;
    private static volatile long requestedAt;
    private static volatile boolean inFlight;
    private static volatile boolean present;
    private static volatile Method bridgeSend;

    private CarrotTrafficSignalCapture() {
    }

    /** Called from the patched NaviTrafficSignalView three-argument constructor. */
    public static void register(Object candidate) {
        if (!(candidate instanceof View)) {
            return;
        }
        View view = (View) candidate;
        synchronized (views) {
            for (int i = views.size() - 1; i >= 0; i--) {
                View old = views.get(i).get();
                if (old == null || old == view) {
                    views.remove(i);
                }
            }
            views.add(new WeakReference<View>(view));
        }
        CarrotHudLog.log(TAG, "registered " + candidate.getClass().getName());
    }

    /** Bridge thread. The actual View draw is posted to Android's main thread. */
    public static void capture(final CarrotNaverBridge bridge) {
        if (bridge == null) {
            return;
        }
        long now = SystemClock.elapsedRealtime();
        if (inFlight || now - requestedAt < CAPTURE_INTERVAL_MS) {
            return;
        }
        requestedAt = now;
        inFlight = true;
        mainHandler().post(new Runnable() {
            @Override
            public void run() {
                View view = visibleView();
                if (view == null) {
                    inFlight = false;
                    if (present) {
                        workerHandler().post(new Runnable() {
                            @Override
                            public void run() {
                                clear(bridge);
                            }
                        });
                    }
                    return;
                }
                int width = view.getWidth();
                int height = view.getHeight();
                if (width <= 0 || height <= 0 || width > MAX_WIDTH || height > MAX_HEIGHT) {
                    inFlight = false;
                    return;
                }
                try {
                    final Bitmap bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
                    view.draw(new Canvas(bitmap));
                    workerHandler().post(new Runnable() {
                        @Override
                        public void run() {
                            send(bridge, bitmap);
                        }
                    });
                } catch (Throwable t) {
                    inFlight = false;
                    CarrotHudLog.log(TAG, "draw failed: " + t);
                }
            }
        });
    }

    private static View visibleView() {
        synchronized (views) {
            for (int i = views.size() - 1; i >= 0; i--) {
                View view = views.get(i).get();
                if (view == null) {
                    views.remove(i);
                } else if (view.isAttachedToWindow() && view.isShown()
                        && view.getVisibility() == View.VISIBLE && view.getAlpha() > 0f) {
                    return view;
                }
            }
        }
        return null;
    }

    private static void send(CarrotNaverBridge bridge, Bitmap bitmap) {
        try {
            int width = bitmap.getWidth();
            int height = bitmap.getHeight();
            ByteArrayOutputStream stream = new ByteArrayOutputStream(16 * 1024);
            if (!bitmap.compress(Bitmap.CompressFormat.PNG, 100, stream)) {
                throw new IllegalStateException("PNG compress failed");
            }
            String value = "{\"format\":\"png\",\"width\":" + width + ",\"height\":" + height
                    + ",\"data\":\"" + android.util.Base64.encodeToString(stream.toByteArray(),
                    android.util.Base64.NO_WRAP) + "\"}";
            invokeSend(bridge, value);
            present = true;
        } catch (Throwable t) {
            CarrotHudLog.log(TAG, "send failed: " + t);
        } finally {
            bitmap.recycle();
            inFlight = false;
        }
    }

    private static void clear(CarrotNaverBridge bridge) {
        try {
            invokeSend(bridge, null);
            present = false;
        } catch (Throwable t) {
            CarrotHudLog.log(TAG, "clear failed: " + t);
        }
    }

    private static void invokeSend(CarrotNaverBridge bridge, String value) throws Exception {
        Method send = bridgeSend;
        if (send == null) {
            send = CarrotNaverBridge.class.getDeclaredMethod("send", String.class, String.class);
            send.setAccessible(true);
            bridgeSend = send;
        }
        send.invoke(bridge, "traffic_signal", value);
    }

    private static Handler mainHandler() {
        Handler h = main;
        if (h == null) {
            h = new Handler(Looper.getMainLooper());
            main = h;
        }
        return h;
    }

    private static Handler workerHandler() {
        Handler h = worker;
        if (h == null) {
            HandlerThread thread = new HandlerThread("carrot-traffic-signal-send");
            thread.start();
            h = new Handler(thread.getLooper());
            worker = h;
        }
        return h;
    }
}
