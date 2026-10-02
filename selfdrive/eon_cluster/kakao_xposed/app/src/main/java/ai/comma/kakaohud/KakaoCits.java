package ai.comma.kakaohud;

import android.os.SystemClock;

import java.lang.ref.WeakReference;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * 카카오 폰 신호등 표시(KNUCitsViewModel.getCitsUIState)를 그대로 HUD 신호등 그림으로 보낸다.
 *
 * 출처(4.51.1 디컴파일): KNUCitsUseCase 가 300m 안의 첫 신호에서 좌회전·직진 신호를 합쳐
 * KNUCits 를 만들고, 뷰모델이 1초마다 잔여초를 줄인다(0 이 되면 Empty). 상태가 Data
 * (navi.drive.core.feature.cits.c$a, 필드 a = KNUCits)이면 폰에 신호등이 떠 있다.
 * KNUCits: f = remainTime(초), g = remainTimeType(잔여초 색 Default/Red/Yellow/Green),
 * a = signalLeftType(앞 신호 색).
 *
 * 폰과 같은 신호·같은 잔여초를 쓰도록, 이 상태를 읽는 동안에는 KakaoBridge 의 자체 선택
 * (onCitsGuide)이 그림을 건드리지 않는다. EON 용 traffic_signal JSON 은 그대로 둔다.
 */
final class KakaoCits {
    static final String VIEW_MODEL = "com.kakaomobility.navi.drive.core.feature.cits.KNUCitsViewModel";
    private static final String DATA_STATE = "com.kakaomobility.navi.drive.core.feature.cits.c$a";
    private static final long POLL_MS = 250;

    private final KakaoSignal signal;
    private final ScheduledExecutorService poller = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "kakao-hud-cits");
        t.setDaemon(true);
        return t;
    });
    private volatile WeakReference<Object> viewModel;
    private volatile long readAt;
    private boolean started, loggedFirst, loggedError;

    KakaoCits(KakaoSignal signal) {
        this.signal = signal;
    }

    /** 뷰모델을 읽고 있는 동안(최근 2초) true. 이때는 그림을 여기서만 그린다. */
    boolean active() {
        return readAt != 0 && SystemClock.elapsedRealtime() - readAt < 2000;
    }

    synchronized void setViewModel(Object vm) {
        viewModel = new WeakReference<>(vm);
        if (!started) {
            started = true;
            poller.scheduleWithFixedDelay(this::tick, POLL_MS, POLL_MS, TimeUnit.MILLISECONDS);
            KakaoHudLog.line("cits view model captured");
        }
    }

    private void tick() {
        try {
            WeakReference<Object> ref = viewModel;
            Object vm = ref == null ? null : ref.get();
            if (vm == null) return;
            Object state = call(call(vm, "getCitsUIState"), "getValue");
            readAt = SystemClock.elapsedRealtime();
            if (state == null || !DATA_STATE.equals(state.getClass().getName())) {
                signal.clear();
                return;
            }
            Object cits = field(state, "a");
            int remain = ((Number) field(cits, "f")).intValue();
            int color = color(field(cits, "g"));
            if (color == 0) color = color(field(cits, "a"));
            if (color == 0 || remain <= 0) {
                signal.clear();
                return;
            }
            signal.publish(color, remain);
            if (!loggedFirst) {
                loggedFirst = true;
                KakaoHudLog.xposed("cits from phone view: color=" + color + " remain=" + remain);
            }
        } catch (Throwable t) {
            readAt = 0;
            if (!loggedError) {
                loggedError = true;
                KakaoHudLog.ex("cits view model", t);
            }
        }
    }

    /** KNUCitsSignalColor → 1 빨강 / 2 노랑 / 3 초록 / 0 Default. */
    static int color(Object value) {
        String name = value instanceof Enum ? ((Enum<?>) value).name() : String.valueOf(value);
        switch (name) {
            case "Red": return 1;
            case "Yellow": return 2;
            case "Green": return 3;
            default: return 0;
        }
    }

    private static Object call(Object target, String name) throws Exception {
        if (target == null) return null;
        Method m = target.getClass().getMethod(name);
        m.setAccessible(true);
        return m.invoke(target);
    }

    private static Object field(Object target, String name) throws Exception {
        if (target == null) return null;
        Field f = target.getClass().getDeclaredField(name);
        f.setAccessible(true);
        return f.get(target);
    }
}
