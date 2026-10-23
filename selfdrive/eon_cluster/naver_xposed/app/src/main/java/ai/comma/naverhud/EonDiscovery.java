package ai.comma.naverhud;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONObject;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetSocketAddress;

/**
 * carrot_navi_server.discovery_loop 이 1초마다 255.255.255.255:7705 로 쏘는
 * {"ip":...,"navi_debug":0} 브로드캐스트를 받아 EON IP 를 알아낸다.
 * 네이버 모듈도 같은 채널을 쓴다.
 */
final class EonDiscovery {
    /** EON 이 알려 준 HUD 네비모드(지도 가로 넓게 캡처) 여부. */
    static volatile boolean wideMap;
    /** 네비모드 HUD 지도 크기(휴대폰 앱 원본 축척 대비 비율, EON 설정 hud_map_scale_naver). */
    static volatile float mapScale = 0.70f;

    private static final int DISCOVERY_PORT = 7705;
    private static final String PREFS = "eon_hud_ui_settings";
    private static final String WIDE_MAP = "hud_wide_map";
    private static final String MAP_SCALE = "hud_map_scale_naver";

    private final NaverNaviClient client;
    private final SharedPreferences prefs;
    private volatile boolean running = true;

    EonDiscovery(Context context, NaverNaviClient client) {
        this.client = client;
        Context app = context.getApplicationContext();
        prefs = (app == null ? context : app).getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        wideMap = prefs.getBoolean(WIDE_MAP, false);
        mapScale = clampScale(prefs.getInt(MAP_SCALE, 70)) / 100f;
        NaverHudLog.line("restored EON HUD UI: wide=" + wideMap
                + " scale=" + Math.round(mapScale * 100f));
    }

    void start() {
        Thread t = new Thread(this::loop, "naver-eon-discovery");
        t.setDaemon(true);
        t.start();
    }

    private void loop() {
        while (running) {
            DatagramSocket sock = null;
            try {
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
                            // ip가 있는 패킷만 실제 EON 방송이다. UI 파라미터값을 저장해
                            // 다음 부팅 때 EON 연결 전부터 같은 지도 크기로 시작한다.
                            boolean previousWide = wideMap;
                            int previousScale = Math.round(mapScale * 100f);
                            wideMap = obj.optInt("hud_wide_map", previousWide ? 1 : 0) == 1;
                            int scale = clampScale(obj.optInt("hud_map_scale_naver",
                                    previousScale));
                            mapScale = scale / 100f;
                            if (!prefs.contains(MAP_SCALE) || previousWide != wideMap
                                    || previousScale != scale) {
                                prefs.edit().putBoolean(WIDE_MAP, wideMap)
                                        .putInt(MAP_SCALE, scale).apply();
                            }
                        }
                    } catch (Throwable ignored) {
                        // 다른 브로드캐스트는 무시.
                    }
                }
            } catch (Throwable t) {
                NaverHudLog.status("discovery bind retry: " + t.getClass().getSimpleName());
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

    private static int clampScale(int scale) {
        return Math.max(30, Math.min(200, scale));
    }
}
