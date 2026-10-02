package ai.comma.naverhud;

import android.app.Activity;
import android.content.Context;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/** Reads Naver Maps 6.10.0.16's live NaviStore and publishes EON HUD state. */
final class NaverBridge {
    private final NaverNaviClient client = new NaverNaviClient();
    private final NaverMapCapture map = new NaverMapCapture(client);
    // 확인한 버전에서만: 화면 스냅샷 대신 지도 엔진으로 직접 그린다.
    private final NaverMapRender render = new NaverMapRender(client, map);
    private volatile Context appContext;
    private volatile boolean renderAllowed;
    // HUD 지도 신호등 칸은 PNG 자산이 와야 그려진다. 카카오처럼 직접 그려 보낸다.
    private final NaverSignal signalImage = new NaverSignal(client);
    private final NaverJunction junction = new NaverJunction(client);
    // HUD 지도 아래 차로 띠와 신호등: 폰 표시 뷰를 그대로 떠서 보낸다.
    private final NaverViewImage laneImage = new NaverViewImage(client, "lane_bottom", 530, 84, 1000);
    // 신호등은 잔여초가 매초 바뀌므로 간격 제한 없이 바뀔 때마다 보낸다.
    private final NaverViewImage signalView = new NaverViewImage(client, "traffic_signal", 302, 192, 0);
    private final ScheduledExecutorService poller = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "naver-hud-state");
        t.setDaemon(true);
        return t;
    });
    private volatile Object store;
    private volatile boolean started;
    private long lastPolylineAt;
    private String polyline = "[]";

    // EON 부하: 250ms 마다 8개 항목을 무조건 보내면 EON 이 초당 32건을 파싱한다.
    // 특히 route 는 경로 좌표(최대 500점, ~20KB)를 품고 있어 초당 ~80KB 였다.
    // 값이 바뀌었을 때 + 1초 하트비트로만 보내고, route 는 최대 1Hz 로 제한한다.
    // 서버는 항목 값을 통째로 바꾸므로 route 에서 좌표를 빼면 안 된다(경로선이 사라짐).
    private static final long HEARTBEAT_MS = 1000;
    private static final long ROUTE_MIN_INTERVAL_MS = 1000;
    private final java.util.HashMap<String, String> lastSent = new java.util.HashMap<>();
    private final java.util.HashMap<String, Long> lastSentAt = new java.util.HashMap<>();
    private boolean healthLogged;
    private int guidingTicks;

    private void send(String name, String value) {
        long now = android.os.SystemClock.elapsedRealtime();
        String prev = lastSent.get(name);
        Long at = lastSentAt.get(name);
        boolean changed = prev == null || !prev.equals(value);
        boolean heartbeat = at == null || now - at >= HEARTBEAT_MS;
        if ("route".equals(name) && at != null && now - at < ROUTE_MIN_INTERVAL_MS) return;
        if (!changed && !heartbeat) return;
        lastSent.put(name, value);
        lastSentAt.put(name, now);
        client.sendState(name, value);
    }

    /** 메인 스레드: 폰 차로 표시(NaviLaneControlView)가 갱신됐다. */
    void onLaneView(android.view.View view, Object item) {
        laneImage.onUpdate(view, NaverViewImage.hasItems(item));
    }

    /** 메인 스레드: 폰 신호등 표시(NaviTrafficSignalView)가 갱신됐다. */
    void onSignalView(android.view.View view, boolean enabled, Object info) {
        signalView.onUpdate(view, enabled && NaverViewImage.hasItems(info));
    }

    void setStore(Object value) {
        store = value;
        map.setStore(value);
        start();
        NaverHudLog.line("NaviStore captured; direct 6.10 state polling active");
    }

    /**
     * EON 찾기·상태 폴링·지도 스냅샷을 시작한다(한 번만). 앱이 붙자마자 부른다.
     * 예전에는 NaviStore 가 생길 때까지 미뤘는데, 네이버는 NaviStore 를 길안내 엔진
     * (NaviEngine)이 처음 쓰일 때 만들어서, 네비 전환 뒤 HUD 지도가 티맵·카카오보다
     * 한참 늦게 떴다. 상태 항목은 NaviStore 가 생긴 뒤부터(tick 이 store 를 기다림),
     * 지도는 그 전에도 화면 지도(MapView) 스냅샷으로 보낸다.
     */
    void start() {
        if (started) return;
        synchronized (this) {
            if (started) return;
            started = true;
            new EonDiscovery(client).start();
            poller.scheduleWithFixedDelay(this::tick, 0, 250, TimeUnit.MILLISECONDS);
            // 지도는 상태 폴링(250ms, 처리시간만큼 더 밀림)과 분리해 자체 주기로 찍는다.
            map.start();
            NaverHudLog.xposed("EON discovery, state polling and map capture started");
        }
    }

    /**
     * 기본은 화면 지도 스냅샷(예전 브릿지 앱 방식)이다. 엔진 렌더는 같은 S9 의 HUD 앱을
     * 밀리게 해서 기본에서 뺐다. 이 파일이 있을 때만 엔진 렌더를 켠다.
     */
    static final String RENDER_FILE =
            "/sdcard/Android/data/com.nhn.android.nmap/files/naver_hud_render";

    void enableMapRender(Context app) {
        appContext = app;
        if (!new java.io.File(RENDER_FILE).exists()) {
            NaverHudLog.xposed("map: phone map snapshots (engine render off; create " + RENDER_FILE + " to turn it on)");
            return;
        }
        NaverHudLog.xposed("map: engine render on (" + RENDER_FILE + ")");
        renderAllowed = true;
    }

    /** 메인 스레드: 네이버 지도 화면이 보이기 시작/멈춤. */
    void setPhoneVisible(boolean visible) {
        render.setPhoneVisible(visible);
    }

    void setActivity(Activity activity) {
        map.addActivity(activity);
    }

    void setMapProvider(Object provider) {
        map.setMapProvider(provider);
    }

    private void tick() {
        Object current = store;
        if (current == null || !client.ready()) return;
        if (renderAllowed) render.maybeStart(appContext, current);
        try {
            publish(current);
        } catch (Throwable error) {
            NaverHudLog.ex("state tick", error);
        }
    }

    private void publish(Object s) {
        Object mode = value(call(s, "b0"));
        boolean guiding = mode != null && "Guiding".equals(String.valueOf(mode));
        send("navigation_status", "{\"source\":\"NAVER\",\"active\":" + guiding
                + ",\"state\":" + quote(String.valueOf(mode)) + "}");
        send("app_status", "{\"source\":\"NAVER\",\"foreground\":true,\"guidance_active\":" + guiding + "}");

        Object directions = value(call(s, "z0"));
        send("guidance_current", guidance(call(directions, "e")));
        send("guidance_next", guidance(call(directions, "f")));

        Object route = value(call(s, "o0"));
        Object goal = call(route, "getGoal");
        long now = android.os.SystemClock.elapsedRealtime();
        if (now - lastPolylineAt >= 5000) {
            polyline = routePolyline(s);
            lastPolylineAt = now;
        }
        send("route", "{\"source\":\"NAVER\",\"remain_distance_m\":"
                + NaverCodes.round(NaverCodes.number(call(goal, "distance")))
                + ",\"remain_time_sec\":" + NaverCodes.remainTimeSec(goal)
                + ",\"polyline\":" + polyline + "}");

        Object position = value(call(s, "P"));
        Object location = call(position, "getLocation");
        Object road = value(call(s, "B"));
        send("vehicle", "{\"source\":\"NAVER\",\"lat\":" + decimal(fieldNumber(location, "latitude"))
                + ",\"lon\":" + decimal(fieldNumber(location, "longitude"))
                + ",\"heading_deg\":" + decimal(NaverCodes.number(call(position, "getHeading")))
                + ",\"speed_kph\":" + decimal(NaverCodes.number(call(position, "getSpeedKmPerHour")))
                + ",\"road_name\":" + quote(text(call(road, "f"))) + "}");

        Object link = call(s, "T");
        send("speed", speed(s, (int) NaverCodes.number(call(link, "h"))));
        send("lane_current", lane(value(call(s, "S"))));
        Object signalItem = value(call(s, "B0"));
        send("traffic_signal", signal(signalItem));
        publishSignalImage(guiding ? signalItem : null);
        junction.publish(s, guiding);

        // 검증 안 된 버전에서 난독화 게터가 비어 있는지 한 번 기록한다.
        if (guiding && !healthLogged && ++guidingTicks >= 20) {
            healthLogged = true;
            NaverHudLog.line("health: directions=" + (directions != null)
                    + " route=" + (route != null) + " goal=" + (goal != null)
                    + " position=" + (position != null) + " location=" + (location != null)
                    + " road=" + (road != null) + " link=" + (link != null)
                    + " polylinePts=" + (polyline.length() > 2));
        }
    }

    private static String guidance(Object direction) {
        if (direction == null) return "null";
        Object detail = call(direction, "i");
        Object turn = call(detail, "s");
        int raw = (int) NaverCodes.number(call(turn, "getValue"));
        int mapped = NaverCodes.turnType(raw, String.valueOf(turn));
        // TbtDataItem (6.10): n() = direction (방면, comma separated), v() = roadName.
        // Naver's own TBT banner (TbtComponent) shows n(), so main_text follows it like
        // TMAP's szTBTMainText and Kakao's node name; the road name is the fallback.
        // Never render Java's "null".
        String road = text(call(detail, "v"));
        String towards = text(call(detail, "n")).replaceAll("\\s*,\\s*", ", ").trim();
        String main = towards.isEmpty() ? road : towards;
        return "{\"source\":\"NAVER\",\"distance_m\":" + NaverCodes.round(NaverCodes.number(call(direction, "h")))
                + ",\"turn_type\":" + mapped + ",\"naver_turn_type\":" + raw
                + ",\"main_text\":" + quote(main) + ",\"road_name\":" + quote(road.isEmpty() ? main : road) + "}";
    }

    private static String speed(Object store, int roadLimit) {
        List<?> events = asList(value(call(store, "q0")));
        Object primary = null, secondary = null, sectionOwner = null;
        if (events.isEmpty()) {
            Object first = value(call(store, "r0"));
            Object second = value(call(store, "s0"));
            if (section(first) != null) sectionOwner = first;
            else if (section(second) != null) sectionOwner = second;
            if (isDisplayableSafety(first)) primary = first;
            if (second != first && isDisplayableSafety(second)) {
                if (primary == null) primary = second;
                else secondary = second;
            }
        } else {
            int index = 0;
            for (Object event : events) {
                // Preserve the existing section scope; a distant section later
                // in the list must not hide an approaching speed camera.
                if (index++ < 2 && sectionOwner == null && section(event) != null) {
                    sectionOwner = event;
                }
                if (event == null) continue;
                if (!isDisplayableSafety(event) || event == primary) continue;
                if (primary == null) primary = event;
                else if (secondary == null) secondary = event;
            }
        }
        StringBuilder out = new StringBuilder("{\"source\":\"NAVER\",\"road_limit_kph\":").append(Math.max(0, roadLimit));
        if (primary != null) out.append(",\"sdi\":").append(NaverCodes.safetyJson(primary));
        if (secondary != null && secondary != primary) out.append(",\"sdi_secondary\":").append(NaverCodes.safetyJson(secondary));
        if (sectionOwner != null) {
            Object section = section(sectionOwner);
            out.append(",\"section\":{\"active\":true,\"speed_limit_kph\":")
                    .append(NaverCodes.speedLimit(sectionOwner))
                    .append(",\"remaining_distance_m\":")
                    .append(NaverCodes.round(NaverCodes.number(NaverCodes.callPrefix(section, "getRemainSectionDistance"))))
                    .append(",\"average_speed_kph\":")
                    .append(NaverCodes.round(NaverCodes.number(NaverCodes.callPrefix(section, "getAverageSpeed"))))
                    .append('}');
        }
        return out.append('}').toString();
    }

    private static boolean isDisplayableSafety(Object sdi) {
        if (sdi == null || NaverCodes.number(call(sdi, "distance")) <= 0) return false;
        Object code = call(sdi, "getCode");
        int raw = (int) NaverCodes.number(call(code, "getValue"));
        int type = NaverCodes.sdiType(raw, String.valueOf(code));
        return type == NaverCodes.SDI_SPEED_BUMP
                || (NaverCodes.sdiLimitAllowed(type) && NaverCodes.speedLimit(sdi) > 0);
    }

    private static Object section(Object sdi) {
        Object result = call(sdi, "getSectionSpeedCamera");
        return Boolean.TRUE.equals(call(result, "isValid")) ? result : null;
    }

    private static String lane(Object item) {
        if (item == null) return "null";
        List<?> lanes = asList(call(item, "i"));
        if (lanes.isEmpty()) lanes = asList(call(item, "j"));
        StringBuilder details = new StringBuilder("[");
        StringBuilder turns = new StringBuilder("[");
        StringBuilder available = new StringBuilder("[");
        int current = 1;
        for (int i = 0; i < lanes.size() && i < 8; i++) {
            Object entry = lanes.get(i);
            boolean recommended = Boolean.TRUE.equals(call(entry, "getRecommend"));
            Object guide = call(entry, "getGuideSet");
            if (guide == null) guide = call(entry, "g");
            int turn = NaverCodes.laneTurn(String.valueOf(guide));
            if (recommended) current = i + 1;
            if (i > 0) { details.append(','); turns.append(','); available.append(','); }
            details.append("{\"turn_type\":").append(turn).append(",\"recommended\":").append(recommended).append('}');
            turns.append(turn);
            available.append(recommended ? 1 : 0);
        }
        return "{\"source\":\"NAVER\",\"count\":" + Math.min(8, lanes.size())
                + ",\"current_lane\":" + current + ",\"distance_m\":" + NaverCodes.round(NaverCodes.number(call(item, "g")))
                + ",\"lanes\":" + details.append(']') + ",\"turn_info\":" + turns.append(']')
                + ",\"available\":" + available.append(']') + "}";
    }

    /** 직진 신호(없으면 유일한 신호)의 색·잔여초를 HUD 신호등 그림으로 보낸다. */
    private void publishSignalImage(Object item) {
        // 폰 신호등 뷰를 뜨고 있으면 그 그림(폰과 같은 신호·잔여초)만 쓴다.
        if (signalView.used()) return;
        try {
            if (item == null || Boolean.TRUE.equals(call(item, "h"))) {
                signalImage.clear();
                return;
            }
            List<?> signals = asList(call(item, "k"));
            Object chosen = signals.size() == 1 ? signals.get(0) : null;
            for (Object e : signals) {
                String guide = String.valueOf(call(e, "g")).toLowerCase(java.util.Locale.ROOT);
                if (guide.contains("straight") || guide.contains("through") || guide.contains("직진")) {
                    chosen = e;
                    break;
                }
            }
            int color = chosen == null ? 0 : signalColor(String.valueOf(call(chosen, "j")));
            if (color == 0) {
                signalImage.clear();
            } else {
                signalImage.publish(color, (int) NaverCodes.number(call(chosen, "i")));
            }
        } catch (Throwable error) {
            NaverHudLog.ex("signal image", error);
        }
    }

    /** navigation_route._signal_phase 와 같은 규칙. 1 빨강 / 2 노랑 / 3 초록 / 0 모름. */
    static int signalColor(String state) {
        String t = state == null ? "" : state.toLowerCase(java.util.Locale.ROOT);
        if (t.contains("red") || t.contains("stop") || t.contains("적색") || t.contains("빨")) return 1;
        if (t.contains("yellow") || t.contains("amber") || t.contains("caution")
                || t.contains("clearance") || t.contains("황색") || t.contains("노란")) return 2;
        if (t.contains("green") || t.contains("protected") || t.contains("permissive")
                || t.contains("녹색") || t.contains("초록")) return 3;
        return 0;
    }

    private static String signal(Object item) {
        if (item == null) return "null";
        List<?> signals = asList(call(item, "k"));
        StringBuilder entries = new StringBuilder("[");
        for (int i = 0; i < signals.size(); i++) {
            Object e = signals.get(i);
            if (i > 0) entries.append(',');
            entries.append("{\"guide\":").append(quote(String.valueOf(call(e, "g"))))
                    .append(",\"state\":").append(quote(String.valueOf(call(e, "j"))))
                    .append(",\"remaining_sec\":").append((int) NaverCodes.number(call(e, "i"))).append('}');
        }
        return "{\"source\":\"NAVER\",\"distance_m\":" + (int) NaverCodes.number(call(item, "j"))
                + ",\"blink\":" + Boolean.TRUE.equals(call(item, "h")) + ",\"signals\":" + entries.append(']') + "}";
    }

    private static String routePolyline(Object store) {
        List<?> points = asList(call(call(call(value(call(store, "K")), "e"), "j"), "getPathPoints"));
        if (points.isEmpty()) return "[]";
        int stride = Math.max(1, points.size() / 500);
        StringBuilder out = new StringBuilder("[");
        for (int i = 0; i < points.size(); i += stride) {
            if (out.length() > 1) out.append(',');
            Object p = points.get(i);
            out.append("{\"lat\":").append(decimal(fieldNumber(p, "latitude")))
                    .append(",\"lon\":").append(decimal(fieldNumber(p, "longitude"))).append('}');
        }
        return out.append(']').toString();
    }

    private static List<?> asList(Object value) {
        return value instanceof List ? (List<?>) value : Collections.emptyList();
    }

    private static Object value(Object liveData) { return call(liveData, "getValue"); }

    private static Object call(Object target, String name) {
        if (target == null) return null;
        try {
            Method method = target.getClass().getMethod(name);
            method.setAccessible(true);
            return method.invoke(target);
        } catch (Throwable ignored) { return null; }
    }

    private static double fieldNumber(Object target, String name) {
        if (target == null) return 0;
        try {
            Field field = target.getClass().getField(name);
            field.setAccessible(true);
            return NaverCodes.number(field.get(target));
        } catch (Throwable ignored) { return 0; }
    }

    private static String decimal(double value) {
        return Double.isFinite(value) ? String.format(Locale.US, "%.6f", value) : "0";
    }

    private static String quote(String value) {
        return "\"" + NaverCodes.esc(value) + "\"";
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }
}
