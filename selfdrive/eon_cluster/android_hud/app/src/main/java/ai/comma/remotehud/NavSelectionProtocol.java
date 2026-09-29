package ai.comma.remotehud;

import java.nio.charset.StandardCharsets;

final class NavSelectionProtocol {
    private NavSelectionProtocol() { }
    static int normalizeApp(int app) {
        return app >= 0 && app <= 3 ? app : 1;
    }
    static String appLabel(int app) {
        int selected = normalizeApp(app);
        return selected == 0 ? "None" : selected == 2 ? "Naver" : selected == 3 ? "Kakao" : "Tmap";
    }
    static int appToStop(int configured, int requested, boolean explicitSelection) {
        int selected = normalizeApp(requested);
        // explicit 선택 시에는 switchNavApps 가 selected 외 나머지를 모두
        // 멈추므로(stopOthers), 여기서는 configured 기반의 단일 대상만 유지한다.
        if (explicitSelection) return configured != selected ? configured : 0;
        return configured != selected && (configured == 1 || configured == 2 || configured == 3) ? configured : 0;
    }
    static byte[] request(String session, String id, int app) {
        if (!validId(session) || !validId(id) || app < 0 || app > 3) return null;
        return ("HUDNAV1 " + session + " " + id + " " + app).getBytes(StandardCharsets.US_ASCII);
    }
    static boolean validId(String id) {
        return id != null && id.matches("[0-9a-f]{32}");
    }
    static boolean acknowledged(String pending, String ack) {
        return validId(pending) && pending.equals(ack);
    }
}
