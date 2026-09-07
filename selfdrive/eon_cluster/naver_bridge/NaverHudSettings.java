package com.naver.map.carrot;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;

/** Versioned, local-only settings from Remote HUD. No navigation/control data. */
public final class NaverHudSettings {
    public static final int PORT = 28992;
    static final class Values {
        final boolean landscape, fit;
        final int scale, quality;
        Values(boolean landscape, boolean fit, int scale, int quality) {
            this.landscape = landscape; this.fit = fit; this.scale = scale; this.quality = quality;
        }
    }
    static volatile Values current = new Values(true, true, 100, 90);
    private static boolean started;

    static Values parse(String text) {
        String[] fields = text.split(",", -1);
        if (fields.length != 5 || !"NHUD1".equals(fields[0])) return null;
        try {
            int landscape = Integer.parseInt(fields[1]), fit = Integer.parseInt(fields[2]);
            int scale = Integer.parseInt(fields[3]), quality = Integer.parseInt(fields[4]);
            if (landscape < 0 || landscape > 1 || fit < 0 || fit > 1
                    || scale < 50 || scale > 100 || quality < 60 || quality > 95) return null;
            return new Values(landscape == 1, fit == 1, scale, quality);
        } catch (NumberFormatException invalid) { return null; }
    }

    public static int quality() { return current.quality; }

    static synchronized void start() {
        if (started) return;
        started = true;
        Thread thread = new Thread(() -> {
            try (DatagramSocket socket = new DatagramSocket(
                    new InetSocketAddress(InetAddress.getByName("127.0.0.1"), PORT))) {
                byte[] buffer = new byte[128];
                while (!Thread.currentThread().isInterrupted()) {
                    DatagramPacket packet = new DatagramPacket(buffer, buffer.length);
                    socket.receive(packet);
                    Values values = parse(new String(packet.getData(), packet.getOffset(),
                            packet.getLength(), StandardCharsets.US_ASCII));
                    if (values != null) current = values;
                }
            } catch (Exception unavailable) {
                // Keep safe defaults/current values and retry at a later capture.
            } finally {
                synchronized (NaverHudSettings.class) { started = false; }
            }
        }, "naver-hud-settings");
        thread.setDaemon(true);
        thread.start();
    }
}
