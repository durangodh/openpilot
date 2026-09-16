package ai.comma.remotehud;

import java.nio.charset.StandardCharsets;

final class NavSelectionProtocol {
    private NavSelectionProtocol() { }
    static int normalizeApp(int app) {
        return app == 2 ? 2 : 1;
    }
    static String appLabel(int app) {
        return normalizeApp(app) == 2 ? "Naver" : "Tmap";
    }
    static int appToStop(int configured, int requested, boolean explicitSelection) {
        int selected = normalizeApp(requested);
        if (explicitSelection) return selected == 2 ? 1 : 2;
        return configured != selected && (configured == 1 || configured == 2) ? configured : 0;
    }
    static String switchCommand(String[] stopPackages, String launchComponent) {
        StringBuilder command = new StringBuilder();
        if (stopPackages != null) {
            for (String stopPackage : stopPackages) {
                if (stopPackage == null || stopPackage.isEmpty()) continue;
                if (command.length() > 0) command.append("; ");
                command.append("am force-stop ").append(stopPackage);
            }
        }
        if (launchComponent != null && !launchComponent.isEmpty()) {
            if (command.length() > 0) command.append("; ");
            command.append("am start -n ").append(launchComponent);
        }
        return command.toString();
    }
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
