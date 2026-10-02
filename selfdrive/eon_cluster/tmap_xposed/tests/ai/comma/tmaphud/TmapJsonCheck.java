package ai.comma.tmaphud;

/**
 * Host-side check for the 7714 JSON values. Prints one "name<TAB>json" line per
 * item so a Python harness can feed them to the EON consumers.
 *
 *   javac -d /tmp/tmapcheck app/src/main/java/ai/comma/tmaphud/TmapJson.java \
 *       app/src/main/java/ai/comma/tmaphud/TmapAssets.java tests/ai/comma/tmaphud/TmapJsonCheck.java
 *   java -Dstdout.encoding=UTF-8 -cp /tmp/tmapcheck ai.comma.tmaphud.TmapJsonCheck
 */
public final class TmapJsonCheck {
    private static void check(boolean ok, String what) {
        if (!ok) throw new AssertionError(what);
    }

    private static void emit(String name, String json) {
        System.out.println(name + "\t" + json);
    }

    public static void main(String[] args) {
        // 값과 조건은 캐롯 패치판(CarrotUiStateData)과 같아야 한다.
        emit("vehicle", TmapJson.vehicle(37.5665, 126.9780, 92, 43, "세종대로", false));
        check(TmapJson.vehicle(0, 0, 0, 0, "", false) == null, "no position, no vehicle");
        String current = TmapJson.guidePoint(320, 40, 12, "세종대로", "시청 방면", "시청", "", "",
                37.5670, 126.9790);
        emit("guidance_current", current);
        check(!current.contains("mid_direction"), "empty strings are omitted (NON_NULL)");
        // 다음 안내 거리는 패치판처럼 nTBTDist(차량 기준) 그대로.
        emit("guidance_next", TmapJson.guidePoint(500, 20, 13, "을지로", "", "", "", "", 37.5660, 126.9800));
        check(TmapJson.guidePoint(0, 0, 0, "", "", "", "", "", 0, 0) == null, "empty guide point");
        check(TmapJson.nextSegmentDistance(0, 500, 320) == 180, "tbt_next image segment");

        String lane = TmapJson.lane(4, 250, true, false, 2, 1, new int[]{1, 1, 2, 4},
                new int[]{0, 0, 0, 0}, new int[]{1, 0, 0, 0}, 0, 3);
        emit("lane_current", lane);
        check(lane.contains("\"count\":4") && lane.contains("\"visible\":true")
                && !lane.contains("guide_line_color"), "lane fields");
        check(TmapJson.laneCount(2, new int[]{1, 1, 1}, null) == 3, "lane count follows arrays");
        check(TmapJson.lane(0, 0, false, false, 0, 0, null, null, null, 0, 0) == null, "no lanes");
        emit("lane_ahead", TmapJson.array(new String[]{TmapJson.aheadLane(3, 800, false, 0,
                new int[]{1, 1, 2}, null, new int[]{0, 0, 1}, 0, 0, 0)}));

        String camera = TmapJson.sdi(1, 450, 50, 0, 0, 0, 0, 0, 0, 37.57, 126.98);
        String section = TmapJson.section(true, false, false, 80, 76.6, 75.0, 1234.4, 55, 0.3);
        emit("speed", TmapJson.speed(43, TmapJson.validRoadLimitKph(60), camera, null, section));
        check(TmapJson.validRoadLimitKph(820) == 80, "encoded road limit");
        check(TmapJson.validRoadLimitKph(55) == null, "road limit must be a multiple of 10");
        check(TmapJson.section(false, false, false, 0, 0, 0, 0, 0, 0) == null, "no section data");

        // 직진 녹색 12초(수신 후 2초 경과) + 좌회전 적색 + 보행자.
        String signal = TmapJson.signal(new boolean[]{false, false, true, false, false},
                new int[]{0, 0, 12, 0, 0}, new int[]{1, 2, 3}, new int[]{6, 3, 6},
                new int[]{12, 30, 9}, 77, 37.567, 126.979, 2500);
        emit("traffic_signal", signal);
        check(signal.contains("\"straight\":{\"state\":\"green\",\"code\":6,\"remain_sec\":10}"), "movement");
        check(signal.contains("\"pedestrian\""), "patched keeps pedestrian movement");
        check(!signal.contains("\"signals\""), "no signals list (EON assist stays as with the patched app)");
        check(TmapJson.signal(null, null, new int[]{1}, new int[]{6}, new int[]{5}, 3, 0, 0, 0) == null, "passed");
        check(TmapJson.signal(null, null, new int[]{1}, new int[]{6}, new int[]{5}, 50, 0, 0, 61000) == null, "expired");
        check(TmapJson.signal(null, null, new int[]{1}, new int[]{6}, new int[]{1}, 50, 0, 0, 2000) == null, "no countdown");
        check(TmapJson.distanceAfterElapsed(100, 36, 2) == 80, "distance after elapsed");
        check(TmapJson.displayIndex(new int[]{2, 1}) == 1, "straight shown");
        check(TmapJson.displayIndex(new int[]{2, 3}) == 0, "single vehicle movement shown");
        check(TmapJson.displayIndex(new int[]{2, 5}) == -1, "ambiguous movements hidden");
        check(TmapJson.lightColor(3) == TmapJson.COLOR_RED, "red");
        check(TmapJson.lightColor(8) == TmapJson.COLOR_YELLOW, "yellow");
        check(TmapJson.lightColor(5) == TmapJson.COLOR_GREEN, "permissive green");

        double[] lats = new double[600], lons = new double[600];
        for (int i = 0; i < 600; i++) {
            lats[i] = 37.5 + i * 0.0001;
            lons[i] = 126.9 + i * 0.0001;
        }
        String polyline = TmapJson.polyline(lats, lons, 600);
        int points = polyline.split("\\{").length - 1;
        check(points == 201, "ceil(600/256)=3 step + last point: " + points);
        emit("route", TmapJson.route(4200, 600, 900, 120, 5100, TmapJson.polyline(lats, lons, 3)));
        check(TmapJson.route(0, 0, 0, 0, 0, null) == null, "no route data");
        emit("navigation_status", TmapJson.status(true, false, true));
        check(TmapJson.status(true, true, true).contains("\"mode\":\"off_route\""), "off route mode");

        // 2차 그림 규칙(티맵 디컴파일 표와 같아야 한다).
        check("navigation_tbt_arrow_02_icon".equals(TmapAssets.tbtIcon(12)), "left turn icon");
        check("navigation_tbt_arrow_01_icon".equals(TmapAssets.tbtIcon(11)), "straight icon");
        check("navigation_tbt_rotary_01_icon".equals(TmapAssets.tbtIcon(142)), "rotary icon");
        check(TmapAssets.tbtIcon(999) == null, "unknown turn has no icon");
        check("navigation_lane_laneguid_12_10".equals(TmapAssets.laneArrow(3, 2, 0)), "lane 0302");
        check("navigation_lane_laneguid_1_1".equals(TmapAssets.laneArrow(1, 1, 0)), "lane 0101");
        check("navigation_lane_laneguid_4_0".equals(TmapAssets.laneArrow(99, 99, 0)), "lane default");
        check("navigation_lane_under_a".equals(TmapAssets.laneArrow(1, 1, TmapAssets.LANE_UNDERPASS)), "underpass");
        check("navigation_lane_high_b".equals(TmapAssets.laneArrow(1, 0, TmapAssets.LANE_OVERPASS)), "overpass");
        check("navigation_lane_bus_b".equals(TmapAssets.laneArrow(1, 0, TmapAssets.LANE_BUS_ONLY)), "bus lane");
        check("navigation_lane_left_pocket_1".equals(TmapAssets.lanePocket(1, TmapAssets.LANE_LEFT_POCKET)), "pocket");
        check(TmapAssets.lanePocket(1, 0) == null, "no pocket");
        check("c_15".equals(TmapAssets.safetyIcons(3, false)[0]), "section camera icon");
        check("c_01".equals(TmapAssets.safetyIcons(3, true)[0]), "changed limit uses camera icon");
        check(TmapAssets.safetyIcons(22, false) == null, "no icon for speed bump");
        check(TmapAssets.safetyLimit(2, false, 50, 80) == 80, "block speed");
        check(TmapAssets.safetyLimit(1, false, 50, 80) == 50, "camera limit");
        check(TmapAssets.distanceText(320).equals("320m"), "meters");
        check(TmapAssets.distanceText(1500).equals("1km"), "TMAP truncates km");
        check(TmapAssets.distanceText(12345).equals("12km"), "km");

        check(TmapJson.quote("a\"b\\c\nd").equals("\"a\\\"b\\\\c\\nd\""), "json escaping");
        check(TmapJson.decimal(Double.NaN).equals("0"), "NaN guarded");
    }
}
