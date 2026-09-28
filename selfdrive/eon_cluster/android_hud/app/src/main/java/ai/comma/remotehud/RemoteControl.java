package ai.comma.remotehud;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.SystemClock;
import android.view.InputDevice;
import android.view.KeyEvent;
import org.json.JSONObject;

final class RemoteControl {
    static final String[] ACTIONS = {"none", "res", "set", "gap", "cancel", "nav_toggle", "lane_left", "lane_right"};
    static final String[] LABELS = {"사용 안 함", "RES / 속도 +1", "SET / 속도 −1", "차간거리", "ACC 취소 (조향 유지)", "티맵 ↔ 네이버", "왼쪽 차선변경 요청", "오른쪽 차선변경 요청"};
    static volatile boolean configuring = false, connected = false, drivingAllowed = false;
    static volatile int learnedKey = -1;
    static volatile String lastInput = "버튼 입력 대기";
    static final RemoteCommandProtocol protocol = new RemoteCommandProtocol();
    static SharedPreferences prefs(Context c) { return c.getSharedPreferences("key_remote", Context.MODE_PRIVATE); }
    static boolean external(InputDevice d) {
        return d != null && !d.isVirtual() && Build.VERSION.SDK_INT >= 29 && d.isExternal();
    }
    static boolean selected(Context c, InputDevice d) {
        return external(d) && d.getDescriptor().equals(prefs(c).getString("device", ""));
    }
    static boolean keyAllowed(int key) {
        return key > 0 && key <= KeyEvent.getMaxKeyCode() && key != KeyEvent.KEYCODE_HOME &&
                key != KeyEvent.KEYCODE_BACK && key != KeyEvent.KEYCODE_POWER && key != KeyEvent.KEYCODE_APP_SWITCH &&
                key != KeyEvent.KEYCODE_SLEEP && key != KeyEvent.KEYCODE_WAKEUP;
    }
    static String mapping(Context c, int key, String gesture) {
        String action = prefs(c).getString(key + "." + gesture, "none");
        for (String known : ACTIONS) if (known.equals(action)) return action;
        return "none";
    }
    static boolean mapped(Context c, int key) {
        for (String gesture : new String[]{"short", "long", "double"})
            if (!mapping(c, key, gesture).equals("none")) return true;
        return false;
    }
    static boolean enabled(Context c) { return !configuring && prefs(c).getBoolean("enabled", false); }
    static void send(Context c, String action) {
        synchronized (protocol) {
            if (enabled(c)) protocol.submit(action, prefs(c).getString("secret", ""), SystemClock.uptimeMillis());
        }
    }
    static byte[] packet(Context c, JSONObject data) {
        synchronized (protocol) {
            long now = SystemClock.uptimeMillis();
            boolean allowed = data.optBoolean("hudCmdDriveAllowed", false);
            if (drivingAllowed && !allowed) protocol.reset();
            drivingAllowed = allowed;
            protocol.observe(data.optString("hudCmdSession"), data.optString("hudCmdTicket"),
                    data.optString("hudCmdAck"), data.optString("hudCmdResult"), now);
            if (!enabled(c)) { protocol.reset(); return null; }
            return protocol.next(now);
        }
    }
    static boolean ready() { synchronized (protocol) { return protocol.fresh(SystemClock.uptimeMillis()); } }
    static long generation() { synchronized (protocol) { return protocol.generation; } }
    static String status() {
        synchronized (protocol) { return (connected ? "입력 서비스 연결됨" : "접근성 서비스 꺼짐") + "\n" + lastInput + "\n" + protocol.result; }
    }
    static void reset() { synchronized (protocol) { protocol.reset(); } }
}
