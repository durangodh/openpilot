package ai.comma.remotekey;

import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.UUID;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * Phone side of selfdrive/eon_cluster/hud_remote.py (HELLO3/SESS3/CMD3/ACK3).
 *
 * One command is in flight at a time. It is resent every RETRY_MS until EON
 * acknowledges it and given up after GIVE_UP_MS, so a short Wi-Fi stall delays
 * a press instead of dropping it. EON applies a request id at most once.
 */
final class RelayProtocol {
    static final int PORT = 7212;
    static final long RETRY_MS = 150;
    static final long GIVE_UP_MS = 1500;
    static final long HELLO_MS = 2000;
    static final long HELLO_FAST_MS = 300;
    /** A session younger than this is used for a command; EON keeps tokens for 6 s. */
    static final long SESSION_FRESH_MS = 5000;
    /** Presses are refused when EON has not answered for this long. */
    static final long EON_LOST_MS = 10000;

    final String client = UUID.randomUUID().toString().replace("-", "").substring(0, 16);
    private String session = "", token = "";
    private long sessionAt = -100000;
    private String pendingId = "", pendingCommand = "";
    private long issuedAt, sentAt = -100000, lastHelloAt = -100000;
    boolean drive;
    String result = "EON 찾는 중";
    long lastLatencyMs = -1;

    static boolean hex(String value, int length) {
        return value != null && value.length() == length && value.matches("[0-9a-f]+");
    }

    static boolean key(String value) { return hex(value, 8) || hex(value, 32); }

    static String sign(String key, String body) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key.getBytes(StandardCharsets.US_ASCII), "HmacSHA256"));
            StringBuilder out = new StringBuilder();
            for (byte b : mac.doFinal(body.getBytes(StandardCharsets.US_ASCII))) {
                out.append(String.format(Locale.ROOT, "%02x", b & 255));
            }
            return out.toString();
        } catch (java.security.GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }

    private static byte[] signed(String key, String body) {
        return (body + " " + sign(key, body)).getBytes(StandardCharsets.US_ASCII);
    }

    boolean sessionFresh(long now) {
        return !token.isEmpty() && now >= sessionAt && now - sessionAt <= SESSION_FRESH_MS;
    }

    boolean eonSeen(long now) { return now >= sessionAt && now - sessionAt <= EON_LOST_MS; }

    boolean pending() { return !pendingId.isEmpty(); }

    /** HELLO3 when due: fast while a command waits for a session, otherwise every HELLO_MS. */
    byte[] hello(String key, long now) {
        long interval = pending() && !sessionFresh(now) ? HELLO_FAST_MS : HELLO_MS;
        if (!key(key) || now - lastHelloAt < interval) return null;
        lastHelloAt = now;
        return signed(key, "HELLO3 " + client);
    }

    boolean submit(String action, long now) {
        expire(now);
        if (!eonSeen(now)) { result = "EON 연결 안 됨 · 명령 보내지 않음"; return false; }
        if (pending() && !action.equals("cancel")) { result = "이전 명령 확인 중"; return false; }
        pendingId = UUID.randomUUID().toString().replace("-", "");
        pendingCommand = action;
        issuedAt = now;
        sentAt = -100000;
        result = action + " · 전송 중";
        return true;
    }

    private void expire(long now) {
        if (pending() && (now < issuedAt || now - issuedAt >= GIVE_UP_MS)) {
            pendingId = "";
            result = pendingCommand + " · 응답 없음 (와이파이 확인)";
        }
    }

    /** The CMD3 datagram to (re)send now, or null. */
    byte[] command(String key, long now) {
        expire(now);
        if (!pending() || !key(key) || !sessionFresh(now) || now - sentAt < RETRY_MS) return null;
        sentAt = now;
        return signed(key, "CMD3 " + session + " " + token + " " + pendingId + " " + pendingCommand);
    }

    /** Handle a SESS3/ACK3 reply. Returns true when it came from EON with the same key. */
    boolean receive(String key, String text, long now) {
        if (!key(key)) return false;
        int split = text.lastIndexOf(' ');
        if (split <= 0) return false;
        String body = text.substring(0, split);
        String signature = text.substring(split + 1);
        if (!hex(signature, 64) || !java.security.MessageDigest.isEqual(
                signature.getBytes(StandardCharsets.US_ASCII),
                sign(key, body).getBytes(StandardCharsets.US_ASCII))) {
            return false;
        }
        String[] f = body.split(" ");
        if (f.length == 6 && f[0].equals("SESS3") && f[1].equals(client) && hex(f[2], 32) && hex(f[3], 16)) {
            session = f[2];
            token = f[3];
            sessionAt = now;
            drive = f[5].equals("1");
            if (!pending()) result = "EON 연결됨";
            return true;
        }
        if (f.length == 3 && f[0].equals("ACK3") && pending() && f[1].equals(pendingId)) {
            if (f[2].equals("stale")) {
                // EON forgot the token (restart or long stall): get a new one and resend.
                token = "";
                sentAt = -100000;
                return true;
            }
            lastLatencyMs = now - issuedAt;
            pendingId = "";
            result = pendingCommand + (f[2].equals("accepted") ? " · EON 수신" : f[2].equals("blocked")
                    ? " · 현재 차량 상태에서 차단" : " · EON 처리 실패") + " (" + lastLatencyMs + "ms)";
            return true;
        }
        return f.length == 3 && f[0].equals("ACK3");
    }

    void reset() {
        pendingId = "";
        session = token = "";
        sessionAt = -100000;
        drive = false;
        result = "EON 찾는 중";
    }
}
