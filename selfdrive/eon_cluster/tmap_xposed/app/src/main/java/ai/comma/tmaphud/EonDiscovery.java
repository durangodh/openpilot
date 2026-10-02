package ai.comma.tmaphud;

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
    private static final int DISCOVERY_PORT = 7705;

    private final TmapNaviClient client;
    private volatile boolean running = true;

    EonDiscovery(TmapNaviClient client) {
        this.client = client;
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
}
