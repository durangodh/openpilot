package ai.comma.remotehud;

import android.os.SystemClock;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;
import org.json.JSONObject;

/** Forward display-only EON parameters to the phone-local Naver bridge. */
final class NaverSettingsRelay {
    private static long lastSend;
    static void update(JSONObject scene, DatagramSocket socket) {
        long now = SystemClock.elapsedRealtime();
        if (scene.optInt("hudNavApp", 1) != 2 || now - lastSend < 1000L) return;
        try {
            String text = "NHUD1," + value(scene, "hudNaverLandscape", 1, 0, 1)
                    + "," + value(scene, "hudNaverMapFit", 1, 0, 1)
                    + "," + value(scene, "hudNaverMapScale", 100, 50, 100)
                    + "," + value(scene, "hudNaverMapQuality", 90, 60, 95);
            byte[] bytes = text.getBytes(StandardCharsets.US_ASCII);
            socket.send(new DatagramPacket(bytes, bytes.length, InetAddress.getByName("127.0.0.1"), 28992));
            lastSend = now;
        } catch (Exception unavailable) {
            // Display tuning cannot interrupt EON telemetry or HUD rendering.
        }
    }
    private static int value(JSONObject scene, String key, int fallback, int min, int max) {
        return Math.max(min, Math.min(max, scene.optInt(key, fallback)));
    }
}
