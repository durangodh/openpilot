package ai.comma.tmaphud;

import android.os.SystemClock;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * TMAP version of the Naver car marker (2026-11-24): tells the HUD where the car sits in the
 * TMAP HUD map frame so the HUD draws its own car there (lamps, turn signals, brake, BSD) and
 * this module hides TMAP's location icon.
 *
 * CAR3 request: int app (1 = TMAP), int placement (car y in thousandths of the map frame, or -1
 * when the car is not at the camera pivot), int relative bearing in tenths of a degree.
 * Reply: int (0 = HUD draws the car, -1 = it does not). An older HUD closes the socket and the
 * map keeps TMAP's own icon.
 */
final class TmapCarMarker {
    private static final int PORT = 7213;
    private static final int REQUEST = 0x43415233; // CAR3

    private final ExecutorService reader = Executors.newSingleThreadExecutor(r -> {
        Thread thread = new Thread(r, "tmap-hud-car-marker");
        thread.setDaemon(true);
        return thread;
    });
    private final AtomicBoolean fetching = new AtomicBoolean();
    private volatile long nextPoll;
    private volatile long lastReply;
    private volatile boolean hudDraws;
    private volatile int placement = -1;
    private volatile int bearing;
    private Socket socket;
    private DataOutputStream output;
    private DataInputStream input;

    /** Main thread. Publishes the car placement and returns the HUD's last answer. */
    boolean hudDraws(int placement, int bearing) {
        this.placement = placement;
        this.bearing = bearing;
        long now = SystemClock.elapsedRealtime();
        if (now >= nextPoll && fetching.compareAndSet(false, true)) {
            nextPoll = now + 100;
            reader.execute(this::poll);
        }
        return placement >= 0 && hudDraws && lastReply != 0 && now - lastReply < 800;
    }

    private void poll() {
        try {
            if (socket == null) {
                socket = new Socket();
                socket.connect(new InetSocketAddress("127.0.0.1", PORT), 250);
                socket.setTcpNoDelay(true);
                socket.setSoTimeout(350);
                output = new DataOutputStream(socket.getOutputStream());
                input = new DataInputStream(socket.getInputStream());
            }
            output.writeInt(REQUEST);
            output.writeInt(1);
            output.writeInt(placement);
            output.writeInt(bearing);
            output.flush();
            int reply = input.readInt();
            if (reply != 0 && reply != -1) throw new IOException("invalid car marker reply");
            hudDraws = reply == 0;
            lastReply = SystemClock.elapsedRealtime();
        } catch (IOException | RuntimeException error) {
            if (socket != null) {
                try { socket.close(); } catch (IOException ignored) { }
            }
            socket = null;
            hudDraws = false;
            nextPoll = SystemClock.elapsedRealtime() + 2000;
        } finally {
            fetching.set(false);
        }
    }
}
