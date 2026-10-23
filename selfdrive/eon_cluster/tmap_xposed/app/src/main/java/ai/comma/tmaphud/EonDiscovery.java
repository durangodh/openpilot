package ai.comma.tmaphud;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONObject;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetSocketAddress;

/**
 * carrot_navi_server.discovery_loop 이 1초마다 255.255.255.255:7705 로 쏘는
 * {"ip":...,"navi_debug":0} 브로드캐스트를 받아 EON IP 를 알아낸다.
 * 네이버·카카오 모듈도 같은 채널을 쓴다.
 */
final class EonDiscovery {
    /** EON 이 알려 준 HUD 네비모드(지도 가로 넓게 캡처) 여부. */
    static volatile boolean wideMap;
    /** 네비모드 HUD 지도 크기(휴대폰 앱 원본 축척 대비 비율, EON 설정 hud_map_scale_tmap). */
    static volatile float mapScale = 0.70f;
    /** EON 방송을 한 번이라도 받았는지. 받기 전에는 HUD 모드(wideMap)를 모른다. */
    static volatile boolean received;

    private static final int DISCOVERY_PORT = 7705;
    private static final String PREFS = "eon_hud_ui_settings";
    private static final String WIDE_MAP = "hud_wide_map";
    private static final String MAP_SCALE = "hud_map_scale_tmap";

    private final TmapNaviClient client;
    private volatile SharedPreferences prefs;
    private volatile boolean running = true;

    EonDiscovery(TmapNaviClient client) {
        this.client = client;
    }

    synchronized void initialize(Context context) {
        if (prefs != null) return;
        Context app = context.getApplicationContext();
        SharedPreferences saved = (app == null ? context : app)
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        if (received) {
            saved.edit().putBoolean(WIDE_MAP, wideMap)
                    .putInt(MAP_SCALE, Math.round(mapScale * 100f)).apply();
        } else {
            wideMap = saved.getBoolean(WIDE_MAP, false);
            mapScale = clampScale(saved.getInt(MAP_SCALE, 70)) / 100f;
            received = saved.contains(MAP_SCALE);
        }
        prefs = saved;
        TmapHudLog.line("restored EON HUD UI: wide=" + wideMap
                + " scale=" + Math.round(mapScale * 100f));
    }

    void start() {
        Thread t = new Thread(this::loop, "tmap-eon-discovery");
        t.setDaemon(true);
        t.start();
    }

    private void loop() {
        while (running) {
            DatagramSocket sock = null;
            try {
                // SO_REUSEADDR must be set before bind. Set after bind (as before)
                // it had no effect, so while the previous navigation app still
                // held 7705 during a HUD app switch this bind failed and the new
                // app waited for the retry below before it could find EON.
                sock = new DatagramSocket(null);
                sock.setReuseAddress(true);
                sock.bind(new InetSocketAddress(DISCOVERY_PORT));
                byte[] buf = new byte[512];
                while (running) {
                    DatagramPacket packet = new DatagramPacket(buf, buf.length);
                    sock.receive(packet);
                    try {
                        String text = new String(packet.getData(), 0, packet.getLength(), "UTF-8");
                        JSONObject obj = new JSONObject(text);
                        String ip = obj.optString("ip", "");
                        if (!ip.isEmpty()) {
                            client.setHost(ip);
                            applyEonSettings(obj);
                        }
                    } catch (Throwable ignored) {
                        // 다른 브로드캐스트는 무시.
                    }
                }
            } catch (Throwable t) {
                TmapHudLog.status("discovery bind retry: " + t.getClass().getSimpleName());
                // Short retry: during a nav-app switch the old app (or a patched
                // app without SO_REUSEADDR) can hold the port for a moment.
                try { Thread.sleep(500L); } catch (InterruptedException ignored) { }
            } finally {
                if (sock != null) {
                    try { sock.close(); } catch (Throwable ignored) { }
                }
            }
        }
    }

    /** 실제 EON UI 파라미터만 기억한다. ip 없는 로컬 방송은 호출되지 않는다. */
    private synchronized void applyEonSettings(JSONObject obj) {
        boolean previousWide = wideMap;
        int previousScale = Math.round(mapScale * 100f);
        wideMap = obj.optInt("hud_wide_map", previousWide ? 1 : 0) == 1;
        int scale = clampScale(obj.optInt("hud_map_scale_tmap", previousScale));
        mapScale = scale / 100f;
        received = true;
        SharedPreferences saved = prefs;
        if (saved != null && (!saved.contains(MAP_SCALE) || previousWide != wideMap
                || previousScale != scale)) {
            saved.edit().putBoolean(WIDE_MAP, wideMap).putInt(MAP_SCALE, scale).apply();
        }
    }

    private static int clampScale(int scale) {
        return Math.max(30, Math.min(200, scale));
    }
}
