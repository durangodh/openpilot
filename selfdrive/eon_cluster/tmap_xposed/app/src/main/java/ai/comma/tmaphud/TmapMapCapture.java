package ai.comma.tmaphud;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Point;
import android.graphics.Rect;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.os.SystemClock;
import android.view.PixelCopy;
import android.view.SurfaceView;
import android.view.TextureView;
import android.view.View;

import java.io.ByteArrayOutputStream;
import java.lang.ref.WeakReference;
import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 티맵 지도 엔진(VSM)이 그린 GL 지도를 map_main(640×384 JPEG)으로 보낸다.
 *
 * VSMMapView 는 지도를 자체 SurfaceView(기본) 또는 TextureView 에 그린다. 경로선과
 * 차량 아이콘도 같은 GL 화면에 그려지므로 그 표면만 PixelCopy 로 복사하면
 * "지도+경로+차량" 화면이 된다(안드로이드 버튼·안내 배너 등 UI 는 빠진다).
 * 지도 엔진의 화면 중심(MapEngine.getScreenCenter, 주행 중 차량 위치)을 기준으로
 * 5:3 영역을 잘라 GPU 에서 바로 640×384 로 줄인다.
 *
 * 티맵 화면이 꺼지거나 다른 앱이 앞에 오면 표면이 사라져 프레임이 멈춘다.
 * 그때는 CNV2 clear 를 한 번 보내 EON 이 옛 지도를 남기지 않게 한다.
 */
final class TmapMapCapture {
    private static final int WIDTH = 640, HEIGHT = 384, JPEG_QUALITY = 65;
    private static final long FRAME_INTERVAL_MS = 200, CHECK_INTERVAL_MS = 40;
    private static final long REQUEST_TIMEOUT_MS = 600;
    private static final long CLEAR_AFTER_MS = 2000;
    // 화면 중심을 못 읽을 때: 세로 화면은 차량이 아래쪽에 있다.
    private static final float FALLBACK_CENTER_Y = 0.62f;

    private final TmapNaviClient client;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Handler copyHandler;
    private final ExecutorService encoder = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "tmap-hud-map-encode");
        t.setDaemon(true);
        return t;
    });
    private final ScheduledExecutorService ticker = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "tmap-hud-map-tick");
        t.setDaemon(true);
        return t;
    });
    private final AtomicBoolean posted = new AtomicBoolean();
    private final AtomicBoolean started = new AtomicBoolean();
    private final AtomicBoolean encoding = new AtomicBoolean();
    private final List<WeakReference<View>> views = new ArrayList<>();

    // 아래 값은 메인 스레드에서만 바꾼다.
    private View activeView;
    private long requestedAt, lastRequestAt, lastFrameAt, lastStatusAt;
    private boolean cleared = true;
    private volatile long sequence;
    private long sent;

    TmapMapCapture(TmapNaviClient client) {
        this.client = client;
        HandlerThread thread = new HandlerThread("tmap-hud-pixelcopy");
        thread.setDaemon(true);
        thread.start();
        copyHandler = new Handler(thread.getLooper());
    }

    /** VSMMapView 생성자 후킹에서 호출(메인 스레드). */
    void addView(View view) {
        if (view == null) return;
        synchronized (views) {
            views.removeIf(ref -> ref.get() == null || ref.get() == view);
            views.add(new WeakReference<>(view));
        }
    }

    void start() {
        if (started.compareAndSet(false, true)) {
            ticker.scheduleWithFixedDelay(this::tick, 0, CHECK_INTERVAL_MS, TimeUnit.MILLISECONDS);
            TmapHudLog.line("map capture started");
        }
    }

    private void tick() {
        if (!client.ready()) return;
        if (!posted.compareAndSet(false, true)) return;
        main.post(() -> {
            try {
                captureOnMain();
            } catch (Throwable error) {
                TmapHudLog.ex("map capture", error);
            } finally {
                posted.set(false);
            }
        });
    }

    private void captureOnMain() {
        long now = SystemClock.elapsedRealtime();
        View view = chooseView();
        if (view != activeView) {
            activeView = view;
            requestedAt = 0;
            if (view != null) {
                TmapHudLog.line("map view selected: " + view.getClass().getName()
                        + " " + view.getWidth() + "x" + view.getHeight());
            }
        }
        if (view == null) {
            if (!cleared && now - lastFrameAt > CLEAR_AFTER_MS) {
                cleared = true;
                client.sendMap(Cnv2.frame(Cnv2.TYPE_CLEAR, Cnv2.FORMAT_JPEG, sequence++, null, 0, 0));
                TmapHudLog.line("map view gone; map cleared");
            }
            if (now - lastStatusAt > 10000) {
                lastStatusAt = now;
                TmapHudLog.status("waiting for a visible VSMMapView");
            }
            return;
        }
        if (requestedAt != 0 && now - requestedAt < REQUEST_TIMEOUT_MS) return;
        if (now - lastRequestAt < FRAME_INTERVAL_MS) return;
        if (encoding.get()) return;   // 인코더가 밀리면 새로 찍지 않는다(최신 1장 정책).

        TextureView texture = (TextureView) fieldValue(view, "mTextureView");
        SurfaceView surface = (SurfaceView) fieldValue(view, "mSurfaceView");
        Point center = screenCenter(view);
        if (texture != null && texture.isAvailable() && texture.isShown()) {
            lastRequestAt = now;
            Bitmap full = texture.getBitmap();
            if (full != null) {
                Rect src = cropRect(full.getWidth(), full.getHeight(), center);
                Bitmap out = Bitmap.createBitmap(WIDTH, HEIGHT, Bitmap.Config.ARGB_8888);
                new Canvas(out).drawBitmap(full, src, new Rect(0, 0, WIDTH, HEIGHT),
                        new Paint(Paint.FILTER_BITMAP_FLAG));
                full.recycle();
                encode(out);
            }
            return;
        }
        if (surface == null || !surface.getHolder().getSurface().isValid()) return;
        Rect frame = surface.getHolder().getSurfaceFrame();
        if (frame.width() <= 0 || frame.height() <= 0) return;
        Rect src = cropRect(frame.width(), frame.height(), center);
        final Bitmap out = Bitmap.createBitmap(WIDTH, HEIGHT, Bitmap.Config.ARGB_8888);
        requestedAt = now;
        lastRequestAt = now;
        try {
            PixelCopy.request(surface, src, out, result -> {
                main.post(() -> requestedAt = 0);
                if (result == PixelCopy.SUCCESS) {
                    encode(out);
                } else {
                    out.recycle();
                    TmapHudLog.status("PixelCopy failed: " + result);
                }
            }, copyHandler);
        } catch (Throwable error) {
            requestedAt = 0;
            out.recycle();
            TmapHudLog.ex("PixelCopy request", error);
        }
    }

    private void encode(final Bitmap image) {
        if (!encoding.compareAndSet(false, true)) {
            image.recycle();
            return;
        }
        encoder.execute(() -> {
            try {
                ByteArrayOutputStream bytes = new ByteArrayOutputStream(80000);
                image.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, bytes);
                client.sendMap(Cnv2.frame(Cnv2.TYPE_IMAGE, Cnv2.FORMAT_JPEG, sequence++,
                        bytes.toByteArray(), WIDTH, HEIGHT));
                main.post(() -> {
                    lastFrameAt = SystemClock.elapsedRealtime();
                    cleared = false;
                });
                if (++sent == 1) TmapHudLog.line("first map frame sent");
            } catch (Throwable error) {
                TmapHudLog.ex("map encode", error);
            } finally {
                image.recycle();
                encoding.set(false);
            }
        });
    }

    /** 화면에 보이는 VSMMapView 중 가장 큰 것(주행 지도). 작은 미리보기 지도는 무시된다. */
    private View chooseView() {
        View best = null;
        long bestArea = 0;
        synchronized (views) {
            for (int i = views.size() - 1; i >= 0; i--) {
                View v = views.get(i).get();
                if (v == null) {
                    views.remove(i);
                    continue;
                }
                if (!v.isShown() || !v.isAttachedToWindow() || v.getWindowVisibility() != View.VISIBLE) continue;
                long area = (long) v.getWidth() * v.getHeight();
                if (area > bestArea) {
                    bestArea = area;
                    best = v;
                }
            }
        }
        return best;
    }

    private static Point screenCenter(View view) {
        try {
            Object engine = view.getClass().getMethod("mapEngine").invoke(view);
            if (engine == null) return null;
            Object p = engine.getClass().getMethod("getScreenCenter").invoke(engine);
            return p instanceof Point ? (Point) p : null;
        } catch (Throwable ignored) {
            return null;
        }
    }

    /** 표면(w×h)에서 화면 중심을 기준으로 5:3 영역을 고른다. */
    static Rect cropRect(int w, int h, Point center) {
        int cropW = w, cropH = Math.round(w * HEIGHT / (float) WIDTH);
        if (cropH > h) {
            cropH = h;
            cropW = Math.min(w, Math.round(h * WIDTH / (float) HEIGHT));
        }
        int cx = w / 2;
        int cy = Math.round(h * (h > w ? FALLBACK_CENTER_Y : 0.5f));
        if (center != null && center.x > 0 && center.x < w && center.y > 0 && center.y < h) {
            cx = center.x;
            cy = center.y;
        }
        int left = Math.max(0, Math.min(w - cropW, cx - cropW / 2));
        int top = Math.max(0, Math.min(h - cropH, cy - cropH / 2));
        return new Rect(left, top, left + cropW, top + cropH);
    }

    private static Object fieldValue(Object target, String name) {
        for (Class<?> t = target.getClass(); t != null; t = t.getSuperclass()) {
            try {
                Field f = t.getDeclaredField(name);
                f.setAccessible(true);
                return f.get(target);
            } catch (NoSuchFieldException ignored) {
                // 상위 클래스(VSMMapView)에 있다.
            } catch (Throwable error) {
                return null;
            }
        }
        return null;
    }
}
