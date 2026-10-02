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
        // 차량·안내: 좌회전(12) 320m 앞, 다음은 우회전(13) 구간 거리 180m.
        emit("vehicle", TmapJson.vehicle(37.5665, 126.9780, 92, 43, "세종대로", false));
        String current = TmapJson.guidance(12, 320, 40, "시청 방면", "세종대로", "시청앞",
                "시청", "", "", 37.5670, 126.9790, false);
        emit("guidance_current", current);
        int seg = TmapJson.nextSegmentDistance(0, 500, 320);
        check(seg == 180, "next segment from vehicle distances");
        check(TmapJson.nextSegmentDistance(210, 500, 320) == 210, "svc link distance wins");
        emit("guidance_next", TmapJson.guidance(13, seg, 20, "", "을지로", "", "", "", "",
                37.5660, 126.9800, false));
        check(TmapJson.guidance(0, 100, 0, "x", "x", "", "", "", "", 0, 0, false) == null,
                "turn 0 means no guidance");

        emit("lane_current", TmapJson.lane(4, 2, 250, new int[]{1, 1, 2, 4},
                new int[]{1, 0, 0, 0}, new int[]{0, 0, 0, 0}, 1, 3, true));
        check(TmapJson.lane(0, 0, 0, null, null, null, 0, 0, false) == null, "no lanes");

        String camera = TmapJson.sdi(1, 450, 50, 0, 0, 0, 0, false, 37.57, 126.98);
        String section = TmapJson.section(true, false, false, 80, 1234.4, 76.6, 55);
        emit("speed", TmapJson.speed(60, camera, null, section));

        // 직진 녹색 12초(수신 후 2초 경과) + 좌회전 적색 + 보행자 신호(제외).
        String signal = TmapJson.signal(85, new int[]{1, 2, 3}, new int[]{6, 3, 6},
                new int[]{12, 30, 9}, 2);
        emit("traffic_signal", signal);
        check(signal.contains("\"remaining_sec\":10"), "elapsed seconds subtracted");
        check(!signal.contains("\"movement\":3"), "pedestrian signal skipped");
        check(TmapJson.displayIndex(new int[]{2, 1}) == 1, "straight shown");
        check(TmapJson.displayIndex(new int[]{2, 3}) == 0, "single vehicle movement shown");
        check(TmapJson.displayIndex(new int[]{2, 5}) == -1, "ambiguous movements hidden");
        check(TmapJson.lightColor(3) == TmapJson.COLOR_RED, "red");
        check(TmapJson.lightColor(8) == TmapJson.COLOR_YELLOW, "yellow");
        check(TmapJson.lightColor(5) == TmapJson.COLOR_GREEN, "permissive green");
        check(TmapJson.signal(50, new int[]{3}, new int[]{6}, new int[]{5}, 0)
                .equals(TmapJson.emptySignal()), "pedestrian-only is empty");

        double[] lats = {37.5665, 37.5670, 37.5660};
        double[] lons = {126.9780, 126.9790, 126.9800};
        emit("route", TmapJson.route(4200, 600, 5100, TmapJson.polyline(lats, lons, 3)));
        emit("navigation_status", TmapJson.status(true, true, "real_drive", false, false, 1));

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

        check(TmapJson.quote(" a\"b\\c\nd ").equals("\"a\\\"b\\\\c\\nd\""), "json escaping, trimmed");
        check(TmapJson.decimal(Double.NaN).equals("0"), "NaN guarded");
    }
}
