package ai.comma.remotehud;

import java.nio.charset.StandardCharsets;

public final class NavSelectionProtocolCheck {
    private static void check(boolean condition) {
        if (!condition) throw new AssertionError();
    }
    public static void main(String[] args) {
        String session = "0123456789abcdef0123456789abcdef";
        String old = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        String current = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        for (int app = 1; app <= 2; app++) {
            check(new String(NavSelectionProtocol.request(session, current, app), StandardCharsets.US_ASCII)
                    .equals("HUDNAV1 " + session + " " + current + " " + app));
        }
        check(NavSelectionProtocol.request("", current, 1) == null);
        check(NavSelectionProtocol.request(session, "bad", 1) == null);
        check(NavSelectionProtocol.request(session, current, 3) == null);
        check(!NavSelectionProtocol.acknowledged(current, old));
        check(!NavSelectionProtocol.acknowledged("", ""));
        check(NavSelectionProtocol.acknowledged(current, current));
        check(NavSelectionProtocol.normalizeApp(1) == 1);
        check(NavSelectionProtocol.normalizeApp(2) == 2);
        check(NavSelectionProtocol.normalizeApp(99) == 1);
        // A button tap must stop the opposite app even after a cold service
        // start has already loaded the newly saved selection.
        check(NavSelectionProtocol.appToStop(2, 2, true) == 1);
        check(NavSelectionProtocol.appToStop(1, 1, true) == 2);
        check(NavSelectionProtocol.appToStop(1, 2, false) == 1);
        check(NavSelectionProtocol.appToStop(2, 2, false) == 0);
        System.out.println("Navigation protocol checks passed");
    }
}
