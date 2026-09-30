package ai.comma.naverhud;

import android.app.Activity;

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
    private final ScheduledExecutorService poller = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "naver-hud-state");
        t.setDaemon(true);
        return t;
    });
    private volatile Object store;
    private volatile boolean started;
    private long lastPolylineAt;
    private String polyline = "[]";
    private long lastMapAt;

    void setStore(Object value) {
        store = value;
        if (!started) {
            synchronized (this) {
                if (!started) {
                    started = true;
                    new EonDiscovery(client).start();
                    poller.scheduleWithFixedDelay(this::tick, 0, 250, TimeUnit.MILLISECONDS);
                    NaverHudLog.line("NaviStore captured; direct 6.10 state polling started");
                }
            }
        }
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
        try {
            publish(current);
            long now = android.os.SystemClock.elapsedRealtime();
            if (now - lastMapAt >= 200) {
                lastMapAt = now;
                map.capture();
            }
        } catch (Throwable error) {
            NaverHudLog.ex("state tick", error);
        }
    }

    private void publish(Object s) {
        Object mode = value(call(s, "b0"));
        boolean guiding = mode != null && "Guiding".equals(String.valueOf(mode));
        client.sendState("navigation_status", "{\"source\":\"NAVER\",\"active\":" + guiding
                + ",\"state\":" + quote(String.valueOf(mode)) + "}");
        client.sendState("app_status", "{\"source\":\"NAVER\",\"foreground\":true,\"guidance_active\":" + guiding + "}");

        Object directions = value(call(s, "z0"));
        client.sendState("guidance_current", guidance(call(directions, "e")));
        client.sendState("guidance_next", guidance(call(directions, "f")));

        Object route = value(call(s, "o0"));
        Object goal = call(route, "getGoal");
        long now = android.os.SystemClock.elapsedRealtime();
        if (now - lastPolylineAt >= 5000) {
            polyline = routePolyline(s);
            lastPolylineAt = now;
        }
        client.sendState("route", "{\"source\":\"NAVER\",\"remain_distance_m\":"
                + NaverCodes.round(NaverCodes.number(call(goal, "distance")))
                + ",\"remain_time_sec\":" + NaverCodes.remainTimeSec(goal)
                + ",\"polyline\":" + polyline + "}");

        Object position = value(call(s, "P"));
        Object location = call(position, "getLocation");
        Object road = value(call(s, "B"));
        client.sendState("vehicle", "{\"source\":\"NAVER\",\"lat\":" + decimal(fieldNumber(location, "latitude"))
                + ",\"lon\":" + decimal(fieldNumber(location, "longitude"))
                + ",\"heading_deg\":" + decimal(NaverCodes.number(call(position, "getHeading")))
                + ",\"speed_kph\":" + decimal(NaverCodes.number(call(position, "getSpeedKmPerHour")))
                + ",\"road_name\":" + quote(text(call(road, "f"))) + "}");

        Object link = call(s, "T");
        client.sendState("speed", speed(s, (int) NaverCodes.number(call(link, "h"))));
        client.sendState("lane_current", lane(value(call(s, "S"))));
        client.sendState("traffic_signal", signal(value(call(s, "B0"))));
    }

    private static String guidance(Object direction) {
        if (direction == null) return "null";
        Object detail = call(direction, "i");
        Object turn = call(detail, "s");
        int raw = (int) NaverCodes.number(call(turn, "getValue"));
        int mapped = NaverCodes.turnType(raw, String.valueOf(turn));
        // The road name is optional in 6.10 (for example at unnamed turns).
        // Use the maneuver direction in that case; never render Java's "null".
        String name = text(call(detail, "v"));
        if (name.isEmpty()) name = text(call(detail, "n"));
        return "{\"source\":\"NAVER\",\"distance_m\":" + NaverCodes.round(NaverCodes.number(call(direction, "h")))
                + ",\"turn_type\":" + mapped + ",\"naver_turn_type\":" + raw
                + ",\"main_text\":" + quote(name) + ",\"road_name\":" + quote(name) + "}";
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
