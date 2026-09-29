package ai.comma.kakaohud;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Host-side check: a delayed map handshake sends the newest frame without blocking guidance. */
public final class KakaoNaviClientQueueCheck {
    private static void handshake(Socket socket) throws Exception {
        InputStream in = socket.getInputStream();
        int matched = 0;
        byte[] end = {'\r', '\n', '\r', '\n'};
        while (matched < end.length) {
            int next = in.read();
            if (next < 0) throw new AssertionError("handshake closed");
            matched = next == end[matched] ? matched + 1 : 0;
        }
        OutputStream out = socket.getOutputStream();
        out.write("HTTP/1.1 101 Switching Protocols\r\n\r\n"
                .getBytes(StandardCharsets.US_ASCII));
        out.flush();
    }

    private static byte[] frame(Socket socket) throws Exception {
        InputStream in = socket.getInputStream();
        int opcode = in.read();
        int lengthByte = in.read();
        if (opcode < 0 || lengthByte < 0) throw new AssertionError("missing frame");
        int length = lengthByte & 0x7f;
        if (length >= 126) throw new AssertionError("test frame too long");
        byte[] mask = in.readNBytes(4);
        byte[] payload = in.readNBytes(length);
        for (int i = 0; i < payload.length; i++) payload[i] ^= mask[i & 3];
        return payload;
    }

    public static void main(String[] args) throws Exception {
        CountDownLatch releaseMap = new CountDownLatch(1);
        AtomicReference<byte[]> mapFrame = new AtomicReference<>();
        AtomicReference<Throwable> mapError = new AtomicReference<>();
        try (ServerSocket server = new ServerSocket(7714)) {
            server.setSoTimeout(3000);
            KakaoNaviClient client = new KakaoNaviClient();
            client.setHost("127.0.0.1");
            client.sendMap(new byte[]{0});
            Socket mapSocket = server.accept();
            mapSocket.setSoTimeout(3000);
            Thread mapServer = new Thread(() -> {
                try (Socket socket = mapSocket) {
                    // Keep the map sender in ensureMap while fresh frames arrive.
                    InputStream in = socket.getInputStream();
                    int matched = 0;
                    byte[] end = {'\r', '\n', '\r', '\n'};
                    while (matched < end.length) {
                        int next = in.read();
                        if (next < 0) throw new AssertionError("map handshake closed");
                        matched = next == end[matched] ? matched + 1 : 0;
                    }
                    if (!releaseMap.await(2, TimeUnit.SECONDS)) {
                        throw new AssertionError("map release timed out");
                    }
                    OutputStream out = socket.getOutputStream();
                    out.write("HTTP/1.1 101 Switching Protocols\r\n\r\n"
                            .getBytes(StandardCharsets.US_ASCII));
                    out.flush();
                    mapFrame.set(frame(socket));
                } catch (Throwable t) {
                    mapError.set(t);
                }
            });
            mapServer.start();

            for (int i = 1; i <= 20; i++) client.sendMap(new byte[]{(byte) i});
            client.sendState("guidance_current", "{}");
            try (Socket stateSocket = server.accept()) {
                stateSocket.setSoTimeout(3000);
                handshake(stateSocket);
                String state = new String(frame(stateSocket), StandardCharsets.UTF_8);
                if (!state.contains("guidance_current")) {
                    throw new AssertionError("guidance blocked by map: " + state);
                }
            }
            releaseMap.countDown();
            mapServer.join(3000);
            if (mapError.get() != null) throw new AssertionError(mapError.get());
            if (!Arrays.equals(mapFrame.get(), new byte[]{20})) {
                throw new AssertionError("stale map frame sent");
            }
        }
        System.out.println("KakaoNaviClientQueueCheck OK");
    }
}
