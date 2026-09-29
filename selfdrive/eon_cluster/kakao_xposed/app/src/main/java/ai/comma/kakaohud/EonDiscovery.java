package ai.comma.kakaohud;

import org.json.JSONObject;

import java.net.DatagramPacket;
import java.net.DatagramSocket;

/**
 * carrot_navi_server.discovery_loop 이 1초마다 255.255.255.255:7705 로 쏘는
 * {"ip":...,"navi_debug":0} 브로드캐스트를 받아 EON IP 를 알아낸다.
 * 네이버 브릿지도 같은 채널을 쓴다.
 */
final class EonDiscovery {
    private static final int DISCOVERY_PORT = 7705;

    private final KakaoNaviClient client;
    private volatile boolean running = true;

    EonDiscovery(KakaoNaviClient client) {
        this.client = client;
    }

    void start() {
        Thread t = new Thread(this::loop, "kakao-eon-discovery");
        t.setDaemon(true);
        t.start();
    }

    private void loop() {
        while (running) {
            DatagramSocket sock = null;
            try {
                sock = new DatagramSocket(DISCOVERY_PORT);
                sock.setReuseAddress(true);
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
                    } catch (Throwable ignored) {
                        // 다른 브로드캐스트는 무시.
                    }
                }
            } catch (Throwable t) {
                KakaoHudLog.status("discovery bind retry: " + t.getClass().getSimpleName());
                try { Thread.sleep(3000L); } catch (InterruptedException ignored) { }
            } finally {
                if (sock != null) {
                    try { sock.close(); } catch (Throwable ignored) { }
                }
            }
        }
    }
}
