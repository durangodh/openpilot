package ai.comma.tmaphud;

import java.util.Locale;

/**
 * carrot_navi_server 의 JSON 항목 값을 만든다. Android/Xposed 에 의존하지 않아
 * 호스트에서 검사할 수 있다(tests/ai/comma/tmaphud/TmapJsonCheck.java).
 *
 * 필드 이름은 EON 소비측(navigation_route.py, navigation_noo.py, remote_hud.py)이
 * 읽는 이름이다. turn_type·sdi type 은 티맵 원래 코드라 변환하지 않는다
 * (네이버/카카오 모듈은 자기 코드를 이 티맵 코드로 바꿔서 보낸다).
 */
final class TmapJson {
    static final String SOURCE = "TMAP";
    // HUD 신호등 그림 색(TmapSignal 이 그린다).
    static final int COLOR_NONE = 0, COLOR_RED = 1, COLOR_YELLOW = 2, COLOR_GREEN = 3;

    private TmapJson() {
    }

    static String vehicle(double lat, double lon, double headingDeg, int speedKph,
                          String roadName, boolean virtualGps) {
        return "{\"source\":\"TMAP\",\"lat\":" + decimal(lat) + ",\"lon\":" + decimal(lon)
                + ",\"heading_deg\":" + decimal(headingDeg)
                + ",\"speed_kph\":" + speedKph
                + ",\"road_name\":" + quote(roadName)
                + ",\"virtual_gps\":" + virtualGps + "}";
    }

    /** 회전 코드가 0 이면 안내 없음(티맵 ObservableTBTData.hasTbtInfo 와 같은 기준). */
    static String guidance(int turnType, int distanceM, int timeSec, String mainText,
                           String roadName, String crossName, String nearDir,
                           String midDir, String farDir, double lat, double lon,
                           boolean unprotected) {
        if (turnType <= 0) return null;
        String main = text(mainText);
        String road = text(roadName);
        if (main.isEmpty()) main = road;
        if (road.isEmpty()) road = main;
        return "{\"source\":\"TMAP\",\"turn_type\":" + turnType
                + ",\"distance_m\":" + Math.max(0, distanceM)
                + ",\"time_sec\":" + Math.max(0, timeSec)
                + ",\"main_text\":" + quote(main)
                + ",\"road_name\":" + quote(road)
                + ",\"cross_name\":" + quote(crossName)
                + ",\"near_dir\":" + quote(nearDir)
                + ",\"mid_dir\":" + quote(midDir)
                + ",\"far_dir\":" + quote(farDir)
                + ",\"lat\":" + decimal(lat) + ",\"lon\":" + decimal(lon)
                + ",\"unprotected_turn\":" + unprotected + "}";
    }

    /**
     * 티맵 상단 TBT 의 두 번째 안내는 nSvcLinkDist(첫 안내→다음 안내 구간 거리)를
     * 보여 준다. 값이 없으면 차량 기준 거리 차이로 구한다(TBTPopUpService 와 같은 계산).
     */
    static int nextSegmentDistance(int svcLinkDist, int nextFromVehicle, int currentFromVehicle) {
        if (svcLinkDist > 0) return svcLinkDist;
        return Math.max(0, nextFromVehicle - currentFromVehicle);
    }

    static String lane(int count, int currentLane, int distanceM, int[] turnInfo,
                       int[] available, int[] etcInfo, int turnCode, int roadCategory,
                       boolean show) {
        if (count <= 0) return null;
        int n = Math.min(count, 16);
        return "{\"source\":\"TMAP\",\"count\":" + n
                + ",\"current_lane\":" + currentLane
                + ",\"distance_m\":" + Math.max(0, distanceM)
                + ",\"turn_info\":" + ints(turnInfo, n)
                + ",\"available\":" + ints(available, n)
                + ",\"etc_info\":" + ints(etcInfo, n)
                + ",\"turn_code\":" + turnCode
                + ",\"road_category\":" + roadCategory
                + ",\"show\":" + show + "}";
    }

    static String sdi(int type, int distanceM, int speedLimitKph, int blockType,
                      int blockDistanceM, int blockSpeedKph, int blockAverageKph,
                      boolean schoolZone, double lat, double lon) {
        return "{\"type\":" + type + ",\"distance_m\":" + distanceM
                + ",\"speed_limit_kph\":" + Math.max(0, speedLimitKph)
                + ",\"block_type\":" + blockType
                + ",\"block_distance_m\":" + blockDistanceM
                + ",\"block_speed_kph\":" + Math.max(0, blockSpeedKph)
                + ",\"block_average_kph\":" + Math.max(0, blockAverageKph)
                + ",\"school_zone\":" + schoolZone
                + ",\"lat\":" + decimal(lat) + ",\"lon\":" + decimal(lon) + "}";
    }

    static String section(boolean active, boolean suspended, boolean offRoute,
                          int speedLimitKph, double remainingDistanceM,
                          double averageSpeedKph, int remainingTimeSec) {
        return "{\"active\":" + active + ",\"suspended\":" + suspended
                + ",\"off_route\":" + offRoute
                + ",\"speed_limit_kph\":" + Math.max(0, speedLimitKph)
                + ",\"remaining_distance_m\":" + Math.max(0, Math.round(remainingDistanceM))
                + ",\"average_speed_kph\":" + Math.max(0, Math.round(averageSpeedKph))
                + ",\"remaining_time_sec\":" + Math.max(0, remainingTimeSec) + "}";
    }

    /** sdiPrimary/sdiSecondary/section 은 위 함수가 만든 JSON 또는 null. */
    static String speed(int roadLimitKph, String sdiPrimary, String sdiSecondary, String section) {
        StringBuilder out = new StringBuilder("{\"source\":\"TMAP\",\"road_limit_kph\":")
                .append(Math.max(0, roadLimitKph));
        if (sdiPrimary != null) out.append(",\"sdi\":").append(sdiPrimary);
        if (sdiSecondary != null) out.append(",\"sdi_secondary\":").append(sdiSecondary);
        if (section != null) out.append(",\"section\":").append(section);
        return out.append('}').toString();
    }

    static String route(int remainDistanceM, int remainTimeSec, int totalDistanceM,
                        String polylineJson) {
        return "{\"source\":\"TMAP\",\"remain_distance_m\":" + Math.max(0, remainDistanceM)
                + ",\"remain_time_sec\":" + Math.max(0, remainTimeSec)
                + ",\"total_distance_m\":" + Math.max(0, totalDistanceM)
                + ",\"polyline\":" + (polylineJson == null ? "[]" : polylineJson) + "}";
    }

    static String status(boolean guidanceActive, boolean routePresent, String mode,
                         boolean offRoute, boolean arrived, int rgStatus) {
        return "{\"source\":\"TMAP\",\"guidance_active\":" + guidanceActive
                + ",\"active\":" + guidanceActive
                + ",\"route_present\":" + routePresent
                + ",\"mode\":" + quote(mode)
                + ",\"state\":" + quote(guidanceActive ? "guiding" : "idle")
                + ",\"off_route\":" + offRoute
                + ",\"arrived\":" + arrived
                + ",\"rg_status\":" + rgStatus + "}";
    }

    // ---- 신호등 ---------------------------------------------------------------

    // TrafficSignalInfo.MOVEMENT_* (티맵 C-ITS)
    static final int MOVE_STRAIGHT = 1, MOVE_LEFT = 2, MOVE_PEDESTRIAN = 3, MOVE_BICYCLE = 4,
            MOVE_RIGHT = 5, MOVE_BUS = 6, MOVE_UTURN = 7;

    /** 차량이 따르는 진행 방향만 보낸다(보행자·자전거·버스 신호 제외). */
    static String movementName(int movement) {
        switch (movement) {
            case MOVE_STRAIGHT: return "straight";
            case MOVE_LEFT: return "left";
            case MOVE_RIGHT: return "right";
            case MOVE_UTURN: return "uturn";
            default: return null;
        }
    }

    /**
     * TrafficSignalInfo.EVENT_STATE_* 를 navigation_route._signal_phase 가 읽는
     * 이름으로 바꾼다. 티맵 isGreenLight(5|6), isRedLight(2|3) 와 같은 구분.
     */
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

    /** HUD 신호등 그림 색. COLOR_RED/YELLOW/GREEN, 모르면 COLOR_NONE. */
    static int lightColor(int state) {
        switch (state) {
            case 2: case 3: return COLOR_RED;
            case 7: case 8: return COLOR_YELLOW;
            case 5: case 6: return COLOR_GREEN;
            default: return COLOR_NONE;
        }
    }

    static boolean isFlashingOrDark(int state) {
        return state == 1 || state == 2 || state == 7;
    }

    /**
     * movements/states/remains 는 같은 길이. elapsedSec 만큼 잔여초를 줄인다
     * (티맵도 수신 뒤 1초마다 줄여서 표시한다).
     */
    static String signal(int distanceM, int[] movements, int[] states, int[] remains, int elapsedSec) {
        StringBuilder entries = new StringBuilder("[");
        boolean blink = true;
        int count = 0;
        int n = movements == null ? 0 : movements.length;
        for (int i = 0; i < n; i++) {
            String guide = movementName(movements[i]);
            if (guide == null) continue;
            int state = states[i];
            if (!isFlashingOrDark(state)) blink = false;
            if (count++ > 0) entries.append(',');
            entries.append("{\"guide\":").append(quote(guide))
                    .append(",\"state\":").append(quote(lightStateName(state)))
                    .append(",\"remaining_sec\":").append(Math.max(0, remains[i] - Math.max(0, elapsedSec)))
                    .append(",\"movement\":").append(movements[i])
                    .append(",\"light_state\":").append(state).append('}');
        }
        if (count == 0) return emptySignal();
        return "{\"source\":\"TMAP\",\"distance_m\":" + Math.max(0, distanceM)
                + ",\"blink\":" + blink + ",\"signals\":" + entries.append(']') + "}";
    }

    static String emptySignal() {
        return "{\"source\":\"TMAP\",\"signals\":[]}";
    }

    /**
     * HUD 신호등 그림에 쓸 방향: 직진, 없으면 진행 방향이 하나뿐일 때 그것.
     * 반환값은 배열 인덱스, 없으면 -1.
     */
    static int displayIndex(int[] movements) {
        int only = -1, vehicleMoves = 0;
        int n = movements == null ? 0 : movements.length;
        for (int i = 0; i < n; i++) {
            if (movements[i] == MOVE_STRAIGHT) return i;
            if (movementName(movements[i]) != null) {
                vehicleMoves++;
                only = i;
            }
        }
        return vehicleMoves == 1 ? only : -1;
    }

    // ---- 공통 -----------------------------------------------------------------

    static String polyline(double[] lats, double[] lons, int count) {
        StringBuilder out = new StringBuilder(count * 40 + 2).append('[');
        for (int i = 0; i < count; i++) {
            if (i > 0) out.append(',');
            out.append("{\"lat\":").append(decimal(lats[i]))
                    .append(",\"lon\":").append(decimal(lons[i])).append('}');
        }
        return out.append(']').toString();
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
        return Double.isFinite(value) ? String.format(Locale.US, "%.7f", value) : "0";
    }

    static String text(String value) {
        return value == null ? "" : value.trim();
    }

    static String quote(String value) {
        String s = text(value);
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
