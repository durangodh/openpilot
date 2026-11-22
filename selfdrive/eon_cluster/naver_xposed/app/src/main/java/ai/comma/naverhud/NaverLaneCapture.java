package ai.comma.naverhud;

import android.app.Activity;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Rect;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.os.SystemClock;
import android.view.PixelCopy;
import android.view.View;
import android.view.Window;
import java.util.List;

/** One window copy for both original lane/distance and traffic-signal pixels.
 * No replacement arrows, distance text, signal lamps or countdown are drawn.
 * Missing screen regions are hidden and searched again on the next tick. */
final class NaverLaneCapture {
    interface Listener {
        void onCaptured(View view);
        void onLost();
        void onSignalCaptured(View view);
        void onSignalLost();
    }

    private static final long TICK_MS = 500;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Handler worker;
    private final Listener listener;
    private final ImageView laneView, signalView;
    private final Runnable tickTask = this::tick;
    private Activity activity;
    private int lanes;
    private boolean signal, running, busy, loggedLane, loggedSignal, loggedError;
    private long generation, requestAt;

    NaverLaneCapture(Context context, Listener listener) {
        this.listener = listener;
        laneView = new ImageView(context);
        signalView = new ImageView(context);
        HandlerThread thread = new HandlerThread("naver-guidance-capture");
        thread.start();
        worker = new Handler(thread.getLooper());
    }

    /** Main thread; signal capture remains active without lane guidance. */
    void update(Activity a, int n, boolean showSignal) {
        if (activity != a || lanes != n || signal != showSignal) {
            generation++;
            if (activity != a || lanes != n) listener.onLost();
            if (activity != a || signal != showSignal) listener.onSignalLost();
        }
        activity = a;
        lanes = n;
        signal = showSignal;
        if (a == null || (n <= 0 && !showSignal)) {
            running = false;
            generation++;
            main.removeCallbacks(tickTask);
            listener.onLost();
            listener.onSignalLost();
            laneView.clearImage();
            signalView.clearImage();
        } else if (!running) {
            running = true;
            main.post(tickTask);
        }
    }

    private void tick() {
        if (!running) return;
        // An incomplete copy must not leave old signal/color/countdown on the HUD.
        if (busy && SystemClock.elapsedRealtime() - requestAt > 1500) {
            listener.onLost();
            listener.onSignalLost();
        }
        if (!busy) capture();
        main.postDelayed(tickTask, TICK_MS);
    }

    private void capture() {
        Activity a = activity;
        Window window = a == null || a.isFinishing() || a.isDestroyed() ? null : a.getWindow();
        View decor = window == null ? null : window.peekDecorView();
        if (decor == null || !decor.isAttachedToWindow() || decor.getWidth() < 2 || decor.getHeight() < 2) {
            listener.onLost();
            listener.onSignalLost();
            return;
        }
        int dw = decor.getWidth(), dh = decor.getHeight();
        int w = Math.min(1080, dw), h = Math.max(2, Math.round(dh * (w / (float) dw)));
        final long token = generation;
        final int count = lanes;
        final boolean wantSignal = signal;
        Bitmap frame = null;
        try {
            List<NaverScreenSemantics.Entry> entries = NaverScreenSemantics.read(decor);
            frame = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
            final Bitmap source = frame;
            busy = true;
            requestAt = SystemClock.elapsedRealtime();
            final long copiedAt = requestAt;
            PixelCopy.request(window, source, result -> {
                Bitmap lane = null, lamp = null;
                Bitmap small = null;
                try {
                    if (result == PixelCopy.SUCCESS) {
                        int sw = Math.max(1, w / 2), sh = Math.max(1, h / 2);
                        small = Bitmap.createScaledBitmap(source, sw, sh, true);
                        int[] px = new int[sw * sh];
                        small.getPixels(px, 0, sw, 0, 0, sw, sh);
                        int[] box = count <= 0 ? null : LaneBoxFinder.find(px, sw, sh, count);
                        int[] fullBox = scale(box, w / (float) sw, h / (float) sh);
                        int[] phoneBox = scale(fullBox, dw / (float) w, dh / (float) h);
                        int[] distance = NaverScreenSemantics.distance(entries, phoneBox, dw, dh);
                        int[] label = scale(distance, w / (float) dw, h / (float) dh);
                        lane = crop(source, ScreenGuidanceFinder.laneWithDistance(fullBox, label, w, h, count));
                        if (wantSignal) {
                            int[] semantic = NaverScreenSemantics.signal(entries, dw, dh);
                            int[] housing = scale(ScreenGuidanceFinder.signal(px, sw, sh), w / (float) sw, h / (float) sh);
                            int[] region = scale(semantic, w / (float) dw, h / (float) dh);
                            if (region == null) region = housing;
                            else if (housing != null && nearby(region, housing)) region = union(region, housing);
                            if (region != null) {
                                int[] counter = NaverScreenSemantics.counter(entries,
                                        scale(region, dw / (float) w, dh / (float) h), dw, dh);
                                if (counter != null) {
                                    int[] number = scale(counter, w / (float) dw, h / (float) dh);
                                    region = union(region, number);
                                } else if (semantic == null) {
                                    // Original countdown may sit outside the dark lamp housing.
                                    region[2] += (region[3] - region[1] + 1) * 2;
                                }
                                lamp = crop(source, ScreenGuidanceFinder.pad(region, 3, w, h));
                            }
                        }
                    }
                } catch (Throwable error) {
                    if (lane != null) lane.recycle();
                    if (lamp != null) lamp.recycle();
                    lane = null; lamp = null;
                    NaverHudLog.ex("guidance screen regions", error);
                } finally {
                    if (small != null && small != source) small.recycle();
                    source.recycle();
                }
                final Bitmap laneCrop = lane, signalCrop = lamp;
                main.post(() -> {
                    busy = false;
                    if (!running || token != generation) {
                        if (laneCrop != null) laneCrop.recycle();
                        if (signalCrop != null) signalCrop.recycle();
                        return;
                    }
                    if (SystemClock.elapsedRealtime() - copiedAt > 1500) {
                        if (laneCrop != null) laneCrop.recycle();
                        if (signalCrop != null) signalCrop.recycle();
                        listener.onLost(); listener.onSignalLost();
                        return;
                    }
                    if (laneCrop == null) {
                        listener.onLost(); laneView.clearImage();
                    } else {
                        laneView.setImage(laneCrop);
                        listener.onCaptured(laneView);
                        if (!loggedLane) {
                            loggedLane = true;
                            NaverHudLog.line("lane + distance: original screen crop " + laneCrop.getWidth() + "x" + laneCrop.getHeight());
                        }
                    }
                    if (signalCrop == null) {
                        listener.onSignalLost(); signalView.clearImage();
                    } else {
                        signalView.setImage(signalCrop);
                        listener.onSignalCaptured(signalView);
                        if (!loggedSignal) {
                            loggedSignal = true;
                            NaverHudLog.line("traffic signal: original screen crop " + signalCrop.getWidth() + "x" + signalCrop.getHeight());
                        }
                    }
                });
            }, worker);
        } catch (Throwable error) {
            busy = false;
            if (frame != null) frame.recycle();
            listener.onLost(); listener.onSignalLost();
            if (!loggedError) { loggedError = true; NaverHudLog.ex("guidance screen copy", error); }
        }
    }

    private static int[] scale(int[] r, float sx, float sy) {
        if (r == null) return null;
        return new int[]{(int) Math.floor(r[0] * sx), (int) Math.floor(r[1] * sy),
                (int) Math.ceil((r[2] + 1) * sx) - 1, (int) Math.ceil((r[3] + 1) * sy) - 1};
    }

    private static int[] union(int[] a, int[] b) {
        return new int[]{Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])};
    }

    private static boolean nearby(int[] a, int[] b) {
        int dx = Math.max(0, Math.max(a[0] - b[2], b[0] - a[2]));
        int dy = Math.max(0, Math.max(a[1] - b[3], b[1] - a[3]));
        return Math.hypot(dx, dy) < (b[3] - b[1] + 1) * 2;
    }

    private static Bitmap crop(Bitmap source, int[] r) {
        if (r == null) return null;
        r = ScreenGuidanceFinder.pad(r, 0, source.getWidth(), source.getHeight());
        if (r[2] <= r[0] || r[3] <= r[1]) return null;
        // Always an independent bitmap: source is recycled when this request completes.
        Bitmap out = Bitmap.createBitmap(r[2] - r[0] + 1, r[3] - r[1] + 1, Bitmap.Config.ARGB_8888);
        new Canvas(out).drawBitmap(source, new Rect(r[0], r[1], r[2] + 1, r[3] + 1),
                new Rect(0, 0, out.getWidth(), out.getHeight()), null);
        return out;
    }

    static final class ImageView extends View {
        private final Paint paint = new Paint(Paint.FILTER_BITMAP_FLAG);
        private Bitmap image;
        ImageView(Context context) { super(context); }
        void clearImage() { if (image != null) image.recycle(); image = null; }
        void setImage(Bitmap b) {
            clearImage(); image = b;
            measure(MeasureSpec.makeMeasureSpec(b.getWidth(), MeasureSpec.EXACTLY),
                    MeasureSpec.makeMeasureSpec(b.getHeight(), MeasureSpec.EXACTLY));
            layout(0, 0, b.getWidth(), b.getHeight());
            invalidate();
        }
        @Override protected void onDraw(Canvas canvas) {
            if (image != null && !image.isRecycled()) canvas.drawBitmap(image, 0, 0, paint);
        }
    }
}
