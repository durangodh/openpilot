package ai.comma.kakaohud;

import android.os.SystemClock;

import java.lang.ref.WeakReference;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * 카카오 차로 안내(폰 주행 화면의 차로 표시)를 lane_current 로 보낸다. 형식은 티맵·네이버
 * 모듈과 같다: count / current_lane / distance_m / lanes[] / turn_info[] / available[].
 *
 * 출처(4.51.1 디컴파일): KNULaneViewModel.getLaneUIState() 의 값이 Data
 * (navi.drive.core.feature.lane.e$a, 필드 a = KNULane)이면 차로 표시가 떠 있다.
 * KNULane(knmsdk.ji0.a): 필드 a = 차로 목록(KNULaneInfo), b = 남은 거리(m).
 * KNULaneInfo(knmsdk.ji0.m): 필드 a = KNULaneTurnType(이름에 STRAIGHT/TURNLEFT/
 * BEARRIGHT/UTURN 조합), f = 추천 차로(suggest).
 *
 * 바뀔 때와 1초마다 보내고, 사라지면 null 을 한 번 보낸다. 250ms 마다 읽기만 한다.
 */
final class KakaoLane {
    static final String VIEW_MODEL = "com.kakaomobility.navi.drive.core.feature.lane.KNULaneViewModel";
    private static final String DATA_STATE = "com.kakaomobility.navi.drive.core.feature.lane.e$a";
    private static final long POLL_MS = 250;
    private static final long RESEND_MS = 1000;
    private static final int MAX_LANES = 8;

    private final KakaoNaviClient client;
    private final ScheduledExecutorService poller = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "kakao-hud-lane");
        t.setDaemon(true);
        return t;
    });
    private volatile WeakReference<Object> viewModel;
    private boolean started;
    private String lastJson;
    private long lastSentAt;
    private boolean shown;
    private boolean loggedFirst, loggedError;

    KakaoLane(KakaoNaviClient client) {
        this.client = client;
    }

    /** KNULaneViewModel 생성자 후킹에서 호출. 가장 최근 것을 쓴다. */
    synchronized void setViewModel(Object vm) {
        viewModel = new WeakReference<>(vm);
        if (!started) {
            started = true;
            poller.scheduleWithFixedDelay(this::tick, POLL_MS, POLL_MS, TimeUnit.MILLISECONDS);
            KakaoHudLog.line("lane view model captured");
        }
    }

    private void tick() {
        try {
            if (!client.ready()) return;
            String json = currentLane();
            long now = SystemClock.elapsedRealtime();
            if (json == null) {
                if (shown) {
                    shown = false;
                    lastJson = null;
                    client.sendState("lane_current", null);
                }
                return;
            }
            if (json.equals(lastJson) && now - lastSentAt < RESEND_MS) return;
            client.sendState("lane_current", json);
            lastJson = json;
            lastSentAt = now;
            shown = true;
            if (!loggedFirst) {
                loggedFirst = true;
                KakaoHudLog.xposed("lane_current sent " + json);
            }
        } catch (Throwable t) {
            if (!loggedError) {
                loggedError = true;
                KakaoHudLog.ex("lane", t);
            }
        }
    }

    private String currentLane() throws Exception {
        WeakReference<Object> ref = viewModel;
        Object vm = ref == null ? null : ref.get();
        if (vm == null) return null;
        Object state = call(call(vm, "getLaneUIState"), "getValue");
        if (state == null || !DATA_STATE.equals(state.getClass().getName())) return null;
        Object lane = field(state, "a");                 // Data.lane (KNULane)
        Object infos = field(lane, "a");                 // KNULane.laneInfos
        if (!(infos instanceof List)) return null;
        List<?> list = (List<?>) infos;
        int n = Math.min(MAX_LANES, list.size());
        if (n <= 0) return null;
        int distance = ((Number) field(lane, "b")).intValue();   // KNULane.distance

        StringBuilder details = new StringBuilder("[");
        StringBuilder turns = new StringBuilder("[");
        StringBuilder available = new StringBuilder("[");
        int current = 0;
        for (int i = 0; i < n; i++) {
            Object info = list.get(i);
            Object turnType = field(info, "a");          // KNULaneInfo.turnType
            boolean suggest = Boolean.TRUE.equals(field(info, "f"));   // KNULaneInfo.suggest
            int turn = laneTurn(turnType instanceof Enum ? ((Enum<?>) turnType).name() : String.valueOf(turnType));
            if (suggest && current == 0) current = i + 1;
            if (i > 0) { details.append(','); turns.append(','); available.append(','); }
            details.append("{\"turn_type\":").append(turn).append(",\"recommended\":").append(suggest).append('}');
            turns.append(turn);
            available.append(suggest ? 1 : 0);
        }
        if (current == 0) current = 1;
        return "{\"source\":\"KAKAO\",\"count\":" + n + ",\"current_lane\":" + current
                + ",\"distance_m\":" + Math.max(0, distance)
                + ",\"lanes\":" + details.append(']') + ",\"turn_info\":" + turns.append(']')
                + ",\"available\":" + available.append(']') + "}";
    }

    /** KNULaneTurnType 이름 → 티맵/네이버와 같은 HUD 차로 화살표 코드(NaverCodes.laneTurn 규칙). */
    static int laneTurn(String name) {
        String v = name == null ? "" : name.toUpperCase(Locale.US);
        boolean straight = v.contains("STRAIGHT");
        boolean left = v.contains("LEFT");
        boolean right = v.contains("RIGHT");
        boolean uturn = v.contains("UTURN");
        if (uturn && !left && !right && !straight) return KakaoCodes.TBT_UTURN;
        if (straight && left && right) return 22;   // TBT_LANE_SPLIT
        if (straight && left) return 20;            // TBT_LANE_LEFT
        if (straight && right) return 21;           // TBT_LANE_RIGHT
        if (left && right) return 22;               // TBT_LANE_SPLIT
        if (left) return KakaoCodes.TBT_LEFT;
        if (right) return KakaoCodes.TBT_RIGHT;
        if (straight) return KakaoCodes.TBT_STRAIGHT;
        return KakaoCodes.TBT_NONE;
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
