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

    // 안내·카메라는 경로 기준 절대거리(distFromS)로 캐시해 두고, 위치가 갱신될
    // 때마다 현재 차량 진행거리로 다시 빼서 보낸다. 카카오 앱도 위치 Flow 와
    // 경로 Flow 를 combine 해서 위치마다 거리를 새로 계산한다
    // (KNUMapLocationUseCase). 경로안내 콜백은 정지 중에는 거의 오지 않아,
    // 콜백 때만 계산하면 거리가 그 시점 값으로 멈춰 있었다(정지 중 91m 차이).
    private final Object guideLock = new Object();
    private int cachedCurAbs = -1, cachedCurTbt = KakaoCodes.TBT_NONE;
    // 카카오 배너 거리와 똑같이 맞추기 위해 SDK 위치 객체 자체를 들고 있다가
    // 카카오 앱과 같은 함수(o60.a.d)로 거리를 구한다.
    private Object lastVehicleLoc = null;
    private Object cachedCurLoc = null;
    private Object cachedSafetyLoc = null;
    private int cachedNextAbs = -1, cachedNextTbt = KakaoCodes.TBT_NONE;
    private boolean hasCachedGuide = false;
    private boolean hasCachedSafety = false;
    private boolean cachedSafetyEmpty = true;
    private int cachedSafetyAbs = -1, cachedSafetyType = 0, cachedSafetyLimit = 0;
    private boolean cachedSafetySection = false;
    private Object cachedSafetyItem = null;   // 통과 재확인용 원본 항목
    private int lastSafetyDistance = Integer.MAX_VALUE;  // 단조 감소 확인용
    private int cachedSectionRemain = -1, cachedSectionVehicleAt = -1;
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
            synchronized (guideLock) {
                lastVehicleLoc = loc;
            }
            sendRouteSummary(loc);
            publishGuidance();
            publishSafety();
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
            String curName = rgName(cur);
            String nextName = rgName(next);
            int curTbt = KakaoCodes.turnType(curName, curRaw, dirAngle(cur));
            int nextTbt = KakaoCodes.turnType(nextName, nextRaw, dirAngle(next));

            if (loggedRouteShape.compareAndSet(false, true)) {
                KakaoHudLog.line("ROUTE shape: cur=" + curName + "/" + curRaw + "->" + curTbt
                        + " dist=" + curDist + " next=" + nextName + "/" + nextRaw
                        + "->" + nextTbt + " dist=" + nextDist);
            }

            Object curLoc = cur == null ? null : tryCallAny(cur, "getLocation", "e");
            synchronized (guideLock) {
                cachedCurLoc = curLoc;
                cachedCurAbs = curDist;
                cachedCurTbt = curTbt;
                cachedNextAbs = nextDist;
                cachedNextTbt = nextTbt;
                hasCachedGuide = true;
            }
            publishGuidance();
            client.sendState("navigation_status", "{\"off_route\":false}");
            KakaoHudLog.status("route cur=" + curRaw + "/" + curDist);
        } catch (Throwable t) {
            KakaoHudLog.ex("onRouteGuide", t);
        }
    }

    // ---- 안전/카메라 ----
    void onSafetyGuide(Object guide) {
        if (guide == null) return;
        Object safeties = tryCallAny(guide, "getSafetiesOnGuide", "a");
        onSafeties(safeties);
    }

    void onSafeties(Object safetyArg) {
        if (!(safetyArg instanceof java.util.List)) return;
        try {
            java.util.List<?> list = (java.util.List<?>) safetyArg;
            KakaoHudLog.status("safety callback count=" + list.size());
            Object best = null;
            int bestDistance = Integer.MAX_VALUE;

            for (Object item : list) {
                if (item == null || getBoolean(item, "getPassed", "d")) continue;
                // 사고다발·급커브처럼 제한속도가 없는 안내가 먼저 잡혀서 뒤의
                // 과속카메라를 가리지 않도록, 감속 대상(방지턱/제한속도 있음)만 고른다.
                int itemLimit = getInt(item, "getSpeedLimit", "l");
                int itemType = KakaoCodes.sdiType(enumName(tryCallAny(item, "getCode", "b")),
                        enumValue(tryCallAny(item, "getCode", "b")));
                if (!KakaoCodes.isSpeedRelevant(itemType, itemLimit)) continue;
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
                synchronized (guideLock) {
                    hasCachedSafety = true;
                    cachedSafetyEmpty = true;
                    cachedSafetyItem = null;
                }
                publishSafety();
                return;
            }

            Object code = callAny(best, "getCode", "b");
            int rawCode = enumValue(code);
            String codeName = enumName(code);
            int type = KakaoCodes.sdiType(codeName, rawCode);
            int limit = getInt(best, "getSpeedLimit", "l");
            if (limit < 0) limit = 0;

            int sectionDistance = getInt(best, "getRemainDist", "r");
            boolean section = sectionDistance > 0
                    || KakaoCodes.isSection(type);

            Object bestLoc = tryCallAny(best, "getLocation", "c");
            int bestAbs = bestLoc == null ? -1 : getInt(bestLoc, "getDistFromS", "f");
            synchronized (guideLock) {
                hasCachedSafety = true;
                cachedSafetyEmpty = false;
                cachedSafetyAbs = bestAbs;
                cachedSafetyLoc = bestLoc;
                cachedSafetyItem = best;
                lastSafetyDistance = Integer.MAX_VALUE;
                cachedSafetyType = type;
                cachedSafetyLimit = limit;
                cachedSafetySection = section && limit > 0;
                cachedSectionRemain = sectionDistance;
                cachedSectionVehicleAt = vehicleDistFromS;
            }
            publishSafety();

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
    /** 캐시된 안내를 현재 차량 진행거리 기준으로 다시 계산해 보낸다. */
    private void publishGuidance() {
        int curAbs, curTbt, nextAbs, nextTbt;
        Object vehicleLoc, curLoc;
        synchronized (guideLock) {
            if (!hasCachedGuide) return;
            curAbs = cachedCurAbs; curTbt = cachedCurTbt;
            nextAbs = cachedNextAbs; nextTbt = cachedNextTbt;
            vehicleLoc = lastVehicleLoc; curLoc = cachedCurLoc;
        }
        int vehicle = vehicleDistFromS;
        if (curTbt != KakaoCodes.TBT_NONE) {
            int remaining = kakaoDistance(vehicleLoc, curLoc, curAbs, vehicle);
            map.updateTurnDistance(remaining);
            if (remaining >= 0) {
                client.sendState("guidance_current",
                        "{\"turn_type\":" + curTbt + ",\"distance_m\":" + remaining + "}");
            }
        } else {
            map.updateTurnDistance(-1);
        }
        if (nextTbt != KakaoCodes.TBT_NONE) {
            // 다음 안내는 현재 안내 지점부터의 구간 거리(차량 위치와 무관).
            int seg = (nextAbs > 0 && curAbs > 0) ? Math.max(0, nextAbs - curAbs) : nextAbs;
            client.sendState("guidance_next",
                    "{\"turn_type\":" + nextTbt + ",\"distance_m\":" + seg + "}");
        }
    }

    /** 캐시된 카메라/구간을 현재 차량 진행거리 기준으로 다시 계산해 보낸다. */
    private void publishSafety() {
        boolean empty, sectionMode;
        int abs, type, limit, secRemain, secAt;
        Object vehicleLoc, safetyLoc, item;
        synchronized (guideLock) {
            if (!hasCachedSafety) return;
            vehicleLoc = lastVehicleLoc; safetyLoc = cachedSafetyLoc;
            empty = cachedSafetyEmpty; abs = cachedSafetyAbs; type = cachedSafetyType;
            limit = cachedSafetyLimit; sectionMode = cachedSafetySection;
            secRemain = cachedSectionRemain; secAt = cachedSectionVehicleAt;
            item = cachedSafetyItem;
        }
        if (empty) {
            client.sendState("speed", "{}");
            return;
        }
        // 매 프레임 통과 여부를 원본 항목에서 다시 확인한다. SDK 거리함수가 통과
        // 후에도 양수를 주는 경우가 있어(경로객체 변경시 직선거리), 부호만으론
        // 지나간 카메라가 남는다. getPassed 가 참이면 즉시 지운다.
        if (item != null && getBoolean(item, "getPassed", "d")) {
            synchronized (guideLock) { cachedSafetyEmpty = true; cachedSafetyItem = null; }
            client.sendState("speed", "{}");
            return;
        }
        int vehicle = vehicleDistFromS;
        int distance = kakaoDistanceSigned(vehicleLoc, safetyLoc, abs, vehicle);
        // 단조 감소 가드: 한 번 가까워진 거리가 갑자기 늘면(경로 재계산·직선거리
        // 튐) 지나친 것으로 보고 지운다. 800m 넘게 튀면 무시.
        if (!sectionMode) {
            if (distance >= 0 && distance <= lastSafetyDistance + 50) {
                lastSafetyDistance = Math.min(lastSafetyDistance, distance);
            } else if (distance > lastSafetyDistance + 50 && lastSafetyDistance < 400) {
                synchronized (guideLock) { cachedSafetyEmpty = true; cachedSafetyItem = null; }
                client.sendState("speed", "{}");
                return;
            }
        }
        if (sectionMode) {
            int remaining;
            if (secRemain > 0) {
                // 구간 잔여거리는 콜백 시점 값이라 그 뒤 진행한 만큼 뺀다.
                int moved = (secAt >= 0 && vehicle >= secAt) ? vehicle - secAt : 0;
                remaining = secRemain - moved;
            } else {
                remaining = distance;
            }
            client.sendState("speed", "{\"section\":{\"active\":true,\"suspended\":false"
                    + ",\"speed_limit_kph\":" + limit
                    + ",\"remaining_distance_m\":" + Math.max(0, remaining) + "}}");
            return;
        }
        if (distance < 0) {
            // 지나친 카메라는 다음 안전 콜백이 올 때까지 보내지 않는다.
            client.sendState("speed", "{}");
            return;
        }
        client.sendState("speed", "{\"sdi\":{\"type\":" + type
                + ",\"distance_m\":" + distance
                + ",\"speed_limit_kph\":" + limit + "}}");
    }

    private Method sdkDistanceMethod;
    private boolean sdkDistanceLogged = false;

    /**
     * 카카오 앱 배너 거리 = C1299h.c(현재위치, 안내위치) = 현재위치.knLocation.d(안내위치).
     * SDK o60.a.d() 는 같은 경로 객체면 distFromS 차이를, 다르면 직선거리 등을 쓴다.
     * 우리가 distFromS 차이만 쓰면 이 분기 차이만큼 카카오 화면과 어긋난다(443m vs 534m).
     * 그래서 같은 함수를 그대로 호출한다. 메서드 이름 "d" 는 4.51.0 dex 에서 확인했다.
     */
    private int kakaoDistanceSigned(Object from, Object to, int toAbs, int vehicleAbs) {
        if (from != null && to != null && from.getClass() == to.getClass()) {
            try {
                Method m = sdkDistanceMethod;
                if (m == null || m.getDeclaringClass() != from.getClass()) {
                    m = from.getClass().getMethod("d", from.getClass());
                    m.setAccessible(true);
                    sdkDistanceMethod = m;
                }
                Object v = m.invoke(from, to);
                if (v instanceof Integer) {
                    int d = (Integer) v;
                    // 5초 간격 상태 로그로 SDK 거리와 distFromS 차이를 비교할 수 있게 남긴다.
                    KakaoHudLog.status("dist sdk=" + d + " fromS=" + (toAbs >= 0 && vehicleAbs >= 0 ? toAbs - vehicleAbs : -1));
                    return d;
                }
            } catch (Throwable t) {
                if (!sdkDistanceLogged) {
                    sdkDistanceLogged = true;
                    KakaoHudLog.ex("sdk distance", t);
                }
            }
        }
        return toAbs >= 0 && vehicleAbs >= 0 ? toAbs - vehicleAbs : toAbs;
    }

    private int kakaoDistance(Object from, Object to, int toAbs, int vehicleAbs) {
        int d = kakaoDistanceSigned(from, to, toAbs, vehicleAbs);
        return d < 0 && toAbs < 0 ? d : Math.max(0, d);
    }

    /** 방향 객체의 KNRGCode enum 이름. name() 은 난독화되지 않는다. */
    private String rgName(Object direction) {
        if (direction == null) return null;
        return enumName(tryCallAny(direction, "getRgCode", "g"));
    }

    /** 방향각(도). getDirectionAng 의 런타임 이름은 "b"(4.51.0 dex 확인). */
    private int dirAngle(Object direction) {
        if (direction == null) return -1;
        return getInt(direction, "getDirectionAng", "b");
    }

    private static String enumName(Object value) {
        return value instanceof Enum ? ((Enum<?>) value).name() : null;
    }

    private static int enumValue(Object value) {
        if (value == null) return -1;
        Object v = tryCall(value, "getValue");
        return v instanceof Number ? ((Number) v).intValue() : -1;
    }

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
