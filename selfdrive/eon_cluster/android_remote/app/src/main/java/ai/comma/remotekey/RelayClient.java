package ai.comma.remotekey;

import android.content.Context;
import android.net.wifi.WifiManager;
import android.os.SystemClock;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONObject;

/**
 * Sends presses to EON over the S20 hotspot.
 *
 * EON's address comes from its 1 Hz discovery broadcast (UDP 7705, the same one
 * the navigation modules use) or from any reply. Until it is known, HELLO goes
 * out as a broadcast. The Wi-Fi lock is held only while EON answers, so the
 * phone sleeps normally away from the car.
 */
final class RelayClient {
    private static final int DISCOVERY_PORT = 7705;
    private static final long IDLE_WAIT_MS = 200;
    private static final long BUSY_WAIT_MS = 50;

    private final Context context;
    final RelayProtocol protocol = new RelayProtocol();
    private final ExecutorService sender = Executors.newSingleThreadExecutor();
    private volatile boolean running;
    private volatile InetAddress eon;
    private DatagramSocket socket;
    private WifiManager.WifiLock lowLatencyLock, highPerfLock;
    private Thread loop, discovery;

    RelayClient(Context context) { this.context = context.getApplicationContext(); }

    synchronized void start() {
        if (running) return;
        running = true;
        WifiManager wifi = (WifiManager) context.getSystemService(Context.WIFI_SERVICE);
        if (wifi != null) {
            // LOW_LATENCY only applies while this app is in front with the screen on;
            // HIGH_PERF keeps Wi-Fi power save off while the phone is locked in a pocket.
            lowLatencyLock = wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_LOW_LATENCY, "eon-remote-ll");
            highPerfLock = wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "eon-remote-hp");
            lowLatencyLock.setReferenceCounted(false);
            highPerfLock.setReferenceCounted(false);
        }
        loop = new Thread(this::run, "eon-remote-relay");
        loop.setDaemon(true);
        loop.start();
        discovery = new Thread(this::discover, "eon-remote-discovery");
        discovery.setDaemon(true);
        discovery.start();
    }

    synchronized void stop() {
        running = false;
        DatagramSocket s = socket;
        if (s != null) s.close();
        updateWifiLock(false);
    }

    private String key() { return RemoteControl.prefs(context).getString("secret", ""); }

    /** Called on the main thread for a recognised press; the first send happens at once. */
    void submit(String action) {
        boolean queued;
        synchronized (protocol) { queued = protocol.submit(action, SystemClock.uptimeMillis()); }
        if (queued) sender.execute(this::flush);
    }

    private void send(byte[] data, InetAddress target) {
        DatagramSocket s = socket;
        if (data == null || s == null) return;
        try {
            InetAddress to = target != null ? target : InetAddress.getByName("255.255.255.255");
            s.send(new DatagramPacket(data, data.length, to, RelayProtocol.PORT));
        } catch (Exception ignored) {
            // No Wi-Fi yet or the hotspot went away; the loop keeps trying.
        }
    }

    private void flush() {
        long now = SystemClock.uptimeMillis();
        String key = key();
        boolean remote = RemoteControl.remotePresent(context);
        byte[] hello, command;
        synchronized (protocol) {
            command = protocol.command(key, now);
            hello = command == null ? protocol.hello(key, now, remote) : null;
        }
        InetAddress target = eon;
        send(command, target);
        send(hello, target);
    }

    private void run() {
        byte[] buffer = new byte[512];
        while (running) {
            try {
                if (socket == null) {
                    socket = new DatagramSocket();
                    socket.setBroadcast(true);
                }
                flush();
                long now = SystemClock.uptimeMillis();
                boolean busy, seen;
                synchronized (protocol) {
                    busy = protocol.pending();
                    seen = protocol.eonSeen(now);
                }
                updateWifiLock(seen);
                socket.setSoTimeout((int) (busy ? BUSY_WAIT_MS : IDLE_WAIT_MS));
                DatagramPacket packet = new DatagramPacket(buffer, buffer.length);
                try {
                    socket.receive(packet);
                } catch (SocketTimeoutException timeout) {
                    continue;
                }
                if (packet.getPort() != RelayProtocol.PORT) continue;
                String text = new String(packet.getData(), 0, packet.getLength(), StandardCharsets.US_ASCII);
                boolean fromEon;
                synchronized (protocol) { fromEon = protocol.receive(key(), text, SystemClock.uptimeMillis()); }
                if (fromEon) eon = packet.getAddress();
            } catch (Exception e) {
                DatagramSocket s = socket;
                socket = null;
                if (s != null) s.close();
                SystemClock.sleep(1000);
            }
        }
    }

    private void updateWifiLock(boolean seen) {
        for (WifiManager.WifiLock lock : new WifiManager.WifiLock[]{lowLatencyLock, highPerfLock}) {
            if (lock == null) continue;
            if (seen && !lock.isHeld()) lock.acquire();
            else if (!seen && lock.isHeld()) lock.release();
        }
    }

    private void discover() {
        byte[] buffer = new byte[1024];
        while (running) {
            DatagramSocket s = null;
            try {
                s = new DatagramSocket(null);
                s.setReuseAddress(true);
                s.bind(new InetSocketAddress(DISCOVERY_PORT));
                s.setSoTimeout(5000);
                while (running) {
                    DatagramPacket packet = new DatagramPacket(buffer, buffer.length);
                    try {
                        s.receive(packet);
                    } catch (SocketTimeoutException timeout) {
                        continue;
                    }
                    try {
                        String ip = new JSONObject(new String(packet.getData(), 0, packet.getLength(),
                                StandardCharsets.UTF_8)).optString("ip", "");
                        if (!ip.isEmpty()) eon = InetAddress.getByName(ip);
                    } catch (Exception ignored) {
                        // Other broadcasts on the same port.
                    }
                }
            } catch (Exception e) {
                SystemClock.sleep(2000);
            } finally {
                if (s != null) s.close();
            }
        }
    }
}
