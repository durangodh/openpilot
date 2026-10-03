package ai.comma.naverhud;

import android.os.SystemClock;

import java.io.BufferedOutputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/**
 * 같은 S9 에서 도는 HUD 앱(127.0.0.1:7213)으로 지도 JPEG 를 바로 보낸다.
 *
 * 예전에는 지도가 S9 → (와이파이) → EON → (와이파이) → S9 로 돌아왔다. EON 은 이 지도를
 * 쓰지 않고 HUD 로 되돌려 주기만 해서, 큰 지도 프레임만 와이파이에서 막혀 HUD 지도가
 * 멈추곤 했다(속도·방향 같은 작은 정보는 계속 갱신). 이 경로는 와이파이를 거치지 않는다.
 * HUD 는 이 지도가 들어오는 동안 EON 쪽 지도를 무시하고, 끊기면 EON 지도로 돌아간다.
 *
 * 형식: "MAPL" + 앱 번호(1 TMAP, 2 네이버, 3 카카오) + 길이(int, big-endian) + JPEG.
 * 길이 0 은 지도 지우기. 최신 한 장만 보내고, HUD 가 없으면 2초마다 다시 연결해 본다.
 */
final class LocalHudMap {
    private static final int PORT = 7213;
    private static final long RETRY_MS = 2000;

    private final byte app;
    private final AtomicReference<byte[]> pending = new AtomicReference<>();
    private final AtomicBoolean scheduled = new AtomicBoolean();
    private final ExecutorService sender = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "naver-hud-local-map");
        t.setDaemon(true);
        return t;
    });
    private Socket socket;
    private OutputStream out;
    private long retryAt;
    private boolean loggedConnect;
    // 10초마다 지도 프레임이 나온 개수와 가장 긴 공백을 로그에 남긴다. 공백이 크면
    // 네비 앱에서 지도를 찍는 단계가 멈춘 것이다(전송 경로와 무관).
    private static final long STATS_MS = 10000;
    private long statsAt, lastFrameAt, frames, maxGapMs, localSent, localFail;

    LocalHudMap(int app) {
        this.app = (byte) app;
    }

    void offer(byte[] jpeg) {
        if (jpeg == null) return;
        stats(jpeg.length > 0);
        pending.set(jpeg);
        if (scheduled.compareAndSet(false, true)) sender.execute(this::drain);
    }

    void offerClear() {
        offer(new byte[0]);
    }

    private synchronized void stats(boolean frame) {
        long now = SystemClock.elapsedRealtime();
        if (statsAt == 0) statsAt = now;
        if (frame) {
            if (lastFrameAt != 0) maxGapMs = Math.max(maxGapMs, now - lastFrameAt);
            lastFrameAt = now;
            frames++;
        }
        if (now - statsAt >= STATS_MS) {
            long gap = lastFrameAt != 0 ? Math.max(maxGapMs, now - lastFrameAt) : 0;
            NaverHudLog.line("map frames " + ((now - statsAt) / 1000) + "s: " + frames + " (max gap " + gap
                    + "ms) | local HUD sent " + localSent + " fail " + localFail
                    + (out != null ? " (connected)" : " (not connected)"));
            statsAt = now;
            frames = 0;
            maxGapMs = 0;
            localSent = 0;
            localFail = 0;
        }
    }

    private void drain() {
        try {
            byte[] frame;
            while ((frame = pending.getAndSet(null)) != null) send(frame);
        } finally {
            scheduled.set(false);
            if (pending.get() != null && scheduled.compareAndSet(false, true)) sender.execute(this::drain);
        }
    }

    private void send(byte[] jpeg) {
        long now = SystemClock.elapsedRealtime();
        if (out == null) {
            if (now < retryAt) return;
            try {
                Socket s = new Socket();
                s.setTcpNoDelay(true);
                s.connect(new InetSocketAddress("127.0.0.1", PORT), 300);
                s.setSoTimeout(1000);
                socket = s;
                out = new BufferedOutputStream(s.getOutputStream(), 1 << 16);
                if (!loggedConnect) {
                    loggedConnect = true;
                    NaverHudLog.line("local HUD map connected");
                }
            } catch (Throwable t) {
                close();
                retryAt = now + RETRY_MS;
                return;
            }
        }
        try {
            ByteBuffer head = ByteBuffer.allocate(9);
            head.put((byte) 'M').put((byte) 'A').put((byte) 'P').put((byte) 'L');
            head.put(app).putInt(jpeg.length);
            out.write(head.array());
            out.write(jpeg);
            out.flush();
            localSent++;
        } catch (Throwable t) {
            localFail++;
            close();
            retryAt = now + 500;
        }
    }

    private void close() {
        try { if (socket != null) socket.close(); } catch (Throwable ignored) { }
        socket = null;
        out = null;
    }
}
