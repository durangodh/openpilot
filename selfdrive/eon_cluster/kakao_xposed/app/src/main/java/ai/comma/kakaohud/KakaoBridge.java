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

    private Object coordCompanion;   // KNMCoordinateSystem.INSTANCE
    private Method katecToWgs;       // katecToWGS84(double,double) -> Pair

    KakaoBridge(KakaoNaviClient client, KakaoMap map) {
        this.client = client;
        this.map = map;
    }

    void setClassLoader(ClassLoader cl) {
        try {
            Class<?> cs = cl.loadClass("com.kakaomobility.knmsdk.utils.KNMCoordinateSystem");
            Object inst = cs.getField("INSTANCE").get(null);
            this.coordCompanion = inst;
            this.katecToWgs = cs.getMethod("katecToWGS84", double.class, double.class);
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
            Object loc = call(locGuide, "getLocation");
            if (loc == null) return;

            Object pos = call(loc, "getPos");     // DoublePoint (KATEC)
            double kx = getDouble(pos, "getX");
            double ky = getDouble(pos, "getY");
            int angle = getInt(loc, "getAngleOrigin");
            String road = getString(loc, "getRoadName");

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

    // ---- 경로 안내 ----
    void onRouteGuide(Object routeGuide) {
        if (routeGuide == null) return;
        try {
            Object cur = call(routeGuide, "getCurDirection");
            Object next = call(routeGuide, "getNextDirection");

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
                client.sendState("guidance_current",
                        "{\"turn_type\":" + tbt + ",\"distance_m\":" + curDist + "}");
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
        if (safetyArg == null) return;
        try {
            if (loggedSafety.compareAndSet(false, true)) {
                StringBuilder sb = new StringBuilder("SAFETY shape: " + safetyArg.getClass().getName());
                if (safetyArg instanceof java.util.List) {
                    java.util.List<?> list = (java.util.List<?>) safetyArg;
                    sb.append(" list.size=").append(list.size());
                    if (!list.isEmpty()) {
                        Object e0 = list.get(0);
                        sb.append(" elem=").append(e0.getClass().getName());
                        for (Method m : e0.getClass().getMethods()) {
                            if (m.getParameterTypes().length == 0 && m.getName().startsWith("get")) {
                                try {
                                    Object v = m.invoke(e0);
                                    sb.append(" ").append(m.getName()).append("=").append(v);
                                } catch (Throwable ignored) { }
                            }
                        }
                    }
                }
                KakaoHudLog.line(sb.toString());
            }
        } catch (Throwable t) {
            KakaoHudLog.ex("onSafeties", t);
        }
    }

    // ---- 리플렉션 헬퍼 ----
    private int rgRaw(Object direction) {
        if (direction == null) return -1;
        try {
            Object rgCode = call(direction, "getRgCode");
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
            Object loc = call(direction, "getLocation");
            return loc == null ? -1 : getInt(loc, "getDistFromS");
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

    private static Object call(Object obj, String name) throws Exception {
        Method m = obj.getClass().getMethod(name);
        m.setAccessible(true);
        return m.invoke(obj);
    }

    private static Object tryCall(Object obj, String name) {
        try { return call(obj, name); } catch (Throwable t) { return null; }
    }

    private static double getDouble(Object obj, String name) {
        try {
            Object v = call(obj, name);
            return v instanceof Number ? ((Number) v).doubleValue() : 0;
        } catch (Throwable t) { return 0; }
    }

    private static int getInt(Object obj, String name) {
        try {
            Object v = call(obj, name);
            return v instanceof Number ? ((Number) v).intValue() : -1;
        } catch (Throwable t) { return -1; }
    }

    private static String getString(Object obj, String name) {
        try {
            Object v = call(obj, name);
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
