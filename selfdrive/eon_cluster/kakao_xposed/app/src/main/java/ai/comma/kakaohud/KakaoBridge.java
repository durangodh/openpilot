package ai.comma.kakaohud;

import java.lang.reflect.Method;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 후킹으로 가로챈 카카오 안내 객체에서 값을 뽑아 EON(7714 /kakao/)으로 보낸다.
 *
 * 확정 사실(디컴파일 기준):
 *  - 모든 SDK 위치(KNLocation.getPos, KNGPSData.getPos)는 **KATEC** 좌표
 *    (예: DoublePoint(309840, 552483)). WGS84 위경도가 아니다.
 *  - KATEC→WGS84 변환: KNMCoordinateSystem.INSTANCE.katecToWGS84(x,y)
 *    (네이티브, 이름 유지). EON 소비측(navigation_route)은 lat/lon 을 기대하므로
 *    vehicle 스트림엔 변환한 WGS84 를 싣는다.
 *  - 지도 캡처(KNMMapCapturer)는 KATEC 를 그대로 쓴다(KNMPoint.katec).
 */
final class KakaoBridge {

    private final KakaoNaviClient client;
    private final KakaoMap map;
    private final AtomicBoolean loggedRouteShape = new AtomicBoolean(false);
    private final AtomicBoolean loggedLocShape = new AtomicBoolean(false);
    private final AtomicBoolean loggedSafety = new AtomicBoolean(false);
    private final AtomicBoolean loggedRouteSummary = new AtomicBoolean(false);
    private volatile int vehicleDistFromS = -1;
    private volatile Object repository;
    private volatile long lastRouteSummaryMs = 0;

    private Object coordCompanion;   // KNMCoordinateSystem.INSTANCE
    private Method katecToWgs;       // katecToWGS84(double,double) -> Pair

    KakaoBridge(KakaoNaviClient client, KakaoMap map) {
        this.client = client;
        this.map = map;
    }

    void setRepository(Object repository) {
        if (repository == null) return;
        boolean changed = this.repository != repository;
        this.repository = repository;
        if (changed) {
            KakaoHudLog.line("repository captured: " + repository.getClass().getName());
        }
    }

    void setClassLoader(ClassLoader cl) {
        try {
            Class<?> cs = cl.loadClass("com.kakaomobility.knmsdk.utils.KNMCoordinateSystem");
            Object inst = kotlinCompanion(cs);
            this.coordCompanion = inst;
            this.katecToWgs = inst.getClass().getMethod("katecToWGS84", double.class, double.class);
            KakaoHudLog.line("coord converter ready");
        } catch (Throwable t) {
            KakaoHudLog.ex("coord init", t);
        }
    }

    /** KATEC (x,y) -> [lon, lat]. 실패하면 null. */
    private double[] toWgs(double x, double y) {
        if (katecToWgs == null || coordCompanion == null) return null;
        try {
            Object pair = katecToWgs.invoke(coordCompanion, x, y);
            Object first = pair.getClass().getMethod("component1").invoke(pair);
            Object second = pair.getClass().getMethod("component2").invoke(pair);
            double a = ((Number) first).doubleValue();
            double b = ((Number) second).doubleValue();
            // 순서(lon,lat vs lat,lon)를 값 범위로 판정: 한국 경도 124~132, 위도 33~43.
            double lon, lat;
            if (a > 120 && a < 135 && b > 30 && b < 45) { lon = a; lat = b; }
            else { lon = b; lat = a; }
            return new double[]{lon, lat};
        } catch (Throwable t) {
            return null;
        }
    }

    // ---- 위치 안내 ----
    void onLocationGuide(Object locGuide) {
        if (locGuide == null) return;
        try {
            Object loc = callAny(locGuide, "getLocation", "c");
            if (loc == null) return;

            Object pos = callAny(loc, "getPos", "l");     // DoublePoint (KATEC)
            double kx = getDouble(pos, "getX", "b");
            double ky = getDouble(pos, "getY", "c");
            int angle = getInt(loc, "getAngleOrigin", "e");
            int distFromS = getInt(loc, "getDistFromS", "f");
            if (distFromS >= 0) vehicleDistFromS = distFromS;
            sendRouteSummary(loc);
            String road = getString(loc, "getRoadName", "m");

            double[] wgs = toWgs(kx, ky);

            if (loggedLocShape.compareAndSet(false, true)) {
                KakaoHudLog.line("LOC shape: katec=" + kx + "," + ky
                        + " -> wgs=" + (wgs == null ? "null" : (wgs[0] + "," + wgs[1]))
                        + " angle=" + angle + " road=" + road);
            }

            if (kx != 0 && ky != 0) {
                map.updatePose(kx, ky, angle);
                if (wgs != null) {
                    String vehicle = "{\"lat\":" + wgs[1] + ",\"lon\":" + wgs[0]
                            + ",\"heading_deg\":" + angle
                            + ",\"road_name\":" + jsonStr(road)
                            + ",\"virtual_gps\":false}";
                    client.sendState("vehicle", vehicle);
                }
            }
            KakaoHudLog.status("loc road=" + road + " ang=" + angle);
        } catch (Throwable t) {
            KakaoHudLog.ex("onLocationGuide", t);
        }
    }

    /**
     * SDK 경로의 d0/e0는 현재 위치부터 목적지까지의 거리와 링크별 예상 시간을
     * 계산한다. 버전 차이로 호출에 실패하면 KNURoute 총량과 진행거리로 보정한다.
     */
    private void sendRouteSummary(Object location) {
        Object repo = repository;
        if (repo == null || location == null) return;

        long now = android.os.SystemClock.elapsedRealtime();
        if (now - lastRouteSummaryMs < 1000) return;
        lastRouteSummaryMs = now;

        try {
            Object route = callAny(repo, "getCurrentRoute", "currentRoute", "U");
            if (route == null) {
                map.updateRoute(null);
                return;
            }

            int remainDistance = -1;
            int remainTime = -1;
            boolean exact = false;

            Object sdkRoute = tryCallAny(route, "getKnRoute", "d");
            if (sdkRoute != null) {
                map.updateRoute(sdkRoute);
                remainDistance = getIntWithArg(sdkRoute, location, "getRemainDist", "d0");
                remainTime = getIntWithArg(sdkRoute, location, "getRemainTime", "e0");
                exact = remainDistance >= 0 && remainTime >= 0;
            }

            if (!exact) {
                int totalDistance = getInt(route, "getTotalDist", "l");
                int totalTime = getInt(route, "getTotalTime", "m");
                if (totalDistance >= 0 && vehicleDistFromS >= 0) {
                    remainDistance = Math.max(0, totalDistance - vehicleDistFromS);
                    if (totalTime >= 0 && totalDistance > 0) {
                        remainTime = (int) Math.round(
                                (double) totalTime * remainDistance / totalDistance);
                    }
                }
            }

            if (remainDistance < 0 || remainTime < 0) return;
            client.sendState("route",
                    "{\"remain_distance_m\":" + remainDistance
                            + ",\"remain_time_sec\":" + remainTime + "}");

            if (loggedRouteSummary.compareAndSet(false, true)) {
                KakaoHudLog.line("ROUTE summary: remain=" + remainDistance
                        + "m time=" + remainTime + "s exact=" + exact);
            }
        } catch (Throwable t) {
            KakaoHudLog.ex("routeSummary", t);
        }
    }

    // ---- 경로 안내 ----
    void onRouteGuide(Object routeGuide) {
        if (routeGuide == null) return;
        try {
            Object cur = callAny(routeGuide, "getCurDirection", "b");
            Object next = callAny(routeGuide, "getNextDirection", "i");

            int curRaw = rgRaw(cur);
            int curDist = dirDist(cur);
            int nextRaw = rgRaw(next);
            int nextDist = dirDist(next);

            if (loggedRouteShape.compareAndSet(false, true)) {
                KakaoHudLog.line("ROUTE shape: curRgRaw=" + curRaw + " curDist=" + curDist
                        + " nextRgRaw=" + nextRaw + " nextDist=" + nextDist);
            }

            if (curRaw >= 0) {
                int tbt = KakaoCodes.turnType(curRaw);
                // KNGuide direction distance is measured from the route start.
                // HUD guidance needs the distance remaining from the vehicle.
                int remaining = curDist >= 0 && vehicleDistFromS >= 0
                        ? Math.max(0, curDist - vehicleDistFromS) : curDist;
                map.updateTurnDistance(remaining);
                if (remaining >= 0) {
                    client.sendState("guidance_current",
                            "{\"turn_type\":" + tbt + ",\"distance_m\":" + remaining + "}");
                }
            } else {
                map.updateTurnDistance(-1);
            }
            if (nextRaw >= 0) {
                int tbt = KakaoCodes.turnType(nextRaw);
                int seg = (nextDist > 0 && curDist > 0) ? Math.max(0, nextDist - curDist) : nextDist;
                client.sendState("guidance_next",
                        "{\"turn_type\":" + tbt + ",\"distance_m\":" + seg + "}");
            }
            client.sendState("navigation_status", "{\"off_route\":false}");
            KakaoHudLog.status("route cur=" + curRaw + "/" + curDist);
        } catch (Throwable t) {
            KakaoHudLog.ex("onRouteGuide", t);
        }
    }

    // ---- 안전/카메라 ----
    void onSafeties(Object safetyArg) {
        if (!(safetyArg instanceof java.util.List)) return;
        try {
            java.util.List<?> list = (java.util.List<?>) safetyArg;
            Object best = null;
            int bestDistance = Integer.MAX_VALUE;

            for (Object item : list) {
                if (item == null || getBoolean(item, "getPassed", "d")) continue;
                Object location = tryCallAny(item, "getLocation", "c");
                int absolute = location == null ? -1 : getInt(location, "getDistFromS", "f");
                int distance = absolute >= 0 && vehicleDistFromS >= 0
                        ? Math.max(0, absolute - vehicleDistFromS) : absolute;
                if (distance >= 0 && distance < bestDistance) {
                    best = item;
                    bestDistance = distance;
                }
            }

            if (best == null) {
                client.sendState("speed", "{}");
                return;
            }

            Object code = callAny(best, "getCode", "b");
            int rawCode = getInt(code, "getValue");
            int type = KakaoCodes.sdiType(rawCode);
            int limit = getInt(best, "getSpeedLimit", "l");
            if (limit < 0) limit = 0;

            int sectionDistance = getInt(best, "getRemainDist", "r");
            boolean section = sectionDistance > 0
                    || type == KakaoCodes.SDI_SECTION_START
                    || type == KakaoCodes.SDI_SECTION_END;

            String value;
            if (section && limit > 0) {
                int remaining = sectionDistance > 0 ? sectionDistance : bestDistance;
                value = "{\"section\":{\"active\":true,\"suspended\":false"
                        + ",\"speed_limit_kph\":" + limit
                        + ",\"remaining_distance_m\":" + Math.max(0, remaining) + "}}";
            } else {
                value = "{\"sdi\":{\"type\":" + type
                        + ",\"distance_m\":" + bestDistance
                        + ",\"speed_limit_kph\":" + limit + "}}";
            }
            client.sendState("speed", value);

            if (loggedSafety.compareAndSet(false, true)) {
                KakaoHudLog.line("SAFETY values: raw=" + rawCode + " type=" + type
                        + " distance=" + bestDistance + " limit=" + limit
                        + " section=" + section + " class=" + best.getClass().getName());
            } else {
                KakaoHudLog.status("safety raw=" + rawCode + " dist=" + bestDistance
                        + " limit=" + limit);
            }
        } catch (Throwable t) {
            KakaoHudLog.ex("onSafeties", t);
        }
    }

    // ---- 리플렉션 헬퍼 ----
    private int rgRaw(Object direction) {
        if (direction == null) return -1;
        try {
            Object rgCode = callAny(direction, "getRgCode", "g");
            if (rgCode == null) return -1;
            Object v = tryCall(rgCode, "getValue");
            if (v instanceof Integer) return (Integer) v;
            return rawFromName(rgCode.toString());
        } catch (Throwable t) {
            return -1;
        }
    }

    private int dirDist(Object direction) {
        if (direction == null) return -1;
        try {
            Object loc = callAny(direction, "getLocation", "e");
            return loc == null ? -1 : getInt(loc, "getDistFromS", "f");
        } catch (Throwable t) {
            return -1;
        }
    }

    private int rawFromName(String nm) {
        if (nm == null) return -1;
        if (nm.contains("Straight")) return 0;
        if (nm.contains("LeftTurn")) return 1;
        if (nm.contains("RightTurn")) return 2;
        if (nm.contains("UTurn")) return 3;
        if (nm.contains("LeftDirection")) return 5;
        if (nm.contains("RightDirection")) return 6;
        if (nm.contains("Goal")) return 101;
        return -1;
    }

    private static Object kotlinCompanion(Class<?> owner) throws Exception {
        for (String field : new String[]{"Companion", "INSTANCE"}) {
            try {
                return owner.getField(field).get(null);
            } catch (NoSuchFieldException ignored) { }
        }
        throw new NoSuchFieldException(owner.getName() + ".Companion");
    }

    private static Object call(Object obj, String name) throws Exception {
        Method m = obj.getClass().getMethod(name);
        m.setAccessible(true);
        return m.invoke(obj);
    }

    private static Object callWithArg(Object obj, String name, Object arg) throws Exception {
        for (Method m : obj.getClass().getMethods()) {
            Class<?>[] params = m.getParameterTypes();
            if (m.getName().equals(name) && params.length == 1
                    && params[0].isAssignableFrom(arg.getClass())) {
                m.setAccessible(true);
                return m.invoke(obj, arg);
            }
        }
        throw new NoSuchMethodException(obj.getClass().getName() + "." + name);
    }

    private static int getIntWithArg(Object obj, Object arg, String... names) {
        for (String name : names) {
            try {
                Object v = callWithArg(obj, name, arg);
                if (v instanceof Number) return ((Number) v).intValue();
            } catch (Throwable ignored) { }
        }
        return -1;
    }

    private static Object callAny(Object obj, String... names) throws Exception {
        NoSuchMethodException last = null;
        for (String name : names) {
            try {
                return call(obj, name);
            } catch (NoSuchMethodException e) {
                last = e;
            }
        }
        throw last != null ? last : new NoSuchMethodException(obj.getClass().getName());
    }

    private static Object tryCall(Object obj, String name) {
        try { return call(obj, name); } catch (Throwable t) { return null; }
    }

    private static Object tryCallAny(Object obj, String... names) {
        try { return callAny(obj, names); } catch (Throwable t) { return null; }
    }

    private static boolean getBoolean(Object obj, String... names) {
        try {
            Object v = callAny(obj, names);
            return v instanceof Boolean && (Boolean) v;
        } catch (Throwable t) { return false; }
    }

    private static double getDouble(Object obj, String... names) {
        try {
            Object v = callAny(obj, names);
            return v instanceof Number ? ((Number) v).doubleValue() : 0;
        } catch (Throwable t) { return 0; }
    }

    private static int getInt(Object obj, String... names) {
        try {
            Object v = callAny(obj, names);
            return v instanceof Number ? ((Number) v).intValue() : -1;
        } catch (Throwable t) { return -1; }
    }

    private static String getString(Object obj, String... names) {
        try {
            Object v = callAny(obj, names);
            return v == null ? "" : v.toString();
        } catch (Throwable t) { return ""; }
    }

    private static String jsonStr(String s) {
        if (s == null) return "\"\"";
        StringBuilder b = new StringBuilder("\"");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '"' || c == '\\') b.append('\\').append(c);
            else if (c == '\n') b.append("\\n");
            else if (c >= 0x20) b.append(c);
        }
        return b.append('"').toString();
    }
}
