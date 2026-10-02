package ai.comma.tmaphud;

import android.os.SystemClock;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * 후킹으로 받은 티맵 내비 엔진 데이터를 carrot_navi_server JSON 항목으로 보낸다.
 * 값과 조건은 캐롯 패치판(CarrotNavi v11.2.3.3740 CarrotUiStateData/CarrotUtilj)과 같다.
 *
 * 출처(11.8.3.4061 디컴파일 기준, com.skt.tmap.engine.navigation 은 난독화 없음):
 *  - NavigationManager.setLastRGData(RGData): 패치판 postOpaKrRgdata 와 같은 RGData.
 *    RGData 필드는 네이티브 엔진이 이름으로 채우므로 이름이 유지된다.
 *  - TmapNavigationEngineInterface.getVertexArray(): 경로 좌표(패치판 postOpaKrvrtx).
 *  - TrafficSignalInfoRepository.onSignalInfoChanged(TrafficSignalStateInfo):
 *    방향별 신호(패치판 postOpaKrSSinf)와 저장소가 계산한 currentTrafficSignalInfo
 *    (패치판 postOpaKrSinf).
 *
 * 후킹 콜백에서는 참조만 저장하고, 문자열 만들기·전송은 전용 스레드(250ms)에서 한다.
 */
final class TmapBridge {
    private static final long TICK_MS = 250;
    // 서버 manifest: on_change_with_heartbeat, interval_ms 500.
    private static final long HEARTBEAT_MS = 500;
    private static final long ROUTE_MIN_INTERVAL_MS = 1000;
    // 이 시간 동안 RGData 가 안 오면 주행이 끝난 것으로 본다(EON STALE_TIMEOUT 3초와 같음).
    private static final long RG_STALE_MS = 3000;
    private static final int MAX_AHEAD_LANES = 4;

    private static final String[] ITEMS = {
            "vehicle", "guidance_current", "guidance_next", "lane_current", "lane_ahead", "speed",
            "traffic_signal", "route", "navigation_status",
    };

    private final TmapNaviClient client;
    private final TmapSignal signalImage;
    private final TmapImages images;
    private final ScheduledExecutorService poller = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "tmap-hud-state");
        t.setDaemon(true);
        return t;
    });

    private volatile Runnable onLive;
    private volatile Object manager;
    private volatile Object rgData;
    private volatile long rgAt;

    /** 안내(경로 안내 또는 안심주행) 데이터가 최근에 들어왔는지. */
    boolean guidanceLive() {
        return rgData != null && SystemClock.elapsedRealtime() - rgAt <= RG_STALE_MS;
    }
    private volatile Object signalInfo;
    private volatile Object signalLights;   // TrafficSignalInfo(저장소 계산 결과)
    private volatile long signalAt;
    private boolean started;

    private final Map<String, String> lastSent = new HashMap<>();
    private final Map<String, Long> lastSentAt = new HashMap<>();
    private boolean wasActive;
    private Object polylineRoute;
    private String polyline;
    private boolean loggedFirstRg, loggedFirstSignal, loggedPolyline;

    TmapBridge(TmapNaviClient client, TmapSignal signalImage, TmapImages images) {
        this.client = client;
        this.signalImage = signalImage;
        this.images = images;
    }

    synchronized void start() {
        if (started) return;
        started = true;
        poller.scheduleWithFixedDelay(this::tick, TICK_MS, TICK_MS, TimeUnit.MILLISECONDS);
        TmapHudLog.line("state polling started");
    }

    Object navigationManager() {
        return manager;
    }

    /** 주행 데이터가 살아 있는 틱마다 부른다(지도 렌더 시작용). */
    void setOnLive(Runnable callback) {
        onLive = callback;
    }

    /** 후킹 스레드(티맵 위치 처리 스레드). 참조만 저장한다. */
    void onRGData(Object navigationManager, Object rg) {
        if (rg == null) return;
        manager = navigationManager;
        rgData = rg;
        rgAt = SystemClock.elapsedRealtime();
        if (!loggedFirstRg) {
            loggedFirstRg = true;
            TmapHudLog.xposed("first RGData: " + rg);
        }
    }

    /** 후킹 스레드(신호 저장소 단일 스레드, 원래 메서드 실행 뒤). 참조만 저장한다. */
    void onSignalInfo(Object repository, Object info) {
        signalInfo = info;
        signalLights = field(repository, "currentTrafficSignalInfo");
        signalAt = SystemClock.elapsedRealtime();
        if (!loggedFirstSignal && info != null) {
            loggedFirstSignal = true;
            TmapHudLog.line("first traffic signal: " + info);
        }
    }

    private void tick() {
        if (!client.ready()) return;
        try {
            publish();
        } catch (Throwable error) {
            TmapHudLog.ex("state tick", error);
        }
    }

    private void publish() {
        long now = SystemClock.elapsedRealtime();
        Object rg = rgData;
        Object nav = manager;
        boolean live = rg != null && now - rgAt <= RG_STALE_MS;

        if (!live) {
            if (wasActive) {
                // 주행이 끝났다: 남은 안내를 지우고 하트비트를 멈춘다.
                wasActive = false;
                for (String name : ITEMS) {
                    if ("navigation_status".equals(name)) continue;
                    sendNow(name, null);
                }
                signalImage.clear();
                images.clearAll();
                polylineRoute = null;
                polyline = null;
                TmapHudLog.line("RGData stale; guidance cleared");
            }
            send("navigation_status", TmapJson.status(false, false, false));
            return;
        }
        wasActive = true;
        Runnable liveHook = onLive;
        if (liveHook != null) liveHook.run();

        double lat = doubleField(rg, "vpPosPointLat");
        double lon = doubleField(rg, "vpPosPointLon");
        int speedKph = intField(rg, "nPosSpeed");
        String vehicle = TmapJson.vehicle(lat, lon, intField(rg, "nPosAngle"), speedKph,
                stringField(rg, "szPosRoadName"), intField(rg, "eVirtualGps") != 0);
        send("vehicle", vehicle);

        // Guidance.from: 현재/다음 안내·차로·앞 차로가 모두 없으면 안내 없음.
        String current = guidePoint(field(rg, "stGuidePoint"));
        String next = guidePoint(field(rg, "stGuidePointNext"));
        String lane = TmapJson.lane(intField(rg, "nLaneCount"), intField(rg, "nLaneDist"),
                booleanField(rg, "bLane"), booleanField(rg, "bLanePlay"), intField(rg, "nCurrentLane"),
                intField(rg, "nLaneTurnCode"), intArrayField(rg, "nLaneTurnInfo"),
                intArrayField(rg, "nLaneEtcInfo"), intArrayField(rg, "nLaneAvailable"),
                intField(rg, "guideLineColor"), intField(rg, "roadcate"));
        String ahead = aheadLanes(objectArrayField(rg, "aheadLaneInfoData"));
        boolean guidance = current != null || next != null || lane != null || ahead != null;
        send("guidance_current", current);
        send("guidance_next", next);
        send("lane_current", lane);
        send("lane_ahead", ahead);

        String speed = speed(rg, lat, lon, speedKph);
        send("speed", speed);

        Object routeResult = call(nav, "getRouteResult");
        refreshPolyline(nav, routeResult);
        String route = TmapJson.route(intField(rg, "nTotalDist"), intField(rg, "nTotalTime"),
                intField(rg, "nAccDist"), intField(rg, "nAccTime"),
                intField(rg, "roadLengthAllRoute"), polyline);
        send("route", route);

        // getV2JsonSnapshot: 이탈은 구간단속 정보의 off_route 로만 판단한다.
        Object sec = field(rg, "sectionSpeedInfo");
        boolean offRoute = sec != null && booleanField(sec, "isOffRoute");
        boolean guiding = route != null || guidance;
        send("navigation_status", TmapJson.status(guiding, offRoute, route != null));

        publishSignal(now, lat, lon, speedKph);
        images.update(rg, guiding,
                Boolean.TRUE.equals(call(call(nav, "getNaviConfigData"), "getNightMode")));
    }

    private static String guidePoint(Object tbt) {
        if (tbt == null) return null;
        return TmapJson.guidePoint(intField(tbt, "nTBTDist"), intField(tbt, "nTBTTime"),
                intField(tbt, "nTBTTurnType"), stringField(tbt, "szRoadName"),
                stringField(tbt, "szTBTMainText"), stringField(tbt, "szNearDirName"),
                stringField(tbt, "szMidDirName"), stringField(tbt, "szFarDirName"),
                doubleField(tbt, "vpTBTPointLat"), doubleField(tbt, "vpTBTPointLon"));
    }

    /** Lane.fromAhead: 앞 차로 최대 4개(LaneInfoData 의 Kotlin getter). */
    private static String aheadLanes(Object[] data) {
        if (data == null || data.length == 0) return null;
        int n = Math.min(data.length, MAX_AHEAD_LANES);
        String[] lanes = new String[n];
        for (int i = 0; i < n; i++) {
            Object d = data[i];
            if (d == null) continue;
            lanes[i] = TmapJson.aheadLane(number(call(d, "getNLaneCount")), number(call(d, "getNLaneDist")),
                    Boolean.TRUE.equals(call(d, "getBLanePlay")), number(call(d, "getNLaneTurnCode")),
                    ints(call(d, "getNLaneTurnInfo")), ints(call(d, "getNLaneEtcInfo")),
                    ints(call(d, "getNLaneAvailable")), number(call(d, "getGuideLineColor")),
                    number(call(d, "getRoadCate")), number(call(d, "getVoiceCode")));
        }
        return TmapJson.array(lanes);
    }

    /** Speed.from: sdiInfo[0]/[1], 없으면 SDI+ 로 대신. */
    private static String speed(Object rg, double lat, double lon, int speedKph) {
        Object[] sdis = objectArrayField(rg, "sdiInfo");
        String first = sdi(sdis, 0);
        String second = sdi(sdis, 1);
        String plus = booleanField(rg, "bSDIPlus") ? TmapJson.sdi(intField(rg, "nSdiPlusType"),
                intField(rg, "nSdiPlusDist"), intField(rg, "nSdiPlusSpeedLimit"),
                intField(rg, "nSdiPlusSection"), intField(rg, "nSdiPlusBlockType"),
                intField(rg, "nSdiPlusBlockSpeed"), intField(rg, "nSdiPlusBlockDist"),
                intField(rg, "nSdiPlusBlockAverageSpeed"), intField(rg, "nSdiPlusBlockTime"),
                doubleField(rg, "vpSdiPlusPointLat"), doubleField(rg, "vpSdiPlusPointLon")) : null;
        String primary = first != null ? first : plus;
        String secondary = second != null ? second : (first != null ? plus : null);
        Object sec = field(rg, "sectionSpeedInfo");
        String section = sec == null ? null : TmapJson.section(booleanField(sec, "isInSection"),
                booleanField(sec, "isSuspended"), booleanField(sec, "isOffRoute"),
                intField(sec, "speedLimit"), doubleField(sec, "averageSpeed"),
                doubleField(sec, "overallAverageSpeed"), doubleField(sec, "remainingDistance"),
                intField(sec, "remainingTime"), doubleField(sec, "sectionProgress"));
        boolean hasPosition = !(lat == 0.0 && lon == 0.0);
        Integer current = hasPosition || speedKph > 0 ? Integer.valueOf(Math.max(0, speedKph)) : null;
        return TmapJson.speed(current, TmapJson.validRoadLimitKph(intField(rg, "nRoadLimitSpeed")),
                primary, secondary, section);
    }

    private static String sdi(Object[] sdis, int index) {
        if (sdis == null || sdis.length <= index || sdis[index] == null) return null;
        Object s = sdis[index];
        return TmapJson.sdi(intField(s, "nSdiType"), intField(s, "nSdiDist"),
                intField(s, "nSdiSpeedLimit"), intField(s, "nSdiSection"),
                intField(s, "nSdiBlockType"), intField(s, "nSdiBlockSpeed"),
                intField(s, "nSdiBlockDist"), intField(s, "nSdiBlockAverageSpeed"),
                intField(s, "nSdiBlockTime"), doubleField(s, "vpSdiPointLat"),
                doubleField(s, "vpSdiPointLon"));
    }

    // ---- 신호등 ----

    private static final String[] LIGHT_FIELDS = {"Red", "Left", "Green", "Right", "UTurn"};

    private void publishSignal(long now, double vehicleLat, double vehicleLon, int speedKph) {
        Object info = signalInfo;
        Object sinf = signalLights;
        long ageMs = now - signalAt;
        List<?> states = info == null ? null : asList(call(info, "getStates"));
        if (info == null && sinf == null) {
            send("traffic_signal", null);
            signalImage.clear();
            return;
        }
        boolean[] on = null;
        int[] onRemain = null;
        if (sinf != null) {
            on = new boolean[LIGHT_FIELDS.length];
            onRemain = new int[LIGHT_FIELDS.length];
            for (int i = 0; i < LIGHT_FIELDS.length; i++) {
                on[i] = booleanField(sinf, "is" + LIGHT_FIELDS[i] + "LightOn");
                String remain = LIGHT_FIELDS[i].equals("UTurn") ? "uTurn" : LIGHT_FIELDS[i].toLowerCase(java.util.Locale.ROOT);
                onRemain[i] = intField(sinf, remain + "LightRemainTime");
            }
        }
        int n = states == null ? 0 : states.size();
        int[] movements = new int[n], lights = new int[n], remains = new int[n];
        for (int i = 0; i < n; i++) {
            Object s = states.get(i);
            movements[i] = number(call(s, "getMovement"));
            lights[i] = number(call(s, "getLightState"));
            remains[i] = number(call(s, "getRemainTime"));
        }
        // 패치판 TrafficSignal: 기준 거리(수신 당시)와 신호 위치까지 거리 중 작은 값.
        Object location = sinf != null ? field(sinf, "location") : null;
        if (location == null && info != null) location = field(info, "location");
        double sLat = location == null ? 0.0 : doubleValue(call(location, "getLatitude"));
        double sLon = location == null ? 0.0 : doubleValue(call(location, "getLongitude"));
        int base = sinf != null ? intField(sinf, "distance") : number(call(info, "getDistance"));
        int elapsed = (int) (Math.max(0L, ageMs) / 1000L);
        int byTime = TmapJson.distanceAfterElapsed(base, speedKph, elapsed);
        int byPoint = TmapJson.distanceMeters(vehicleLat, vehicleLon, sLat, sLon);
        int distance = byPoint >= 0 ? Math.min(byPoint, byTime) : byTime;
        String json = TmapJson.signal(on, onRemain, n > 0 ? movements : null, lights, remains,
                distance, sLat, sLon, ageMs);
        send("traffic_signal", json);

        int idx = json == null ? -1 : TmapJson.displayIndex(movements);
        int color = idx < 0 ? TmapJson.COLOR_NONE : TmapJson.lightColor(lights[idx]);
        if (color != TmapJson.COLOR_NONE) {
            signalImage.publish(color, TmapJson.subtractElapsed(remains[idx], elapsed));
        } else if (json != null && on != null && distance > 5) {
            // 직진 신호가 없거나 방향이 여럿인 교차로: 폰 신호등과 같은 규칙
            // (TrafficSignalInfoRepository.getCurrentRemainTime)으로 저장소의 합친 값을 쓴다.
            int[] phone = TmapJson.phoneLight(on, onRemain);
            if (phone == null) {
                signalImage.clear();
            } else {
                signalImage.publish(phone[0], TmapJson.subtractElapsed(phone[1], elapsed));
            }
        } else {
            signalImage.clear();
        }
    }

    // ---- 경로 좌표 ----

    private Method convertTo;
    private Field pointX, pointY;

    /** 경로가 바뀔 때만 다시 읽는다(패치판 postOpaKrvrtx/ensureRoutePolyline). */
    private void refreshPolyline(Object nav, Object routeResult) {
        if (routeResult == null) {
            polylineRoute = null;
            polyline = null;
            return;
        }
        if (routeResult == polylineRoute && polyline != null) return;
        polylineRoute = routeResult;
        try {
            Object engine = call(nav, "getTmapNavigationEngineInterface");
            Object raw = call(engine, "getVertexArray");
            if (!(raw instanceof Object[]) || ((Object[]) raw).length == 0) {
                polyline = null;
                return;
            }
            Object[] points = (Object[]) raw;
            int total = points.length;
            double[] lats = new double[total], lons = new double[total];
            int valid = 0;
            for (Object p : points) {
                if (p == null) continue;
                if (convertTo == null) {
                    convertTo = p.getClass().getMethod("convertTo", int.class);
                    pointX = p.getClass().getField("x");
                    pointY = p.getClass().getField("y");
                }
                Object w = convertTo.invoke(p, 0);  // COORDTYPE.WGS84: x=경도, y=위도
                if (w == null) w = p;
                double x = pointX.getDouble(w), y = pointY.getDouble(w);
                if (x == 0.0 && y == 0.0) continue;
                lons[valid] = x;
                lats[valid] = y;
                valid++;
            }
            polyline = TmapJson.polyline(lats, lons, valid);
            if (!loggedPolyline && valid > 0) {
                loggedPolyline = true;
                TmapHudLog.line("route vertices=" + total + " first=(" + lats[0] + "," + lons[0] + ")");
            }
        } catch (Throwable error) {
            polyline = null;
            TmapHudLog.ex("polyline", error);
        }
    }

    // ---- 전송(변경 + 하트비트) ----

    private void send(String name, String value) {
        long now = SystemClock.elapsedRealtime();
        String prev = lastSent.get(name);
        Long at = lastSentAt.get(name);
        boolean changed = at == null || (prev == null ? value != null : !prev.equals(value));
        boolean heartbeat = at == null || now - at >= HEARTBEAT_MS;
        if (value == null && !changed) return;   // 없는 항목은 바뀔 때 한 번만.
        if ("route".equals(name) && at != null && now - at < ROUTE_MIN_INTERVAL_MS) return;
        if (!changed && !heartbeat) return;
        lastSent.put(name, value);
        lastSentAt.put(name, now);
        client.sendState(name, value);
    }

    private void sendNow(String name, String value) {
        lastSent.put(name, value);
        lastSentAt.put(name, SystemClock.elapsedRealtime());
        client.sendState(name, value);
    }

    // ---- 리플렉션 ----

    private static final Map<String, Field> FIELDS = new ConcurrentHashMap<>();
    private static final Field MISSING;
    private static final Map<String, Method> METHODS = new ConcurrentHashMap<>();
    private static final Method NO_METHOD;

    static {
        try {
            MISSING = TmapBridge.class.getDeclaredField("FIELDS");
            NO_METHOD = Object.class.getMethod("hashCode");
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    private static Field findField(Class<?> type, String name) {
        String key = type.getName() + '#' + name;
        Field cached = FIELDS.get(key);
        if (cached != null) return cached == MISSING ? null : cached;
        Field found = null;
        for (Class<?> t = type; t != null && found == null; t = t.getSuperclass()) {
            try {
                found = t.getDeclaredField(name);
                found.setAccessible(true);
            } catch (Throwable ignored) {
                found = null;
            }
        }
        FIELDS.put(key, found == null ? MISSING : found);
        if (found == null) TmapHudLog.line("field missing: " + key);
        return found;
    }

    static Object field(Object target, String name) {
        if (target == null) return null;
        Field f = findField(target.getClass(), name);
        if (f == null) return null;
        try {
            return f.get(target);
        } catch (Throwable ignored) {
            return null;
        }
    }

    static int intField(Object target, String name) {
        return number(field(target, name));
    }

    static double doubleField(Object target, String name) {
        Object v = field(target, name);
        return v instanceof Number ? ((Number) v).doubleValue() : 0.0;
    }

    static boolean booleanField(Object target, String name) {
        return Boolean.TRUE.equals(field(target, name));
    }

    static String stringField(Object target, String name) {
        Object v = field(target, name);
        return v instanceof String ? (String) v : "";
    }

    static int[] intArrayField(Object target, String name) {
        Object v = field(target, name);
        return v instanceof int[] ? (int[]) v : null;
    }

    static Object[] objectArrayField(Object target, String name) {
        Object v = field(target, name);
        return v instanceof Object[] ? (Object[]) v : null;
    }

    static double doubleValue(Object v) {
        return v instanceof Number ? ((Number) v).doubleValue() : 0.0;
    }

    static int[] ints(Object v) {
        return v instanceof int[] ? (int[]) v : null;
    }

    static int number(Object v) {
        return v instanceof Number ? ((Number) v).intValue() : 0;
    }

    static Object call(Object target, String name) {
        if (target == null) return null;
        String key = target.getClass().getName() + '#' + name;
        Method m = METHODS.get(key);
        if (m == null) {
            m = NO_METHOD;
            for (Class<?> t = target.getClass(); t != null && m == NO_METHOD; t = t.getSuperclass()) {
                try {
                    Method found = t.getDeclaredMethod(name);
                    found.setAccessible(true);
                    m = found;
                } catch (Throwable ignored) {
                    // 상위 클래스에서 계속 찾는다.
                }
            }
            if (m == NO_METHOD) {
                try {
                    m = target.getClass().getMethod(name);  // 인터페이스 기본 메서드 등
                    m.setAccessible(true);
                } catch (Throwable ignored) {
                    TmapHudLog.line("method missing: " + key);
                }
            }
            METHODS.put(key, m);
        }
        if (m == NO_METHOD) return null;
        try {
            return m.invoke(target);
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static String enumName(Object value) {
        return value instanceof Enum ? ((Enum<?>) value).name() : "";
    }

    private static List<?> asList(Object value) {
        return value instanceof List ? (List<?>) value : null;
    }
}
