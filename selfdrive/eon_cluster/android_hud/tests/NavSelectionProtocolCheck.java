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
        // 0=선택안함 1=티맵 2=네이버 3=카카오
        for (int app = 0; app <= 3; app++) {
            check(new String(NavSelectionProtocol.request(session, current, app), StandardCharsets.US_ASCII)
                    .equals("HUDNAV1 " + session + " " + current + " " + app));
        }
        check(NavSelectionProtocol.request("", current, 1) == null);
        check(NavSelectionProtocol.request(session, "bad", 1) == null);
        // 3은 이제 유효(카카오), 4는 무효
        check(NavSelectionProtocol.request(session, current, 4) == null);
        check(!NavSelectionProtocol.acknowledged(current, old));
        check(!NavSelectionProtocol.acknowledged("", ""));
        check(NavSelectionProtocol.acknowledged(current, current));
        check(NavSelectionProtocol.normalizeApp(1) == 1);
        check(NavSelectionProtocol.normalizeApp(2) == 2);
        check(NavSelectionProtocol.normalizeApp(3) == 3);
        check(NavSelectionProtocol.normalizeApp(0) == 0);
        check(NavSelectionProtocol.normalizeApp(99) == 1);
        check(NavSelectionProtocol.appLabel(0).equals("None"));
        check(NavSelectionProtocol.appLabel(1).equals("Tmap"));
        check(NavSelectionProtocol.appLabel(2).equals("Naver"));
        check(NavSelectionProtocol.appLabel(3).equals("Kakao"));
        // 3앱 지원으로 appToStop 이 바뀜: explicit 선택 시 switchNavApps 가
        // 선택 외 전부를 정지하므로, appToStop 은 configured 기반 단일 대상만 반환.
        // configured==selected 이면 정지 대상 없음(0).
        check(NavSelectionProtocol.appToStop(2, 2, true) == 0);
        check(NavSelectionProtocol.appToStop(1, 1, true) == 0);
        // configured 가 선택과 다르면 그 configured 를 정지 대상으로.
        check(NavSelectionProtocol.appToStop(1, 3, true) == 1);
        check(NavSelectionProtocol.appToStop(2, 3, true) == 2);
        // 비explicit(원격/부팅): configured 가 선택과 다르고 1/2/3 이면 그 값.
        check(NavSelectionProtocol.appToStop(1, 2, false) == 1);
        check(NavSelectionProtocol.appToStop(3, 1, false) == 3);
        check(NavSelectionProtocol.appToStop(2, 2, false) == 0);
        System.out.println("Navigation protocol checks passed");
    }
}
