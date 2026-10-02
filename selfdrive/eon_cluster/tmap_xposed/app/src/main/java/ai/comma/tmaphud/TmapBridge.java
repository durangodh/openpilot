package ai.comma.tmaphud;

import android.os.SystemClock;

import java.lang.reflect.Constructor;
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
 *
 * 출처(11.8.3.4061 디컴파일 기준, com.skt.tmap.engine.navigation 은 난독화 없음):
 *  - NavigationManager.setLastRGData(RGData): 위치가 처리될 때마다 최종 RGData.
 *    RGData 필드는 네이티브 엔진이 이름으로 채우므로 이름이 유지된다.
 *    차량(vpPosPoint*, nPosAngle, nPosSpeed), 안내(stGuidePoint, stGuidePointNext),
 *    차로(nLane*), 제한속도·SDI(nRoadLimitSpeed, sdiInfo[]), 구간단속(sectionSpeedInfo),
 *    남은 거리/시간(nTotalDist, nTotalTime), 이탈(eRgStatus == 5).
 *  - NavigationManager 공개 getter: 주행 모드, 경로 유무, 재탐색 중, 도착.
 *  - TmapNavigationEngineInterface.getVertexArray(): 경로 좌표.
 *  - TrafficSignalInfoRepository.onSignalInfoChanged(TrafficSignalStateInfo): C-ITS 신호.
 *
 * 후킹 콜백에서는 참조만 저장하고, 문자열 만들기·전송은 전용 스레드(250ms)에서 한다.
 */
final class TmapBridge {
    private static final long TICK_MS = 250;
    // 값이 바뀌었을 때 + 1초 하트비트로만 보낸다(네이버 모듈과 같은 정책).
    private static final long HEARTBEAT_MS = 1000;
    private static final long ROUTE_MIN_INTERVAL_MS = 1000;
    private static final long POLYLINE_REFRESH_MS = 5000;
    // 이 시간 동안 RGData 가 안 오면 주행이 끝난 것으로 본다(EON STALE_TIMEOUT 3초와 같음).
    private static final long RG_STALE_MS = 3000;
    private static final int POLYLINE_MAX_POINTS = 500;
    private static final int RG_STATUS_BREAKAWAY = 5;

    private static final String[] ITEMS = {
            "vehicle", "guidance_current", "guidance_next", "lane_current", "speed",
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

    private volatile Object manager;
    private volatile Object rgData;
    private volatile long rgAt;
    private volatile Object signalRepository;
    private volatile Object signalInfo;
    private volatile long signalAt;
    private boolean started;

    private final Map<String, String> lastSent = new HashMap<>();
    private final Map<String, Long> lastSentAt = new HashMap<>();
    private boolean wasActive;
    private Object polylineRoute;
    private long polylineAt;
    private String polyline = "[]";
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

    /** 후킹 스레드(티맵 위치 처리 스레드). 참조만 저장한다. */
    void onRGData(Object navigationManager, Object rg) {
        if (rg == null) return;
        manager = navigationManager;
        rgData = rg;
        rgAt = SystemClock.elapsedRealtime();
        if (!loggedFirstRg) {
            loggedFirstRg = true;
            TmapHudLog.line("first RGData: " + rg);
        }
    }

    /** 후킹 스레드(신호 저장소 단일 스레드). 참조만 저장한다. */
    void onSignalInfo(Object repository, Object info) {
        signalRepository = repository;
        signalInfo = info;
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

        String mode = enumName(call(nav, "getDriveMode"));
        Object routeResult = call(nav, "getRouteResult");
        boolean routePresent = routeResult != null;
        boolean naviPlaying = "REAL_DRIVE".equals(mode) || "SIMULATION_DRIVE".equals(mode);
        boolean arrived = Boolean.TRUE.equals(call(nav, "getArrived"));
        boolean guiding = live && naviPlaying && routePresent && !arrived;
        int rgStatus = live ? intField(rg, "eRgStatus") : -1;
        boolean offRoute = guiding && (rgStatus == RG_STATUS_BREAKAWAY
                || Boolean.TRUE.equals(call(nav, "getRequestingReRoute")));

        send("navigation_status", TmapJson.status(guiding, routePresent,
                live ? (mode.isEmpty() ? "unknown" : mode.toLowerCase(java.util.Locale.ROOT)) : "idle",
                offRoute, arrived, rgStatus));

        if (!live) {
            if (wasActive) {
                // 주행이 끝났다: 남은 안내를 지우고 하트비트를 멈춘다.
                wasActive = false;
                for (String name : ITEMS) {
                    if ("navigation_status".equals(name)) continue;
                    // 서버는 traffic_signal 의 null 을 신호등 이미지 지우기로만 처리한다.
                    // JSON 신호 상태는 빈 목록으로 지운다.
                    sendNow(name, "traffic_signal".equals(name) ? TmapJson.emptySignal() : null);
                }
                signalImage.clear();
                images.clearAll();
                polylineRoute = null;
                polyline = "[]";
                TmapHudLog.line("RGData stale; guidance cleared");
            }
            return;
        }
        wasActive = true;

        double lat = doubleField(rg, "vpPosPointLat");
        double lon = doubleField(rg, "vpPosPointLon");
        if (lat != 0.0 && lon != 0.0) {
            send("vehicle", TmapJson.vehicle(lat, lon, intField(rg, "nPosAngle"),
                    intField(rg, "nPosSpeed"), stringField(rg, "szPosRoadName"),
                    intField(rg, "eVirtualGps") != 0));
        }

        Object cur = guiding ? field(rg, "stGuidePoint") : null;
        Object next = guiding ? field(rg, "stGuidePointNext") : null;
        String current = guidance(cur, -1);
        send("guidance_current", current);
        send("guidance_next", current == null ? null
                : guidance(next, TmapJson.nextSegmentDistance(intField(next, "nSvcLinkDist"),
                        intField(next, "nTBTDist"), intField(cur, "nTBTDist"))));

        send("lane_current", TmapJson.lane(intField(rg, "nLaneCount"), intField(rg, "nCurrentLane"),
                intField(rg, "nLaneDist"), intArrayField(rg, "nLaneTurnInfo"),
                intArrayField(rg, "nLaneAvailable"), intArrayField(rg, "nLaneEtcInfo"),
                intField(rg, "nLaneTurnCode"), intField(rg, "roadcate"), showLane(rg)));

        send("speed", speed(rg));
        publishSignal(now);
        images.update(rg, guiding,
                Boolean.TRUE.equals(call(call(nav, "getNaviConfigData"), "getNightMode")));

        if (guiding) {
            refreshPolyline(nav, routeResult, lat, lon, now);
            Object summary = call(nav, "getRouteSummaryInfo");
            send("route", TmapJson.route(intField(rg, "nTotalDist"), intField(rg, "nTotalTime"),
                    intField(summary, "nTotalDist"), polyline));
        } else {
            send("route", null);
        }
    }

    private static String guidance(Object tbt, int distanceOverride) {
        if (tbt == null) return null;
        int distance = distanceOverride >= 0 ? distanceOverride : intField(tbt, "nTBTDist");
        return TmapJson.guidance(intField(tbt, "nTBTTurnType"), distance, intField(tbt, "nTBTTime"),
                stringField(tbt, "szTBTMainText"), stringField(tbt, "szRoadName"),
                stringField(tbt, "szCrossName"), stringField(tbt, "szNearDirName"),
                stringField(tbt, "szMidDirName"), stringField(tbt, "szFarDirName"),
                doubleField(tbt, "vpTBTPointLat"), doubleField(tbt, "vpTBTPointLon"),
                booleanField(tbt, "isUnprotectedTurn"));
    }

    private static String speed(Object rg) {
        Object[] sdis = objectArrayField(rg, "sdiInfo");
        int count = Math.min(sdis == null ? 0 : sdis.length, Math.max(0, intField(rg, "sdiCount")));
        String primary = null, secondary = null;
        for (int i = 0; i < count && secondary == null; i++) {
            Object s = sdis[i];
            if (s == null) continue;
            String json = TmapJson.sdi(intField(s, "nSdiType"), intField(s, "nSdiDist"),
                    intField(s, "nSdiSpeedLimit"), intField(s, "nSdiBlockType"),
                    intField(s, "nSdiBlockDist"), intField(s, "nSdiBlockSpeed"),
                    intField(s, "nSdiBlockAverageSpeed"), booleanField(s, "bIsInSchoolZone"),
                    doubleField(s, "vpSdiPointLat"), doubleField(s, "vpSdiPointLon"));
            if (primary == null) primary = json;
            else secondary = json;
        }
        Object sec = field(rg, "sectionSpeedInfo");
        String section = null;
        if (sec != null && booleanField(sec, "isInSection")) {
            section = TmapJson.section(true, booleanField(sec, "isSuspended"),
                    booleanField(sec, "isOffRoute"), intField(sec, "speedLimit"),
                    doubleField(sec, "remainingDistance"), doubleField(sec, "averageSpeed"),
                    intField(sec, "remainingTime"));
        }
        return TmapJson.speed(intField(rg, "nRoadLimitSpeed"), primary, secondary, section);
    }

    // ---- 차로 표시 여부: 티맵 화면과 같은 판단(ObservableLaneData.getShowLane) ----

    private Constructor<?> laneDataCtor;
    private Method laneShow;
    private boolean laneShowResolved;

    private boolean showLane(Object rg) {
        if (!laneShowResolved) {
            laneShowResolved = true;
            try {
                ClassLoader loader = rg.getClass().getClassLoader();
                Class<?> type = loader.loadClass("com.skt.tmap.engine.navigation.livedata.ObservableLaneData");
                laneDataCtor = type.getConstructor(rg.getClass());
                laneShow = type.getMethod("getShowLane");
            } catch (Throwable error) {
                TmapHudLog.ex("lane show resolve", error);
            }
        }
        if (laneDataCtor != null && laneShow != null) {
            try {
                return Boolean.TRUE.equals(laneShow.invoke(laneDataCtor.newInstance(rg)));
            } catch (Throwable ignored) {
                // 아래 bLane 으로.
            }
        }
        return booleanField(rg, "bLane");
    }

    // ---- 신호등 ----

    private void publishSignal(long now) {
        Object info = signalInfo;
        Object repo = signalRepository;
        Object observable = call(call(repo, "getObservableTrafficSignalData"), "getValue");
        // Kotlin getter 실제 이름은 isTrafficSignalVisible()(jadx 는 getIs… 로 보여 준다).
        // 같은 이름의 필드를 바로 읽는다.
        boolean visible = booleanField(observable, "isTrafficSignalVisible");
        List<?> states = info == null ? null : asList(call(info, "getStates"));
        if (!visible || states == null || states.isEmpty()) {
            send("traffic_signal", TmapJson.emptySignal());
            signalImage.clear();
            return;
        }
        int n = states.size();
        int[] movements = new int[n], lights = new int[n], remains = new int[n];
        for (int i = 0; i < n; i++) {
            Object s = states.get(i);
            movements[i] = number(call(s, "getMovement"));
            lights[i] = number(call(s, "getLightState"));
            remains[i] = number(call(s, "getRemainTime"));
        }
        int elapsed = (int) ((now - signalAt) / 1000L);
        // 저장소가 1초마다 차량 속도만큼 줄이는 거리. 없으면 수신 당시 거리.
        int distance = repo != null && hasField(repo, "currentScheduledDistance")
                ? intField(repo, "currentScheduledDistance") : number(call(info, "getDistance"));
        send("traffic_signal", TmapJson.signal(distance, movements, lights, remains, elapsed));

        int idx = TmapJson.displayIndex(movements);
        if (idx < 0) {
            signalImage.clear();
        } else {
            signalImage.publish(TmapJson.lightColor(lights[idx]), Math.max(0, remains[idx] - elapsed));
        }
    }

    // ---- 경로 좌표 ----

    private Method convertTo;
    private Field pointX, pointY;

    private void refreshPolyline(Object nav, Object routeResult, double lat, double lon, long now) {
        if (routeResult == polylineRoute && now - polylineAt < POLYLINE_REFRESH_MS) return;
        polylineRoute = routeResult;
        polylineAt = now;
        try {
            Object engine = call(nav, "getTmapNavigationEngineInterface");
            Object raw = call(engine, "getVertexArray");
            if (!(raw instanceof Object[]) || ((Object[]) raw).length == 0) {
                polyline = "[]";
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
                Object w = convertTo.invoke(p, 0);  // COORDTYPE.WGS84
                double x = pointX.getDouble(w), y = pointY.getDouble(w);
                if (x == 0.0 || y == 0.0) continue;
                // 한국 경도(124~132)가 위도(33~39)보다 127 에 가깝다.
                boolean xIsLon = Math.abs(x - 127.0) < Math.abs(y - 127.0);
                lons[valid] = xIsLon ? x : y;
                lats[valid] = xIsLon ? y : x;
                valid++;
            }
            if (valid == 0) {
                polyline = "[]";
                return;
            }
            // 지나온 구간은 뺀다: 차량에서 가장 가까운 점 직전부터 목적지까지.
            int start = 0;
            if (lat != 0.0 && lon != 0.0) {
                double best = Double.MAX_VALUE;
                double k = Math.cos(Math.toRadians(lat));
                for (int i = 0; i < valid; i++) {
                    double dy = lats[i] - lat, dx = (lons[i] - lon) * k;
                    double d = dx * dx + dy * dy;
                    if (d < best) {
                        best = d;
                        start = i;
                    }
                }
                start = Math.max(0, start - 2);
            }
            int remain = valid - start;
            int stride = Math.max(1, (remain + POLYLINE_MAX_POINTS - 2) / (POLYLINE_MAX_POINTS - 1));
            double[] outLat = new double[POLYLINE_MAX_POINTS + 1], outLon = new double[POLYLINE_MAX_POINTS + 1];
            int out = 0;
            for (int i = start; i < valid && out < POLYLINE_MAX_POINTS; i += stride) {
                outLat[out] = lats[i];
                outLon[out] = lons[i];
                out++;
            }
            // 목적지(마지막 점)는 항상 넣는다. 서버가 경로 변경 판단에 쓴다.
            if (outLat[out - 1] != lats[valid - 1] || outLon[out - 1] != lons[valid - 1]) {
                outLat[out] = lats[valid - 1];
                outLon[out] = lons[valid - 1];
                out++;
            }
            polyline = TmapJson.polyline(outLat, outLon, out);
            if (!loggedPolyline) {
                loggedPolyline = true;
                TmapHudLog.line("route vertices=" + total + " sent=" + out + " first=("
                        + lats[0] + "," + lons[0] + ")");
            }
        } catch (Throwable error) {
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

    private static boolean hasField(Object target, String name) {
        return target != null && findField(target.getClass(), name) != null;
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
