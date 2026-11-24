package ai.comma.naverhud;

import android.app.Activity;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Rect;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.os.SystemClock;
import android.view.PixelCopy;
import android.view.View;
import android.view.Window;

import java.util.Locale;

/**
 * 네이버 6.12: 화면에 그려진 원본 차로 상자를 그대로 잘라 HUD 로 보낸다(2026-11-22 요청 "네이버지도
 * 원본 그대로"). 6.12 는 차로 표시를 Compose 로 그려 떼어 올 View 가 없으므로, 네이버 창을
 * PixelCopy 로 떠서(창 자신의 그림이라 위에 덮인 다른 앱과 상관없다) LaneBoxFinder 로 상자를 찾는다.
 *
 * 500ms 마다: 상자 위치를 모르거나 차로 수가 바뀌었거나 3초가 지났으면 창 전체를 절반 크기로 떠서
 * 찾고, 위치를 알면 그 부분만 원래 해상도로 떠 온다. 남은 거리 글자는 상자 오른쪽에 직접 붙인다
 * (폰 글자는 지도 위에 있어 같이 자르면 지도가 섞인다).
 *
 * 1.5초 넘게 상자를 못 뜨면 lost 를 불러 예전처럼 직접 그린 차로 띠(NaverLaneStrip)로 돌아간다.
 */
final class NaverLaneCapture {
    interface Listener {
        /** 메인 스레드: 잘라 온 그림이 처음 준비됐다(이후 view 를 보내면 된다). */
        void onCaptured(View view);

        /** 메인 스레드: 한동안 못 떴다. 직접 그린 띠로 돌아간다. */
        void onLost();
    }

    private static final long TICK_MS = 500;
    private static final long SEARCH_EVERY_MS = 3000;
    // 2026-11-23: the strip came and went. One missed search or one odd crop (an animation, a
    // map label passing behind) no longer drops the box; the last good crop stays up to STALE_MS.
    private static final long STALE_MS = 3000;
    private int cropMisses;
    private int winW, winH;
    // 2026-11-24: the strip still shows about half the time; where it is lost was never measured.
    // Counters written to naver_hud.log every 30 s while lanes are shown ("lane capture stats").
    private int stSearch, stFound, stNotFound, stCopyFail, stCropOk, stCropBad, stLostStale, stLostZero, stNoWindow;
    private long stSince, stLiveMs, stLastTick;
    private static final long ZERO_GRACE_MS = 2000;
    private long zeroSince;
    // Real screens to tune the finder with (2026-11-22): the half-size window copy with the found
    // box (green) and Naver's distance labels (yellow), once a minute while lanes are shown.
    private static final long DUMP_EVERY_MS = 60_000;
    // Off once the capture worked on the car (2026-11-23): writing a PNG each minute was only for tuning.
    private static final boolean DUMP_ENABLED = false;
    private long dumpedAt = -DUMP_EVERY_MS;
    private final java.io.File dumpDir;

    private final Handler main = new Handler(Looper.getMainLooper());
    private final Handler worker;
    private final Listener listener;
    private final ImageView view;
    private Activity activity;
    private int lanes;
    private String label = "";
    private boolean running, live, busy, loggedFound, loggedMiss, loggedError;
    private Rect box;            // 창 좌표(원래 해상도): 상자 + 네이버 거리 글자
    private volatile Rect boxPart;   // box 안에서 차로 상자 부분(crop 좌표)
    private int boxLanes;
    private long searchedAt, capturedAt = -1;
    private int misses;

    NaverLaneCapture(Context context, Listener listener) {
        this.listener = listener;
        this.view = new ImageView(context);
        java.io.File dir = null;
        try {
            dir = context.getExternalFilesDir(null);
        } catch (Throwable ignored) {
            // no dump
        }
        dumpDir = dir;
        HandlerThread t = new HandlerThread("naver-lane-capture");
        t.start();
        worker = new Handler(t.getLooper());
    }

    /** 메인 스레드: 차로 항목이 바뀔 때. n=0 이면 멈춘다. */
    void update(Activity activity, int n, double meters) {
        if (activity != null) this.activity = activity;
        // A lane item that drops to no lanes for a moment (between Naver's updates) does not end
        // the capture: it keeps its last lane count and stops only after ZERO_GRACE_MS of none
        // (the crop check still drops the box if Naver's own box really went away).
        if (n <= 0 || activity == null) {
            if (running && zeroSince == 0) zeroSince = SystemClock.elapsedRealtime();
            if (!running) live = false;
            return;
        }
        zeroSince = 0;
        this.lanes = n;
        if (!running) {
            running = true;
            capturedAt = SystemClock.elapsedRealtime();   // 첫 그림까지 STALE_MS 기다린다
            main.post(this::tick);
        }
    }

    private void stats(long now) {
        if (stLastTick != 0 && live) stLiveMs += now - stLastTick;
        stLastTick = now;
        if (stSince == 0) stSince = now;
        if (now - stSince < 30_000) return;
        Activity a = activity;
        NaverHudLog.line(String.format(Locale.US, "lane capture stats %ds: shown %d%% | search %d found %d none %d copyfail %d"
                        + " | crop ok %d bad %d | lost stale %d zero %d | nowindow %d | lanes %d activity %s",
                (now - stSince) / 1000, 100 * stLiveMs / Math.max(1, now - stSince), stSearch, stFound, stNotFound,
                stCopyFail, stCropOk, stCropBad, stLostStale, stLostZero, stNoWindow, lanes,
                a == null ? "none" : a.getClass().getSimpleName()));
        stSearch = stFound = stNotFound = stCopyFail = stCropOk = stCropBad = stLostStale = stLostZero = stNoWindow = 0;
        stLiveMs = 0;
        stSince = now;
    }

    /** 잘라 온 그림을 지금 쓰는 중이면 true. */
    boolean live() {
        return live;
    }

    private void tick() {
        if (!running) return;
        long now = SystemClock.elapsedRealtime();
        stats(now);
        if (zeroSince != 0 && now - zeroSince > ZERO_GRACE_MS) {
            running = false;
            zeroSince = 0;
            if (live) {
                live = false;
                stLostZero++;
                listener.onLost();
            }
            return;
        }
        if (live && now - capturedAt > STALE_MS) {
            live = false;
            stLostStale++;
            listener.onLost();
        }
        if (!busy) capture(now);
        main.postDelayed(this::tick, TICK_MS);
    }

    private void capture(long now) {
        Activity a = activity;
        Window window = a == null || a.isFinishing() ? null : a.getWindow();
        View decor = window == null ? null : window.peekDecorView();
        if (decor == null || decor.getWidth() <= 0 || decor.getHeight() <= 0 || !decor.isAttachedToWindow()) {
            stNoWindow++;
            return;
        }
        try {
            final int n = lanes;
            // Searched again every SEARCH_EVERY_MS as before 2026-11-23: Naver shifts the box with the
            // distance text width (59m -> 100m), and searching only on a lost box (the heat change)
            // left the crop on the old place, so the strip showed about half the time.
            if (box == null || boxLanes != n || now - searchedAt > SEARCH_EVERY_MS) {
                searchedAt = now;
                stSearch++;
                final int sw = decor.getWidth() / 2, sh = decor.getHeight() / 2;
                // Naver's own distance label bounds, scaled to the half-size copy.
                final java.util.List<int[]> anchors = new java.util.ArrayList<>();
                try {
                    for (int[] r : NaverScreenSemantics.distances(NaverScreenSemantics.read(decor), decor.getWidth(), decor.getHeight())) {
                        anchors.add(new int[]{r[0] / 2, r[1] / 2, r[2] / 2, r[3] / 2});
                    }
                } catch (Throwable ignored) {
                    // pixels alone
                }
                final boolean dump = DUMP_ENABLED && now - dumpedAt > DUMP_EVERY_MS;
                if (dump) dumpedAt = now;
                final Bitmap small = Bitmap.createBitmap(sw, sh, Bitmap.Config.ARGB_8888);
                busy = true;
                PixelCopy.request(window, small, result -> {
                    Rect found = null, part = null;
                    if (result == PixelCopy.SUCCESS) {
                        int[] px = new int[sw * sh];
                        small.getPixels(px, 0, sw, 0, 0, sw, sh);
                        int[] r = LaneBoxFinder.find(px, sw, sh, n, anchors);
                        if (dump) dumpCapture(small, r, anchors, n);
                        int[] lbl = LaneBoxFinder.label(r, anchors);
                        int[] boxOnly = r;
                        if (r != null && lbl != null) {
                            // Naver's own distance text goes along with the box (original pixels).
                            r = new int[]{Math.min(r[0], lbl[0]), Math.min(r[1], lbl[1]), Math.max(r[2], lbl[2]), Math.max(r[3], lbl[3])};
                        }
                        if (r != null) {
                            found = new Rect(Math.max(0, r[0] * 2 - 4), Math.max(0, r[1] * 2 - 3),
                                    Math.min(sw * 2, r[2] * 2 + 6), Math.min(sh * 2, r[3] * 2 + 5));
                            // the box inside the crop, for the stillBox check (the label is not dark)
                            part = new Rect(Math.max(0, boxOnly[0] * 2 - found.left), Math.max(0, boxOnly[1] * 2 - found.top),
                                    Math.min(found.width(), boxOnly[2] * 2 + 2 - found.left),
                                    Math.min(found.height(), boxOnly[3] * 2 + 2 - found.top));
                        }
                    }
                    if (!dump || result != PixelCopy.SUCCESS) small.recycle();
                    final Rect f = found, fp = part;
                    final int res = result;
                    main.post(() -> {
                        busy = false;
                        boxPart = fp;
                        onSearched(f, n, res, sw * 2, sh * 2);
                    });
                }, worker);
            } else {
                winW = decor.getWidth();
                winH = decor.getHeight();
                cropBox(window, box);
            }
        } catch (Throwable error) {
            busy = false;
            if (!loggedError) {
                loggedError = true;
                NaverHudLog.ex("lane capture", error);
            }
        }
    }

    /** Worker thread: writes lane_capture_<n>.png (last 5 kept) and recycles the bitmap. */
    private void dumpCapture(Bitmap small, int[] box, java.util.List<int[]> anchors, int lanes) {
        try {
            if (dumpDir == null) return;
            Bitmap out = small.copy(Bitmap.Config.ARGB_8888, true);
            Canvas c = new Canvas(out);
            Paint p = new Paint();
            p.setStyle(Paint.Style.STROKE);
            p.setStrokeWidth(2f);
            p.setColor(Color.YELLOW);
            for (int[] a : anchors) c.drawRect(a[0], a[1], a[2], a[3], p);
            if (box != null) {
                p.setColor(Color.GREEN);
                c.drawRect(box[0], box[1], box[2], box[3], p);
            }
            int slot = (int) ((System.currentTimeMillis() / 60000) % 5);
            java.io.File f = new java.io.File(dumpDir, "lane_capture_" + slot + ".png");
            try (java.io.FileOutputStream os = new java.io.FileOutputStream(f)) {
                out.compress(Bitmap.CompressFormat.PNG, 100, os);
            }
            out.recycle();
            NaverHudLog.line("lane capture dump " + f.getName() + " lanes=" + lanes + " box="
                    + (box == null ? "none" : box[0] + "," + box[1] + "," + box[2] + "," + box[3])
                    + " labels=" + anchors.size());
        } catch (Throwable error) {
            NaverHudLog.ex("lane capture dump", error);
        } finally {
            small.recycle();
        }
    }

    private void onSearched(Rect found, int n, int result, int w, int h) {
        if (result != PixelCopy.SUCCESS) stCopyFail++;
        else if (found == null) stNotFound++;
        else stFound++;
        if (found == null && box != null && boxLanes == n) {
            // periodic re-search missed, but the known box still crops fine: keep it
            Activity a = activity;
            if (running && a != null && !a.isFinishing() && a.getWindow() != null) cropBox(a.getWindow(), box);
            return;
        }
        if (found == null) {
            box = null;
            misses++;
            if (!loggedMiss || misses % 120 == 0) {
                loggedMiss = true;
                NaverHudLog.line("lane capture: box not found (lanes=" + n + " copy=" + result + " window=" + w + "x" + h
                        + " misses=" + misses + ")");
            }
            return;
        }
        if (!loggedFound || box == null || boxLanes != n) {
            NaverHudLog.line("lane capture: box " + found.toShortString() + " lanes=" + n + " window=" + w + "x" + h);
            loggedFound = true;
        }
        box = found;
        boxLanes = n;
        Activity a = activity;
        if (running && a != null && !a.isFinishing() && a.getWindow() != null) cropBox(a.getWindow(), found);
    }

    /**
     * Copies the known crop plus a margin around it and looks for the lane box again inside that
     * small area at full resolution (2026-11-24): Naver slides the box sideways and up/down with the
     * distance text and the lane count, and a crop fixed to the old place kept failing the check.
     * Found: the crop follows the box (the distance label keeps its offset). Not found there: the
     * old place is checked as before.
     */
    private void cropBox(Window window, final Rect r) {
        final int lanesNow = lanes;
        final Rect bp0 = boxPart;
        int mx = Math.max(16, r.width() / 3), my = Math.max(12, r.height() / 2);
        int W = winW > 0 ? winW : r.right + mx, H = winH > 0 ? winH : r.bottom + my;
        final Rect area = new Rect(Math.max(0, r.left - mx), Math.max(0, r.top - my),
                Math.min(W, r.right + mx), Math.min(H, r.bottom + my));
        final Bitmap wide = Bitmap.createBitmap(area.width(), area.height(), Bitmap.Config.ARGB_8888);
        busy = true;
        PixelCopy.request(window, area, wide, result -> {
            boolean ok = false;
            Rect moved = null;
            Bitmap crop = null;
            if (result == PixelCopy.SUCCESS) {
                int aw = wide.getWidth(), ah = wide.getHeight();
                int[] px = new int[aw * ah];
                wide.getPixels(px, 0, aw, 0, 0, aw, ah);
                int[] f = lanesNow > 0 ? LaneBoxFinder.find(px, aw, ah, lanesNow) : null;
                Rect target = new Rect(r);   // window coordinates of what we cut
                if (f != null && bp0 != null) {
                    // keep the label's offset: the crop's box part moves to the box just found
                    int left = area.left + f[0] - bp0.left, top = area.top + f[1] - bp0.top;
                    target = new Rect(left, top, left + r.width(), top + r.height());
                    ok = true;
                } else if (f != null) {
                    target = new Rect(area.left + f[0] - 2, area.top + f[1] - 2, area.left + f[2] + 3, area.top + f[3] + 3);
                    ok = true;
                } else if (bp0 != null) {
                    int bl = r.left - area.left + bp0.left, bt = r.top - area.top + bp0.top;
                    if (bl >= 0 && bt >= 0 && bl + bp0.width() <= aw && bt + bp0.height() <= ah && bp0.width() >= 8 && bp0.height() >= 8) {
                        int[] sub = new int[bp0.width() * bp0.height()];
                        wide.getPixels(sub, 0, bp0.width(), bl, bt, bp0.width(), bp0.height());
                        ok = LaneBoxFinder.stillBox(sub, bp0.width(), bp0.height());
                    }
                }
                if (ok) {
                    Rect in = new Rect(target.left - area.left, target.top - area.top,
                            target.right - area.left, target.bottom - area.top);
                    if (in.intersect(0, 0, aw, ah) && in.width() >= 8 && in.height() >= 8) {
                        crop = Bitmap.createBitmap(wide, in.left, in.top, in.width(), in.height());
                        moved = new Rect(in.left + area.left, in.top + area.top, in.right + area.left, in.bottom + area.top);
                    } else {
                        ok = false;
                    }
                }
            }
            wide.recycle();
            final boolean good = ok;
            final Bitmap cropF = crop;
            final Rect movedF = moved;
            main.post(() -> {
                busy = false;
                if (good && movedF != null) box = movedF;   // follow the box
                final Bitmap crop2 = cropF;
                if (!good || crop2 == null) {
                    stCropBad++;
                    if (crop2 != null) crop2.recycle();
                    if (++cropMisses >= 2) {
                        box = null;      // 두 번 연속 상자가 아니면 다음 틱에 다시 찾는다
                        cropMisses = 0;
                    }
                    return;
                }
                cropMisses = 0;
                stCropOk++;
                capturedAt = SystemClock.elapsedRealtime();
                view.setImage(crop2);
                if (!live && running) {
                    live = true;
                    listener.onCaptured(view);
                }
            });
        }, worker);
    }

    /** 잘라 온 상자 + 오른쪽 거리 글자. NaverViewImage 가 500ms 마다 이 View 를 그려 보낸다. */
    static final class ImageView extends View {
        private static final int TEXT_GAP_FRAC = 5;   // 상자 높이의 1/5
        private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint edge = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint bmpPaint = new Paint(Paint.FILTER_BITMAP_FLAG);
        private Bitmap image;
        private String label = "";

        ImageView(Context context) {
            super(context);
            text.setColor(Color.WHITE);
            text.setFakeBoldText(true);
            edge.setColor(Color.argb(230, 0, 0, 0));
            edge.setFakeBoldText(true);
            edge.setStyle(Paint.Style.STROKE);
            edge.setStrokeJoin(Paint.Join.ROUND);
        }

        void setLabel(String s) {
            // Only Naver's own pixels: the distance comes with the crop when Naver's label is found.
        }

        void setImage(Bitmap b) {
            Bitmap old = image;
            image = b;
            if (old != null && old != b) old.recycle();
            relayout();
        }

        private void relayout() {
            if (image == null) return;
            int h = image.getHeight();
            text.setTextSize(h * 0.53f);
            edge.setTextSize(h * 0.53f);
            edge.setStrokeWidth(Math.max(2f, h * 0.09f));
            int w = image.getWidth();
            if (!label.isEmpty()) w += h / TEXT_GAP_FRAC + (int) Math.ceil(text.measureText(label)) + 4;
            measure(MeasureSpec.makeMeasureSpec(w, MeasureSpec.EXACTLY), MeasureSpec.makeMeasureSpec(h, MeasureSpec.EXACTLY));
            layout(0, 0, w, h);
            invalidate();
        }

        @Override protected void onDraw(Canvas c) {
            Bitmap b = image;
            if (b == null || b.isRecycled()) return;
            c.drawBitmap(b, 0, 0, bmpPaint);
            if (label.isEmpty()) return;
            int h = b.getHeight();
            Paint.FontMetrics fm = text.getFontMetrics();
            float y = h / 2f - (fm.ascent + fm.descent) / 2f;
            float x = b.getWidth() + h / (float) TEXT_GAP_FRAC;
            c.drawText(label, x, y, edge);
            c.drawText(label, x, y, text);
        }
    }
}
