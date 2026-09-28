package ai.comma.remotehud;
import java.util.*;
import java.nio.charset.StandardCharsets;

public final class RemoteInputCheck {
    static void check(boolean b, String name) { if (!b) throw new AssertionError(name); }
    static void ticks(RemoteKeyEngine e, long from, long to) { for (long t = from; t <= to; t += 50) e.tick(t); }
    public static void main(String[] args) {
        Map<String, String> mapping = new HashMap<>();
        List<String> out = new ArrayList<>();
        RemoteKeyEngine e = new RemoteKeyEngine((key, gesture) -> mapping.getOrDefault(key + gesture, "none"), out::add);
        mapping.put("1short", "res");
        e.release(1, 100, false); check(out.isEmpty(), "unmatched up");
        e.press(1, 100); e.press(1, 120); e.release(1, 150, false);
        check(out.equals(Arrays.asList("res")), "duplicate down fires once"); out.clear();
        mapping.put("1long", "cancel");
        e.press(1, 200); ticks(e, 200, 1100); e.release(1, 1200, false);
        check(out.equals(Arrays.asList("cancel")), "long only once; no short on release"); out.clear();
        e.press(1, 1300); e.reset(); e.release(1, 1400, false); check(out.isEmpty(), "disconnect resets hold");
        e.press(1, 1500); e.release(1, 1600, true); check(out.isEmpty(), "cancelled up");
        mapping.put("1double", "gap");
        e.press(1, 2000); e.release(1, 2050, false); e.press(1, 2150); e.release(1, 2200, false);
        check(out.equals(Arrays.asList("gap")), "double without short"); out.clear();
        e.press(1, 3000); e.release(1, 3050, false); e.tick(3400);
        check(out.equals(Arrays.asList("res")), "single waits only assigned double"); out.clear();
        e.press(1, 4000); e.release(1, 4050, false); e.tick(5000); check(out.isEmpty(), "stalled single expires");
        mapping.put("1long", "set"); e.press(1, 5000); ticks(e, 5000, 6300);
        e.release(1, 6350, false); check(out.equals(Arrays.asList("set", "set")), "bounded speed repeat"); out.clear();
        e.press(1, 7000); e.tick(18000); e.release(1, 18010, false); check(out.isEmpty(), "stuck key expires");
        mapping.put("1long", "lane_left"); e.reset(); e.press(1, 20000); ticks(e, 20000, 20850); e.release(1, 20900, false); e.tick(21000);
        check(out.equals(Arrays.asList("lane_left", "lane_left")), "lane heartbeat stops on release");
        out.clear(); e.reset(); e.press(1, 22000); e.blockDrivingHolds(); ticks(e, 22000, 23000);
        e.release(1, 23050, false); check(out.isEmpty(), "pedal interruption cancels held driving action through release");
        RemoteCommandProtocol p = new RemoteCommandProtocol();
        String session = "a".repeat(32), key = "0123456789abcdef".repeat(2), ticket = "b".repeat(16);
        check(!p.submit("res", key, 0), "offline press discarded");
        p.observe(session, ticket, "", "", 1000);
        check(p.submit("res", key, 1010), "online submit");
        String wire = new String(p.next(1010), StandardCharsets.US_ASCII);
        check(wire.startsWith("HUDCMD2 " + session), "v2 wire");
        check(p.next(1050) == null, "retry rate");
        check(wire.equals(new String(p.next(1110), StandardCharsets.US_ASCII)), "retry unchanged");
        String[] fields = wire.split(" ");
        p.observe(session, ticket, fields[2], "accepted", 1120);
        check(p.next(1120) == null && p.result.contains("EON 수신"), "ack clears");
        p.submit("set", key, 1130); p.observe("c".repeat(32), ticket, "", "", 1140);
        check(p.next(1150) == null, "new session drops press");
        p.submit("set", key, 1160); check(p.next(1560) == null, "400ms expiry");
        check(!p.submit("res", key, 2000), "stale telemetry");
        // Shared Python/Java HMAC test vector.
        check(RemoteCommandProtocol.sign(key, "HUDCMD2 test").equals("d51286bb458b866314601810b8433cf56447de1fe3517c9530a677044db6cf4a"), "HMAC cross-language vector");
        System.out.println("RemoteInputCheck PASS");
    }
}
