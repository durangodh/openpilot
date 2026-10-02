package ai.comma.tmaphud;

import java.util.Locale;

/**
 * carrot_navi_server 의 JSON 항목 값을 캐롯 패치판(CarrotNavi v11.2.3.3740,
 * CarrotUiStateData)과 같은 이름·조건으로 만든다. 패치판은 Jackson NON_NULL 이라
 * 값이 없는 필드는 빠진다. 여기서도 null 은 넣지 않는다.
 *
 * Android/Xposed 에 의존하지 않아 호스트에서 검사한다(tests/.../TmapJsonCheck.java).
 * turn_type·sdi type 은 티맵 원래 코드다.
 */
final class TmapJson {
    // HUD 신호등 그림 색(TmapSignal 이 그린다).
    static final int COLOR_NONE = 0, COLOR_RED = 1, COLOR_YELLOW = 2, COLOR_GREEN = 3;
    // CarrotUiStateData 상수
    static final int MAX_LANE_COUNT = 16;
    static final int MAX_ROAD_LIMIT_KPH = 200;
    static final int ENCODED_ROAD_LIMIT_SCALE = 10;
    static final long MAX_TRAFFIC_SIGNAL_AGE_MS = 60000;
    static final int TRAFFIC_SIGNAL_PASS_DISTANCE_M = 5;
    static final int ROUTE_MAX_POINTS = 256;

    private TmapJson() {
    }

    /** null 값을 건너뛰는 작은 JSON 객체 빌더(Jackson NON_NULL 과 같은 결과). */
    static final class Obj {
        private final StringBuilder sb = new StringBuilder("{");
        private boolean empty = true;

        private Obj key(String name) {
            if (!empty) sb.append(',');
            empty = false;
            sb.append('"').append(name).append("\":");
            return this;
        }

        Obj num(String name, Integer value) {
            if (value != null) key(name).sb.append(value.intValue());
            return this;
        }

        Obj num(String name, Long value) {
            if (value != null) key(name).sb.append(value.longValue());
            return this;
        }

        Obj dbl(String name, Double value) {
            if (value != null) key(name).sb.append(decimal(value));
            return this;
        }

        Obj bool(String name, Boolean value) {
            if (value != null) key(name).sb.append(value.booleanValue());
            return this;
        }

        Obj str(String name, String value) {
            if (value != null) key(name).sb.append(quote(value));
            return this;
        }

        Obj raw(String name, String json) {
            if (json != null) key(name).sb.append(json);
            return this;
        }

        Obj ints(String name, int[] values) {
            if (values != null) key(name).sb.append(TmapJson.ints(values, values.length));
            return this;
        }

        boolean isEmpty() {
            return empty;
        }

        String build() {
            return sb.toString() + "}";
        }
    }

    static Integer positive(int value) {
        return value > 0 ? Integer.valueOf(value) : null;
    }

    static Integer nonZero(int value) {
        return value != 0 ? Integer.valueOf(value) : null;
    }

    static String nullIfEmpty(String value) {
        return value == null || value.isEmpty() ? null : value;
    }

    /** Point.from: 위경도가 둘 다 0 이면 없음. */
    static String point(double lat, double lon) {
        if (lat == 0.0 && lon == 0.0) return null;
        return new Obj().dbl("lat", lat).dbl("lon", lon).build();
    }

    static String vehicle(double lat, double lon, int headingDeg, int speedKph,
                          String roadName, boolean virtualGps) {
        if (lat == 0.0 && lon == 0.0) return null;
        return new Obj().dbl("lat", lat).dbl("lon", lon)
                .num("heading_deg", headingDeg)
                .num("speed_kph", Math.max(0, speedKph))
                .str("road_name", nullIfEmpty(roadName))
                .bool("virtual_gps", virtualGps).build();
    }

    /** GuidePoint.from(TBTInfo). 모든 값이 비면 null. distance_m 은 차량 기준 nTBTDist. */
    static String guidePoint(int distanceM, int timeSec, int turnType, String roadName,
                             String mainText, String nearDir, String midDir, String farDir,
                             double lat, double lon) {
        String pt = point(lat, lon);
        Integer dist = positive(distanceM), time = positive(timeSec);
        String road = nullIfEmpty(roadName), main = nullIfEmpty(mainText);
        String near = nullIfEmpty(nearDir), mid = nullIfEmpty(midDir), far = nullIfEmpty(farDir);
        if (dist == null && time == null && turnType == 0 && road == null && main == null
                && near == null && mid == null && far == null && pt == null) {
            return null;
        }
        return new Obj().num("distance_m", dist).num("time_sec", time).num("turn_type", turnType)
                .str("road_name", road).str("main_text", main)
                .str("near_direction", near).str("mid_direction", mid).str("far_direction", far)
                .raw("point", pt).build();
    }

    /** Lane.laneCount: 요청 개수와 배열 길이 중 큰 값, 최대 16. */
    static int laneCount(int requested, int[] turnInfo, int[] available) {
        int count = Math.max(0, requested);
        if (turnInfo != null) count = Math.max(count, turnInfo.length);
        if (available != null) count = Math.max(count, available.length);
        return Math.min(count, MAX_LANE_COUNT);
    }

    static int[] copyLane(int[] values, int count) {
        if (values == null) return null;
        int[] out = new int[count];
        System.arraycopy(values, 0, out, 0, Math.min(count, values.length));
        return out;
    }

    /** Lane.from(RGData): 현재 차로. */
    static String lane(int laneCount, int distanceM, boolean visible, boolean lanePlay,
                       int currentLane, int turnCode, int[] turnInfo, int[] etcInfo,
                       int[] available, int guideLineColor, int roadCategory) {
        int count = laneCount(laneCount, turnInfo, available);
        if (count <= 0) return null;
        return new Obj().num("count", count).num("distance_m", distanceM)
                .bool("visible", visible).bool("lane_play", lanePlay)
                .num("current_lane", positive(currentLane)).num("turn_code", nonZero(turnCode))
                .ints("turn_info", copyLane(turnInfo, count)).ints("etc_info", copyLane(etcInfo, count))
                .ints("available", copyLane(available, count))
                .num("guide_line_color", nonZero(guideLineColor))
                .num("road_category", roadCategory).build();
    }

    /** Lane.from(LaneInfoData): 앞 차로(lane_ahead 원소). */
    static String aheadLane(int laneCount, int distanceM, boolean lanePlay, int turnCode,
                            int[] turnInfo, int[] etcInfo, int[] available, int guideLineColor,
                            int roadCategory, int voiceCode) {
        int count = laneCount(laneCount, turnInfo, available);
        if (count <= 0) return null;
        return new Obj().num("count", count).num("distance_m", distanceM)
                .bool("lane_play", lanePlay).num("turn_code", nonZero(turnCode))
                .ints("turn_info", copyLane(turnInfo, count)).ints("etc_info", copyLane(etcInfo, count))
                .ints("available", copyLane(available, count))
                .num("guide_line_color", nonZero(guideLineColor))
                .num("road_category", nonZero(roadCategory))
                .num("voice_code", nonZero(voiceCode)).build();
    }

    /** 앞 차로 목록(최대 4개). 비면 null. */
    static String array(String[] items) {
        if (items == null) return null;
        StringBuilder out = new StringBuilder("[");
        int n = 0;
        for (String item : items) {
            if (item == null) continue;
            if (n++ > 0) out.append(',');
            out.append(item);
        }
        return n == 0 ? null : out.append(']').toString();
    }

    /** Sdi.from(SDIInfo) / fromSdiPlus. 모든 값이 비면 null. */
    static String sdi(int type, int distanceM, int speedLimitKph, int sectionType, int blockType,
                      int blockSpeedKph, int blockDistanceM, int blockAverageKph, int blockTimeSec,
                      double lat, double lon) {
        Obj o = new Obj().num("type", type >= 0 ? Integer.valueOf(type) : null)
                .num("distance_m", positive(distanceM))
                .num("speed_limit_kph", positive(speedLimitKph))
                .num("section_type", positive(sectionType))
                .num("block_type", positive(blockType))
                .num("block_speed_kph", positive(blockSpeedKph))
                .num("block_distance_m", positive(blockDistanceM))
                .num("block_average_kph", positive(blockAverageKph))
                .num("block_time_sec", positive(blockTimeSec))
                .raw("point", point(lat, lon));
        return o.isEmpty() ? null : o.build();
    }

    /** Section.from(SectionSpeedInfo). 값이 하나도 없으면 null. */
    static String section(boolean inSection, boolean suspended, boolean offRoute, int speedLimit,
                          double averageSpeed, double overallAverageSpeed, double remainingDistance,
                          int remainingTime, double progress) {
        boolean hasData = inSection || suspended || offRoute || speedLimit > 0 || averageSpeed > 0
                || overallAverageSpeed > 0 || remainingDistance > 0 || remainingTime > 0 || progress > 0;
        if (!hasData) return null;
        return new Obj().bool("active", inSection).num("speed_limit_kph", speedLimit)
                .dbl("average_kph", averageSpeed).dbl("overall_average_kph", overallAverageSpeed)
                .dbl("remaining_distance_m", remainingDistance).num("remaining_time_sec", remainingTime)
                .dbl("progress", progress).bool("suspended", suspended).bool("off_route", offRoute).build();
    }

    /** CarrotUiStateData.validRoadLimitKph: 200 초과 값은 (값-20)/10 으로 푼다. */
    static Integer validRoadLimitKph(int raw) {
        if (raw <= 0) return null;
        int kph = raw;
        if (raw > MAX_ROAD_LIMIT_KPH) {
            int encoded = raw - 20;
            if (encoded <= 0 || encoded % ENCODED_ROAD_LIMIT_SCALE != 0) return null;
            kph = encoded / ENCODED_ROAD_LIMIT_SCALE;
        }
        if (kph <= 0 || kph > MAX_ROAD_LIMIT_KPH || kph % ENCODED_ROAD_LIMIT_SCALE != 0) return null;
        return kph;
    }

    /** Speed.from(RGData). 모두 없으면 null. */
    static String speed(Integer currentKph, Integer roadLimitKph, String sdi, String sdiSecondary,
                        String section) {
        if (currentKph == null && roadLimitKph == null && sdi == null && sdiSecondary == null && section == null) {
            return null;
        }
        return new Obj().num("current_kph", currentKph).num("road_limit_kph", roadLimitKph)
                .raw("sdi", sdi).raw("sdi_secondary", sdiSecondary).raw("section", section).build();
    }

    /** Route.from(RGData) + 경로 좌표. 모두 없으면 null. */
    static String route(int remainDistanceM, int remainTimeSec, int movedDistanceM, int movedTimeSec,
                        int totalDistanceM, String polylineJson) {
        Obj o = new Obj().num("remain_distance_m", positive(remainDistanceM))
                .num("remain_time_sec", positive(remainTimeSec))
                .num("moved_distance_m", positive(movedDistanceM))
                .num("moved_time_sec", positive(movedTimeSec))
                .num("total_distance_m", positive(totalDistanceM))
                .raw("polyline", polylineJson);
        return o.isEmpty() ? null : o.build();
    }

    /** CarrotUtilj.getV2JsonSnapshot 의 navigation_status. */
    static String status(boolean guiding, boolean offRoute, boolean routePresent) {
        return new Obj().str("mode", offRoute ? "off_route" : guiding ? "guiding" : "idle")
                .bool("guidance_active", guiding).bool("off_route", offRoute)
                .bool("route_present", routePresent).build();
    }

    // ---- 신호등(TrafficSignal) ------------------------------------------------

    // TrafficSignalInfo.MOVEMENT_* (티맵 C-ITS)
    static final int MOVE_STRAIGHT = 1, MOVE_LEFT = 2, MOVE_PEDESTRIAN = 3, MOVE_BICYCLE = 4,
            MOVE_RIGHT = 5, MOVE_BUS = 6, MOVE_UTURN = 7;

    /** 패치판 Movements 필드 이름. */
    static String movementName(int movement) {
        switch (movement) {
            case MOVE_STRAIGHT: return "straight";
            case MOVE_LEFT: return "left";
            case MOVE_PEDESTRIAN: return "pedestrian";
            case MOVE_BICYCLE: return "bicycle";
            case MOVE_RIGHT: return "right";
            case MOVE_BUS: return "bus";
            case MOVE_UTURN: return "uturn";
            default: return null;
        }
    }

    static boolean isVehicleMovement(int movement) {
        return movement == MOVE_STRAIGHT || movement == MOVE_LEFT || movement == MOVE_RIGHT
                || movement == MOVE_UTURN;
    }

    /** TrafficSignalInfo.EVENT_STATE_* 이름. */
    static String lightStateName(int state) {
        switch (state) {
            case 1: return "dark";
            case 2: return "flashing-red";
            case 3: return "red";
            case 5: return "green-permissive";
            case 6: return "green";
            case 7: return "flashing-yellow";
            case 8: return "yellow";
            case 9: return "conflicting";
            default: return "unknown";
        }
    }

    /** HUD 신호등 그림 색. 티맵 isGreenLight(5|6), isRedLight(2|3). */
    static int lightColor(int state) {
        switch (state) {
            case 2: case 3: return COLOR_RED;
            case 7: case 8: return COLOR_YELLOW;
            case 5: case 6: return COLOR_GREEN;
            default: return COLOR_NONE;
        }
    }

    /** HUD 신호등 그림에 쓸 방향: 직진, 없으면 차량 진행 방향이 하나뿐일 때 그것. 없으면 -1. */
    static int displayIndex(int[] movements) {
        int only = -1, vehicleMoves = 0;
        int n = movements == null ? 0 : movements.length;
        for (int i = 0; i < n; i++) {
            if (movements[i] == MOVE_STRAIGHT) return i;
            if (isVehicleMovement(movements[i])) {
                vehicleMoves++;
                only = i;
            }
        }
        return vehicleMoves == 1 ? only : -1;
    }

    /**
     * 폰 신호등(TrafficSignalInfoRepository)과 같은 색·잔여초. on/remain 순서는
     * red, left, green, right, uturn. 초록·좌회전이 켜져 있으면 초록(둘 다면 짧은 쪽),
     * 아니면 빨강. 아무것도 안 켜졌으면 폰도 숨기므로 null.
     */
    static int[] phoneLight(boolean[] on, int[] remain) {
        if (on == null || remain == null || on.length < 3 || remain.length < 3) return null;
        if (on[2]) return new int[]{COLOR_GREEN, on[1] ? Math.min(remain[2], remain[1]) : remain[2]};
        if (on[1]) return new int[]{COLOR_GREEN, remain[1]};
        if (on[0]) return new int[]{COLOR_RED, remain[0]};
        return null;
    }

    static int subtractElapsed(int value, int elapsedSec) {
        return Math.max(0, value - Math.max(0, elapsedSec));
    }

    /** CarrotUiStateData.distanceAfterElapsed: 수신 뒤 차량 속도로 간 거리를 뺀다. */
    static int distanceAfterElapsed(int baseDistanceM, int speedKph, int elapsedSec) {
        int moved = (int) Math.round((Math.max(0, speedKph) / 3.6) * Math.max(0, elapsedSec));
        return Math.max(0, baseDistanceM - moved);
    }

    /** 두 점 사이 거리(m). 하나라도 없으면 -1. */
    static int distanceMeters(double lat1, double lon1, double lat2, double lon2) {
        if ((lat1 == 0.0 && lon1 == 0.0) || (lat2 == 0.0 && lon2 == 0.0)) return -1;
        double r = 6371000.0;
        double p1 = Math.toRadians(lat1), p2 = Math.toRadians(lat2);
        double dp = p2 - p1, dl = Math.toRadians(lon2 - lon1);
        double a = Math.sin(dp / 2) * Math.sin(dp / 2)
                + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) * Math.sin(dl / 2);
        return (int) Math.round(2 * r * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a)));
    }

    /**
     * TrafficSignal(source "signal_event") 를 만든다. lights 는 {red,left,green,right,uturn}
     * 순서의 켜짐·잔여초(TrafficSignalInfo), movements 는 SignalState 목록. elapsedSec 만큼
     * 잔여초를 줄인다. distanceM 은 이미 계산한 현재 거리. 표시할 수 없으면 null
     * (패치판 prepareForSend: 60초 경과·5m 이내 통과·남은 초 없음).
     */
    static String signal(boolean[] lightsOn, int[] lightsRemain, int[] movements, int[] states,
                         int[] remains, int distanceM, double lat, double lon, long ageMs) {
        int elapsed = (int) (Math.max(0L, ageMs) / 1000L);
        boolean anyRemaining = false;
        String lights = null;
        if (lightsOn != null) {
            String[] names = {"red", "left", "green", "right", "uturn"};
            Obj o = new Obj();
            for (int i = 0; i < names.length; i++) {
                int remain = subtractElapsed(lightsRemain[i], elapsed);
                anyRemaining |= remain > 0;
                o.raw(names[i], new Obj().bool("on", lightsOn[i]).num("remain_sec", remain).build());
            }
            lights = o.build();
        }
        String movementJson = null;
        if (movements != null && movements.length > 0) {
            Obj o = new Obj();
            for (int i = 0; i < movements.length; i++) {
                String name = movementName(movements[i]);
                if (name == null) continue;
                int remain = subtractElapsed(remains[i], elapsed);
                anyRemaining |= remain > 0;
                o.raw(name, new Obj().str("state", lightStateName(states[i])).num("code", states[i])
                        .num("remain_sec", remain).build());
            }
            if (!o.isEmpty()) movementJson = o.build();
        }
        if (lights == null && movementJson == null) return null;
        boolean expired = ageMs > MAX_TRAFFIC_SIGNAL_AGE_MS;
        boolean passed = distanceM >= 0 && distanceM <= TRAFFIC_SIGNAL_PASS_DISTANCE_M;
        if (expired || passed || !anyRemaining) return null;
        return new Obj().bool("visible", true).str("source", "signal_event")
                .num("distance_m", distanceM >= 0 ? Integer.valueOf(distanceM) : null)
                .num("last_update_age_ms", Long.valueOf(Math.max(0L, ageMs)))
                .raw("point", point(lat, lon)).raw("lights", lights).raw("movements", movementJson)
                .build();
    }

    // ---- 공통 -----------------------------------------------------------------

    /**
     * 패치판 compactRoutePoints: 전체 경로를 ceil(n/256) 간격으로 고르고 마지막 점을 더한다.
     */
    static String polyline(double[] lats, double[] lons, int count) {
        if (count <= 0) return null;
        int step = Math.max(1, (int) Math.ceil(count / (double) ROUTE_MAX_POINTS));
        StringBuilder out = new StringBuilder(64 * (count / step + 2)).append('[');
        int n = 0;
        for (int i = 0; i < count; i += step) {
            if (n++ > 0) out.append(',');
            out.append("{\"lat\":").append(decimal(lats[i])).append(",\"lon\":").append(decimal(lons[i])).append('}');
        }
        int last = count - 1;
        if (last % step != 0) {
            out.append(",{\"lat\":").append(decimal(lats[last])).append(",\"lon\":").append(decimal(lons[last])).append('}');
        }
        return out.append(']').toString();
    }

    /**
     * 티맵 상단 TBT 의 두 번째 안내 거리(nSvcLinkDist, 없으면 두 안내 거리 차).
     * TBT 그림(tbt_next)에만 쓴다. JSON 의 guidance_next 는 패치판처럼 nTBTDist 그대로다.
     */
    static int nextSegmentDistance(int svcLinkDist, int nextFromVehicle, int currentFromVehicle) {
        if (svcLinkDist > 0) return svcLinkDist;
        return Math.max(0, nextFromVehicle - currentFromVehicle);
    }

    static String ints(int[] values, int n) {
        StringBuilder out = new StringBuilder("[");
        for (int i = 0; i < n; i++) {
            if (i > 0) out.append(',');
            out.append(values != null && i < values.length ? values[i] : 0);
        }
        return out.append(']').toString();
    }

    static String decimal(double value) {
        if (!Double.isFinite(value)) return "0";
        String s = String.format(Locale.US, "%.7f", value);
        // 불필요한 0 을 줄인다(1.5000000 → 1.5, 3.0000000 → 3.0).
        int end = s.length();
        while (end > 0 && s.charAt(end - 1) == '0' && s.indexOf('.') >= 0 && end - 1 > s.indexOf('.') + 1) end--;
        return s.substring(0, end);
    }

    static String text(String value) {
        return value == null ? "" : value.trim();
    }

    static String quote(String value) {
        String s = value == null ? "" : value;
        StringBuilder out = new StringBuilder(s.length() + 2).append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (c < 0x20) out.append(String.format(Locale.US, "\\u%04x", (int) c));
                    else out.append(c);
            }
        }
        return out.append('"').toString();
    }
}
