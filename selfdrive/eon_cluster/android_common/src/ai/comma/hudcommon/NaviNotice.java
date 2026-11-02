package ai.comma.hudcommon;

/**
 * HUD 전용 안내(감속에는 쓰지 않음)를 speed JSON 에 붙인다(세 모듈 공통):
 * "notice":{"kind":"traffic_info","distance_m":N}. EON 은 이것을 순정 HUD 의
 * 교통정보수집 표시(0x1E5 단속 아님 항목 9)로 보낸다.
 */
public final class NaviNotice {
    public static final String TRAFFIC_INFO = "traffic_info";

    private NaviNotice() {}

    public static String with(String json, String kind, int distanceM) {
        if (json == null || kind == null || distanceM <= 0 || !json.endsWith("}")) return json;
        return json.substring(0, json.length() - 1) + ",\"notice\":{\"kind\":\"" + kind
                + "\",\"distance_m\":" + distanceM + "}}";
    }
}
