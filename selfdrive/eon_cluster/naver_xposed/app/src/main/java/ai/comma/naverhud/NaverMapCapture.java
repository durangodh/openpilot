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
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/** Reads Naver's own rendered map via NaverMap.takeSnapshot; never captures a screen. */
final class NaverMapCapture {
    // 12.3인치 HUD 6:4 지도칸(760x720)과 1:1. 더 넓은 범위가 같은 글자 크기로 보인다.
    private static final int WIDTH = 760, HEIGHT = 720, JPEG_QUALITY = 65;
    // 스냅샷 요청 간격(5fps = EON 지도 FPS 최대값). 이전 응답이 오면 바로 다음
    // 요청이 가능하도록 짧은 주기로 확인한다.
    private static final long FRAME_INTERVAL_MS = 200, CHECK_INTERVAL_MS = 40;
    // 응답이 안 오는 요청을 포기하는 시간. 길면 그동안 지도가 멈춰 보인다.
    private static final long REQUEST_TIMEOUT_MS = 600;

    private final NaverNaviClient client;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService encoder = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(() -> {
            // JPEG 인코딩은 HUD 앱(같은 S9)보다 조금만 뒤로. BACKGROUND 는 안드로이드가
            // CPU 를 크게 제한하는 그룹에 넣어, S9 이 바쁠 때 인코딩이 수백 ms 씩 밀렸다.
            android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_LESS_FAVORABLE);
            r.run();
        }, "naver-hud-map-encode");
        t.setDaemon(true);
        return t;
    });
    private final ScheduledExecutorService ticker = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "naver-hud-map-tick");
        t.setDaemon(true);
        return t;
    });
    private final AtomicBoolean posted = new AtomicBoolean();
    // 인코딩 대기는 최신 한 장만 둔다. 예전에는 인코더 큐가 무제한이라 S9 이 바쁘면
    // (HUD 그리기·USB 전송) 200ms 마다 쌓인 프레임을 차례로 인코딩하느라 HUD 지도가
    // 점점 늦어지고(밀림) 한꺼번에 몰려 갔다.
    private final AtomicReference<Frame> pendingFrame = new AtomicReference<>();
    private final AtomicBoolean encodeScheduled = new AtomicBoolean();

    private static final class Frame {
        final Bitmap image; final long gen; final long snapAt;
        Frame(Bitmap image, long gen, long snapAt) { this.image = image; this.gen = gen; this.snapAt = snapAt; }
    }

    // 10초마다 naver_hud.log 에 남기는 지도 경로 통계(멈춤·밀림 원인 확인용).
    private static final long STATS_INTERVAL_MS = 10000;
    private long statsAt, statReq, statOk, statTimeout, statDropped, statSent;
    private long statSnapMsSum, statSnapMsMax, statEncMsSum, statEncMsMax, statAgeMsMax;
    private final AtomicBoolean started = new AtomicBoolean();
    private final List<WeakReference<Activity>> activities = new ArrayList<>();
    private volatile Object provider;
    private volatile Object store;   // NaviStore
    private Object activeMap;
    // generation: 지도(NaverMap 객체)가 바뀔 때만 올린다. 이전 지도의 프레임만 버린다.
    // requestId  : 스냅샷 요청마다 올린다. 요청 중복 판단용이며 프레임 폐기 기준이 아니다.
    // 예전에는 한 번호로 둘 다 처리해서, 인코딩이 다음 요청(200ms)보다 늦으면
    // 멀쩡한 프레임까지 버려져 지도가 계속 안 나올 수 있었다.
    private volatile long generation;
    private long requestedAt, lastRequestAt, lastFrameAt, lastStatusAt, requestId;
    private long sent;

    NaverMapCapture(NaverNaviClient client) { this.client = client; }

    void setMapProvider(Object value) { provider = value; }

    void setStore(Object value) { store = value; }

    void addActivity(Activity activity) {
        if (activity == null) return;
        synchronized (activities) {
            activities.removeIf(ref -> ref.get() == null || ref.get() == activity);
            activities.add(new WeakReference<>(activity));
        }
    }

    void start() {
        if (started.compareAndSet(false, true)) {
            ticker.scheduleWithFixedDelay(this::capture, 0, CHECK_INTERVAL_MS, TimeUnit.MILLISECONDS);
        }
    }

    private void capture() {
        if (!client.ready()) return;
        // 메인 스레드가 바쁠 때 요청이 쌓이지 않게 한 번에 하나만 올린다.
        if (!posted.compareAndSet(false, true)) return;
        main.post(() -> {
            try {
                captureOnMain();
            } finally {
                posted.set(false);
            }
        });
    }

    private void captureOnMain() {
        Object map = chooseMap();
        long now = SystemClock.elapsedRealtime();
        if (map != activeMap) {
            activeMap = map;
            generation++;
            requestedAt = 0;
            lastFrameAt = 0;
            // 야간 지도 확인용: Android Auto 지도(car)는 앱의 야간 설정이 아니라
            // 차량 주/야를 따를 수 있다. 화면 지도(phone)는 보이는 그대로다.
            if (map != null) NaverHudLog.xposed("snapshot map selected: " + mapSource
                    + " " + map.getClass().getName());
        }
        if (map == null) {
            if (now - lastStatusAt > 10000) {
                lastStatusAt = now;
                NaverHudLog.status("waiting for NaverMap");
            }
            return;
        }
        logStats(now);
        if (requestedAt != 0 && now - requestedAt < REQUEST_TIMEOUT_MS) return;
        if (requestedAt != 0) statTimeout++;   // 응답 없이 시간 초과
        if (now - lastRequestAt < FRAME_INTERVAL_MS) return;
        requestedAt = now;
        lastRequestAt = now;
        statReq++;
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
        long now = SystemClock.elapsedRealtime();
        if (req == requestId) {
            if (requestedAt != 0) {
                long ms = now - requestedAt;
                statOk++;
                statSnapMsSum += ms;
                statSnapMsMax = Math.max(statSnapMsMax, ms);
            }
            requestedAt = 0;
        }
        if (gen != generation) return;
        lastFrameAt = now;
        try {
            Frame old = pendingFrame.getAndSet(new Frame(fitCenterCrop(source), gen, now));
            if (old != null) {   // 아직 인코딩 못 한 옛 프레임은 버린다(최신만 보낸다)
                old.image.recycle();
                statDropped++;
            }
            if (encodeScheduled.compareAndSet(false, true)) encoder.execute(this::drainEncode);
        } catch (Throwable error) {
            NaverHudLog.ex("snapshot crop", error);
        }
    }

    private void drainEncode() {
        try {
            Frame frame;
            while ((frame = pendingFrame.getAndSet(null)) != null) encode(frame);
        } finally {
            encodeScheduled.set(false);
            if (pendingFrame.get() != null && encodeScheduled.compareAndSet(false, true)) {
                encoder.execute(this::drainEncode);
            }
        }
    }

    private void encode(Frame frame) {
        try {
            // 지도가 바뀐 경우만 버린다. 새 요청이 나갔다고 버리지 않는다.
            if (frame.gen != generation) return;
            long start = SystemClock.elapsedRealtime();
            ByteArrayOutputStream bytes = new ByteArrayOutputStream(100000);
            frame.image.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, bytes);
            if (frame.gen == generation) {
                client.sendMap(bytes.toByteArray());
                long end = SystemClock.elapsedRealtime();
                statEncMsSum += end - start;
                statEncMsMax = Math.max(statEncMsMax, end - start);
                statAgeMsMax = Math.max(statAgeMsMax, end - frame.snapAt);
                statSent++;
                if (++sent == 1) NaverHudLog.line("first direct map frame sent");
            }
        } catch (Throwable error) {
            NaverHudLog.ex("snapshot encode", error);
        } finally {
            frame.image.recycle();
        }
    }

    /** 메인 스레드에서 호출. 인코더 통계 필드는 다른 스레드가 쓰지만 대략값이면 충분하다. */
    private void logStats(long now) {
        if (statsAt == 0) { statsAt = now; return; }
        if (now - statsAt < STATS_INTERVAL_MS) return;
        long sentNow = statSent;
        NaverHudLog.line(String.format(java.util.Locale.US,
                "map stats %ds: req %d ok %d timeout %d dropped %d sent %d | snapshot avg %dms max %dms"
                        + " | encode avg %dms max %dms | frame age max %dms | src %s",
                (now - statsAt) / 1000, statReq, statOk, statTimeout, statDropped, sentNow,
                statOk > 0 ? statSnapMsSum / statOk : 0, statSnapMsMax,
                sentNow > 0 ? statEncMsSum / sentNow : 0, statEncMsMax, statAgeMsMax, mapSource));
        statsAt = now;
        statReq = statOk = statTimeout = statDropped = statSent = 0;
        statSnapMsSum = statSnapMsMax = statEncMsSum = statEncMsMax = statAgeMsMax = 0;
    }

    private String mapSource = "";

    private Object chooseMap() {
        // 안내가 실제로 그려지는 지도(NaviStore 의 NaverNaviUI 지도). 폰 화면이든
        // Android Auto 화면이든 이 지도가 차량을 따라간다. MapProvider 의 차량 지도는
        // 안내에 안 쓰이면 SDK 기본 카메라(서울시청)에 멈춰 있을 수 있다.
        Object guided = NaverReflect.fieldOfType(
                NaverReflect.fieldOfType(store, NaverReflect.NAVI_UI), NaverReflect.NAVER_MAP);
        if (guided != null) {
            mapSource = "guidance";
            return guided;
        }
        // 안내 전에는 보이는 화면 지도를 먼저 쓴다. MapProvider 의 차량 지도는 안드로이드
        // 오토 화면에 안 쓰이면 SDK 기본 카메라(서울시청)에 멈춰 있다.
        Object phone = visiblePhoneMap();
        if (phone != null) {
            mapSource = "phone";
            return phone;
        }
        Object car = call(provider, "i");
        if (!isNaverMap(car)) car = firstNaverMapGetter(provider);
        if (car != null) {
            mapSource = "car";
            return car;
        }
        return null;
    }

    private Object visiblePhoneMap() {
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
