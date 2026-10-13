package ai.comma.naverhud;

import org.json.JSONObject;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetSocketAddress;

/**
 * carrot_navi_server.discovery_loop 이 1초마다 255.255.255.255:7705 로 쏘는
 * {"ip":...,"navi_debug":0,"naver_map_zoom_out":0~100} 브로드캐스트를 받아
 * EON IP 와 지도 축소 비율을 알아낸다.
 * 네이버 모듈도 같은 채널을 쓴다.
 */
final class EonDiscovery {
    private static final int DISCOVERY_PORT = 7705;

    private final NaverNaviClient client;
    private volatile boolean running = true;

    EonDiscovery(NaverNaviClient client) {
        this.client = client;
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
                        }
                        if (obj.has("naver_map_zoom_out")) {
                            NaverMapCapture.setZoomOut(obj.optInt("naver_map_zoom_out", 0) / 100f);
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
}
