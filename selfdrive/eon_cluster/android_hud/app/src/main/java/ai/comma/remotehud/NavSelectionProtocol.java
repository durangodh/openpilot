package ai.comma.remotehud;

import java.nio.charset.StandardCharsets;

final class NavSelectionProtocol {
    private NavSelectionProtocol() { }
    static byte[] request(String session, String id, int app) {
        if (!validId(session) || !validId(id) || (app != 1 && app != 2)) return null;
        return ("HUDNAV1 " + session + " " + id + " " + app).getBytes(StandardCharsets.US_ASCII);
    }
    static boolean validId(String id) {
        return id != null && id.matches("[0-9a-f]{32}");
    }
    static boolean acknowledged(String pending, String ack) {
        return validId(pending) && pending.equals(ack);
    }
}
