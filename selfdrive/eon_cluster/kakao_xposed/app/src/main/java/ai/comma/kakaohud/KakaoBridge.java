package ai.comma.kakaohud;

import java.lang.reflect.Method;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 후킹으로 가로챈 카카오 안내 객체에서 값을 뽑아 EON(7714 /kakao/)으로 보낸다.
 *
 * 카카오 KNSDK 는 난독화됐지만 **안내 객체의 getter 이름은 유지**된다:
 *   위치안내 KNGuide_Location: getLocation()->KNLocation, getGpsMatched()->KNGPSData
 *   경로안내 KNGuide_Route: getCurDirection()->방향(getRgCode/getDirectionAng),
 *                          getLane()->차로
 *   KNLocation: getPos()->DoublePoint(x,y), getAngleOrigin(), getRoadName(),
 *               getDistFromS()
 * 그래서 필드 오프셋이 아니라 **이름 기반 리플렉션**으로 접근한다(업데이트에 강함).
 *
 * 1차 목적 = 정찰: 실제 값이 기대와 맞는지 로그로 남기면서 동시에 전송을 시도한다.
 * 좌표계(KATEC vs WGS84)는 실측 로그의 x/y 범위로 확정한다.
 */
final class KakaoBridge {

    private final KakaoNaviClient client;
    private final AtomicBoolean loggedRouteShape = new AtomicBoolean(false);
    private final AtomicBoolean loggedLocShape = new AtomicBoolean(false);

    // 마지막 위치/방위(지도 캡처와 vehicle 스트림에 공용).
    volatile double lastX = 0, lastY = 0, lastBearing = 0;
    volatile boolean hasPos = false;

    KakaoBridge(KakaoNaviClient client) {
        this.client = client;
    }

    // ---- 위치 안내 (KNUSDKRepository$c.a 콜백의 두 번째 인자) ----
    void onLocationGuide(Object locGuide) {
        if (locGuide == null) return;
        try {
            Object loc = call(locGuide, "getLocation");
            if (loc == null) return;

            Object pos = call(loc, "getPos");     // DoublePoint
            double x = getDouble(pos, "getX");
            double y = getDouble(pos, "getY");
            int angle = getInt(loc, "getAngleOrigin");
            String road = getString(loc, "getRoadName");
            int distFromS = getInt(loc, "getDistFromS");

            if (loggedLocShape.compareAndSet(false, true)) {
                KakaoHudLog.line("LOC shape: x=" + x + " y=" + y
                        + " angle=" + angle + " road=" + road + " distFromS=" + distFromS
                        + "  (x>1e6 이면 KATEC, |x|<190 이면 WGS84)");
            }

            lastX = x;
            lastY = y;
            lastBearing = angle;
            hasPos = (x != 0 && y != 0);

            // vehicle 스트림(TMAP 스키마와 동일 필드). 좌표계는 서버/HUD가 그대로
            // 지도 캡처에 쓰므로, EON 소비측(navigation_route)이 기대하는 lat/lon 은
            // 좌표 확정 후 2차에서 변환한다. 1차엔 raw x/y 와 heading 을 실어 보낸다.
            String vehicle = "{\"lat\":" + y + ",\"lon\":" + x
                    + ",\"heading_deg\":" + angle
                    + ",\"road_name\":" + jsonStr(road)
                    + ",\"virtual_gps\":false}";
            client.sendState("vehicle", vehicle);

            KakaoHudLog.status("loc ok road=" + road + " ang=" + angle);
        } catch (Throwable t) {
            KakaoHudLog.ex("onLocationGuide", t);
        }
    }

    // ---- 경로 안내 (KNUSDKRepository$d.a 콜백의 두 번째 인자) ----
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
                String gcur = "{\"turn_type\":" + tbt
                        + ",\"distance_m\":" + curDist + "}";
                client.sendState("guidance_current", gcur);
            }
            if (nextRaw >= 0) {
                int tbt = KakaoCodes.turnType(nextRaw);
                int seg = (nextDist > 0 && curDist > 0) ? Math.max(0, nextDist - curDist) : nextDist;
                String gnext = "{\"turn_type\":" + tbt
                        + ",\"distance_m\":" + seg + "}";
                client.sendState("guidance_next", gnext);
            }

            // navigation_status: 경로안내가 오는 동안은 활성.
            client.sendState("navigation_status", "{\"off_route\":false}");

            KakaoHudLog.status("route ok cur=" + curRaw + "/" + curDist);
        } catch (Throwable t) {
            KakaoHudLog.ex("onRouteGuide", t);
        }
    }

    // ---- 안전/카메라 (KNUSDKRepository$e.a / .b 콜백) ----
    void onSafeties(Object listOrGuide) {
        if (listOrGuide == null) return;
        try {
            // 1차엔 형태만 확인. 실제 필드가 로그로 확정되면 speed 스트림으로 변환.
            KakaoHudLog.status("safety event: " + listOrGuide.getClass().getName());
        } catch (Throwable t) {
            KakaoHudLog.ex("onSafeties", t);
        }
    }

    // ---- 리플렉션 헬퍼 ----
    private int rgRaw(Object direction) {
        if (direction == null) return -1;
        try {
            Object rgCode = call(direction, "getRgCode"); // KNRGCode enum
            if (rgCode == null) return -1;
            // enum 의 실값 getter 이름은 난독화될 수 있어, 우선 getValue/ordinal 순으로 시도.
            Object v = tryCall(rgCode, "getValue");
            if (v instanceof Integer) return (Integer) v;
            // 대체: enum 이름으로 매핑(예: KNRGCode_LeftTurn) — value 를 못 얻을 때.
            String nm = rgCode.toString();
            return rawFromName(nm);
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

    /** enum 이름 → KNRGCode 실값(최소 집합). getValue 실패 시 폴백. */
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
        try {
            return call(obj, name);
        } catch (Throwable t) {
            return null;
        }
    }

    private static double getDouble(Object obj, String name) {
        try {
            Object v = call(obj, name);
            return v instanceof Number ? ((Number) v).doubleValue() : 0;
        } catch (Throwable t) {
            return 0;
        }
    }

    private static int getInt(Object obj, String name) {
        try {
            Object v = call(obj, name);
            return v instanceof Number ? ((Number) v).intValue() : -1;
        } catch (Throwable t) {
            return -1;
        }
    }

    private static String getString(Object obj, String name) {
        try {
            Object v = call(obj, name);
            return v == null ? "" : v.toString();
        } catch (Throwable t) {
            return "";
        }
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
