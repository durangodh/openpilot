package ai.comma.remotehud;

import java.nio.charset.StandardCharsets;
import java.util.UUID;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/** One in-flight request. Reconnect/session changes never replay a queued press. */
final class RemoteCommandProtocol {
    private String session = "", ticket = "", pending = "", id = "", command = "";
    private long receivedAt = -1000, issuedAt, sentAt = -1000;
    long generation = 0;
    String result = "EON 연결 대기";
    static boolean hex(String value, int length) {
        return value != null && value.length() == length && value.matches("[0-9a-f]+");
    }
    static String sign(String key, String body) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key.getBytes(StandardCharsets.US_ASCII), "HmacSHA256"));
            StringBuilder out = new StringBuilder();
            for (byte b : mac.doFinal(body.getBytes(StandardCharsets.US_ASCII))) out.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
            return out.toString();
        } catch (java.security.GeneralSecurityException e) { throw new IllegalStateException(e); }
    }
    void observe(String newSession, String newTicket, String ack, String outcome, long now) {
        if (!hex(newSession, 32) || !hex(newTicket, 16)) {
            reset(); result = "EON 연결 키 등록 필요"; return;
        }
        if (!session.equals(newSession)) reset();
        session = newSession; ticket = newTicket; receivedAt = now;
        if (!pending.isEmpty() && id.equals(ack)) {
            pending = "";
            result = command + ("accepted".equals(outcome) ? " · EON 수신" : " · 현재 차량 상태에서 차단");
        }
    }
    boolean fresh(long now) { return hex(session, 32) && hex(ticket, 16) && now >= receivedAt && now - receivedAt <= 250; }
    boolean submit(String action, String key, long now) {
        expire(now);
        if (!hex(key, 32)) { result = "S9에 EON 연결 키 입력 필요"; return false; }
        if (!fresh(now)) {
            result = "EON 연결 끊김 · 명령 전송 안 함"; return false;
        }
        if (!pending.isEmpty() && !action.equals("cancel")) { result = "이전 명령 확인 중"; return false; }
        id = UUID.randomUUID().toString().replace("-", "");
        command = action;
        String body = "HUDCMD2 " + session + " " + id + " " + ticket + " " + action;
        pending = body + " " + sign(key, body);
        issuedAt = now; sentAt = -1000; result = action + " · 전송 대기";
        return true;
    }
    private void expire(long now) {
        if (!pending.isEmpty() && (now < issuedAt || now - issuedAt >= 400)) {
            pending = ""; result = "응답 없음 · 연결 키/통신 확인";
        }
    }
    byte[] next(long now) {
        expire(now);
        if (pending.isEmpty() || now - sentAt < 100) return null;
        sentAt = now;
        return pending.getBytes(StandardCharsets.US_ASCII);
    }
    void reset() { generation++; pending = id = session = ticket = ""; receivedAt = -1000; }
}
