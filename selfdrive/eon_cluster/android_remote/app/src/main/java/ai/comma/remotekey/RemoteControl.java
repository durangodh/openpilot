package ai.comma.remotekey;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.SystemClock;
import android.view.InputDevice;
import android.view.KeyEvent;

/** Shared state between the accessibility service and the settings screen. */
final class RemoteControl {
    static final String[] ACTIONS = {"none", "res", "set", "gap", "cancel", "nav_toggle", "lane_left", "lane_right",
            "res_long", "set_long", "gap_long"};
    static final String[] LABELS = {"사용 안 함", "RES / 속도 +1", "SET / 속도 −1", "차간거리", "ACC 취소 (조향 유지)",
            "티맵 ↔ 네이버", "왼쪽 차선변경 요청", "오른쪽 차선변경 요청",
            "RES 길게 (속도 +10)", "SET 길게 (속도 −10)", "GAP 길게 (1.5초, 티맵 ↔ 네이버)"};
    static volatile boolean configuring = false, connected = false;
    static volatile int learnedKey = -1;
    static volatile String lastInput = "버튼 입력 대기";
    /** Presses that arrived while the phone was locked (lock-screen test). */
    static volatile int lockedInputs = 0;
    static volatile RelayClient client;

    static SharedPreferences prefs(Context c) { return c.getSharedPreferences("key_remote", Context.MODE_PRIVATE); }

    static boolean external(InputDevice d) { return d != null && !d.isVirtual() && d.isExternal(); }

    /** One remote can appear as several input devices (keyboard, mouse, consumer
     *  control); they share vendor/product IDs, so any of them counts. */
    static String vidPid(InputDevice d) { return d.getVendorId() + ":" + d.getProductId(); }

    static boolean selected(Context c, InputDevice d) {
        if (!external(d)) return false;
        SharedPreferences p = prefs(c);
        String id = p.getString("deviceVidPid", "");
        return d.getDescriptor().equals(p.getString("device", "")) || (!id.isEmpty() && !id.equals("0:0") && id.equals(vidPid(d)));
    }

    /** Remember the device a learned key came from; mappings stay for the same remote. */
    static void adopt(Context c, InputDevice d) {
        SharedPreferences p = prefs(c);
        if (d.getDescriptor().equals(p.getString("device", "")) && vidPid(d).equals(p.getString("deviceVidPid", ""))) return;
        SharedPreferences.Editor e = p.edit();
        if (!vidPid(d).equals(p.getString("deviceVidPid", ""))) {
            // A different remote: its key codes need learning again.
            String secret = p.getString("secret", "");
            boolean enabled = p.getBoolean("enabled", false);
            e.clear().putString("secret", secret).putBoolean("enabled", enabled);
        }
        e.putString("device", d.getDescriptor()).putString("deviceName", d.getName())
                .putString("deviceVidPid", vidPid(d)).apply();
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
        RelayClient relay = client;
        if (enabled(c) && relay != null) relay.submit(action);
    }

    static boolean drivingAllowed() {
        RelayClient relay = client;
        if (relay == null) return false;
        synchronized (relay.protocol) { return relay.protocol.drive; }
    }

    static String status() {
        RelayClient relay = client;
        String eon = "리모컨 입력 서비스 꺼짐 (접근성에서 켜세요)";
        if (relay != null) {
            synchronized (relay.protocol) {
                eon = relay.protocol.result
                        + (relay.protocol.eonSeen(SystemClock.uptimeMillis())
                        ? (relay.protocol.drive ? "\n차선변경 가능 (D단)" : "\n차선변경 대기 (D단 아님/페달)") : "");
            }
        }
        return eon + "\n마지막 입력: " + lastInput + "\n잠금 중 받은 입력: " + lockedInputs + "회";
    }
}
