package ai.comma.kakaohud;

import android.util.Base64;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.Random;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/**
 * carrot_navi_server(7714) 로 카카오 안내/지도 데이터를 보내는 최소 WebSocket
 * 클라이언트. 서버가 경로의 "/kakao/" 로 소스를 구분하므로 그 경로로 접속한다.
 *
 * - 상태(JSON) 스트림과 지도(바이너리) 스트림 각각 소켓 1개.
 * - EON IP 는 7705 UDP 디스커버리 브로드캐스트({"ip":...})로 받는다.
 * - 서버 프로토콜: 텍스트 item_update JSON, 지도는 opcode 2 바이너리 프레임.
 */
final class KakaoNaviClient {
    private static final int PORT = 7714;
    private static final String WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
    private static final String STATE_PATH = "/api/navi/ws/v2/json/kakao/state";
    private static final String MAP_PATH = "/api/navi/ws/v2/render/kakao/map_main";
    private static final String SIGNAL_PATH = "/api/navi/ws/v2/render/kakao/traffic_signal";
    private static final String IMAGE_PATH = "/api/navi/ws/v2/image/kakao/";

    private final Object stateLock = new Object();
    private final Object mapLock = new Object();
    private final LocalHudMap localMap = new LocalHudMap(3);
    // HUD 가 지도를 S9 안에서 직접 받는 동안에는 EON 으로 가는 지도를 초당 1장으로 줄인다.
    // EON 은 이 지도를 HUD 로 되돌려 주기만 하고(직접 받는 동안 HUD 는 무시), 초당 5장이
    // 와이파이를 두 번 지나가며 핫스팟을 막아 지도가 멈췄다 한 번에 따라잡는 원인이었다.
    // 1장은 직접 경로가 끊길 때를 위한 예비이자 EON 의 지도 끊김 판정(3초)용이다.
    private static final long EON_MAP_BACKUP_MS = 1000;
    private long lastEonMapAt;

    private boolean skipEonMap() {
        long now = android.os.SystemClock.elapsedRealtime();
        if (localMap.delivering() && now - lastEonMapAt < EON_MAP_BACKUP_MS) return true;
        lastEonMapAt = now;
        return false;
    }
    private volatile String host;
    private Socket stateSock;
    private OutputStream stateOut;
    private Socket mapSock;
    private OutputStream mapOut;
    private Socket sigSock;
    private OutputStream sigOut;
    private final Object sigLock = new Object();
    private final Random rnd = new Random();
    // Xposed callbacks run on KakaoNavi threads. Never perform socket I/O there:
    // a reconnect or handshake may take seconds and must not stall navigation.
    private final ThreadPoolExecutor stateSender = new ThreadPoolExecutor(
            1, 1, 0L, TimeUnit.MILLISECONDS,
            new ArrayBlockingQueue<Runnable>(16),
            runnable -> {
                Thread thread = new Thread(runnable, "kakao-hud-sender");
                thread.setDaemon(true);
                return thread;
            },
            new ThreadPoolExecutor.DiscardOldestPolicy());
    // Map frames are disposable. Keep only the newest one, on a separate worker,
    // so a slow map socket cannot delay guidance and old frames cannot queue up.
    private final ExecutorService mapSender = Executors.newSingleThreadExecutor(runnable -> {
        Thread thread = new Thread(runnable, "kakao-hud-map-sender");
        thread.setDaemon(true);
        return thread;
    });
    private final AtomicReference<byte[]> pendingMap = new AtomicReference<>();
    private final AtomicBoolean mapDrainScheduled = new AtomicBoolean(false);
    // 신호등 PNG. 지도와 같은 "최신 1장만" 정책.
    private final AtomicReference<byte[]> pendingSignal = new AtomicReference<>();
    private final AtomicBoolean signalDrainScheduled = new AtomicBoolean(false);

    // 교차로 확대 이미지처럼 드물게 바뀌는 그림. 이름마다 소켓 하나, 최신 1장만.
    private final java.util.concurrent.ConcurrentHashMap<String, ImageChannel> images =
            new java.util.concurrent.ConcurrentHashMap<>();

    void setHost(String ip) {
        if (ip != null && !ip.isEmpty() && !ip.equals(host)) {
            host = ip;
            KakaoHudLog.line("EON host = " + ip);
            closeAll();
        }
    }

    boolean ready() {
        return host != null;
    }

    /** 안내 상태를 item_update JSON 텍스트 프레임으로 보낸다. */
    void sendState(final String name, final String jsonValue) {
        stateSender.execute(() -> sendStateNow(name, jsonValue));
    }

    private void sendStateNow(String name, String jsonValue) {
        try {
            OutputStream out = ensureState();
            if (out == null) return;
            String msg = "{\"type\":\"item_update\",\"name\":\"" + name
                    + "\",\"present\":true,\"value\":" + jsonValue + "}";
            synchronized (stateLock) {
                writeFrame(out, msg.getBytes("UTF-8"), 1);
            }
        } catch (Throwable t) {
            KakaoHudLog.ex("sendState", t);
            closeState();
        }
    }

    /** 지도 JPEG 을 opcode 2 바이너리 프레임으로 보낸다(네이버 HUD14 와 동일 수용 경로). */
    void sendMap(final byte[] jpeg) {
        if (jpeg == null || jpeg.length == 0) return;
        localMap.offer(jpeg);   // 같은 S9 의 HUD 앱으로도 바로 보낸다(와이파이 왕복 없음)
        if (skipEonMap()) return;
        pendingMap.set(jpeg);
        scheduleMapDrain();
    }

    private void scheduleMapDrain() {
        if (mapDrainScheduled.compareAndSet(false, true)) {
            mapSender.execute(this::drainMap);
        }
    }

    private void drainMap() {
        try {
            byte[] jpeg;
            while ((jpeg = pendingMap.getAndSet(null)) != null) {
                sendMapNow(jpeg);
            }
        } finally {
            mapDrainScheduled.set(false);
            if (pendingMap.get() != null) scheduleMapDrain();
        }
    }

    /** 신호등 PNG(CNV2 헤더 포함)를 traffic_signal 오버레이 소켓으로 보낸다. */
    void sendSignal(final byte[] framed) {
        if (framed == null || framed.length == 0) return;
        pendingSignal.set(framed);
        if (signalDrainScheduled.compareAndSet(false, true)) {
            signalSender.execute(this::drainSignal);
        }
    }

    // 신호등은 지도와 따로 보낸다. 같은 스레드면 지도 재연결(최대 3초)마다 신호 숫자가 멈췄다.
    private final ExecutorService signalSender = Executors.newSingleThreadExecutor(runnable -> {
        Thread thread = new Thread(runnable, "kakao-hud-signal-sender");
        thread.setDaemon(true);
        return thread;
    });

    private void drainSignal() {
        try {
            byte[] f;
            while ((f = pendingSignal.getAndSet(null)) != null) sendSignalNow(f);
        } finally {
            signalDrainScheduled.set(false);
            if (pendingSignal.get() != null && signalDrainScheduled.compareAndSet(false, true)) {
                signalSender.execute(this::drainSignal);
            }
        }
    }

    private void sendSignalNow(byte[] framed) {
        try {
            OutputStream out = ensureSignal();
            if (out == null) return;
            synchronized (sigLock) {
                writeFrame(out, framed, 2);
            }
        } catch (Throwable t) {
            KakaoHudLog.ex("sendSignal", t);
            closeSignal();
        }
    }

    private OutputStream ensureSignal() throws Exception {
        if (sigOut != null) return sigOut;
        if (host == null) return null;
        Socket s = connect(SIGNAL_PATH);
        if (s == null) return null;
        sigSock = s;
        sigOut = s.getOutputStream();
        KakaoHudLog.line("signal socket connected");
        return sigOut;
    }

    private void closeSignal() {
        try { if (sigSock != null) sigSock.close(); } catch (Throwable ignored) { }
        sigSock = null; sigOut = null;
    }

    private void sendMapNow(byte[] jpeg) {
        try {
            OutputStream out = ensureMap();
            if (out == null) return;
            // A reconnect can take seconds. Send the latest frame available after
            // connecting, rather than the frame that triggered the reconnect.
            byte[] latest = pendingMap.getAndSet(null);
            if (latest != null) jpeg = latest;
            synchronized (mapLock) {
                writeFrame(out, jpeg, 2);
            }
        } catch (Throwable t) {
            KakaoHudLog.ex("sendMap", t);
            closeMap();
        }
    }

    private OutputStream ensureState() throws Exception {
        if (stateOut != null) return stateOut;
        if (host == null) return null;
        Socket s = connect(STATE_PATH);
        if (s == null) return null;
        stateSock = s;
        stateOut = s.getOutputStream();
        KakaoHudLog.line("state socket connected");
        return stateOut;
    }

    private OutputStream ensureMap() throws Exception {
        if (mapOut != null) return mapOut;
        if (host == null) return null;
        Socket s = connect(MAP_PATH);
        if (s == null) return null;
        mapSock = s;
        mapOut = s.getOutputStream();
        KakaoHudLog.line("map socket connected");
        return mapOut;
    }

    private Socket connect(String path) throws Exception {
        String h = host;
        if (h == null) return null;
        Socket s = new Socket();
        s.connect(new InetSocketAddress(h, PORT), 3000);
        s.setSoTimeout(3000);
        s.setTcpNoDelay(true);
        OutputStream out = s.getOutputStream();
        InputStream in = s.getInputStream();

        byte[] keyBytes = new byte[16];
        rnd.nextBytes(keyBytes);
        String key = Base64.encodeToString(keyBytes, Base64.NO_WRAP);
        String req = "GET " + path + " HTTP/1.1\r\n"
                + "Host: " + h + ":" + PORT + "\r\n"
                + "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                + "Sec-WebSocket-Key: " + key + "\r\n"
                + "Sec-WebSocket-Version: 13\r\n\r\n";
        out.write(req.getBytes("ISO-8859-1"));
        out.flush();

        // 응답 헤더를 \r\n\r\n 까지 읽고 101 인지 확인.
        StringBuilder resp = new StringBuilder();
        int prev = 0, cur;
        int guard = 0;
        while ((cur = in.read()) != -1 && guard++ < 8192) {
            resp.append((char) cur);
            if (prev == '\r' && cur == '\n' && resp.length() >= 4
                    && resp.substring(resp.length() - 4).equals("\r\n\r\n")) {
                break;
            }
            prev = cur;
        }
        String header = resp.toString();
        if (!header.contains(" 101 ")) {
            KakaoHudLog.line("ws handshake failed: " + header.split("\r\n")[0]);
            s.close();
            return null;
        }
        // Accept 검증은 생략(EON 서버는 신뢰 대상). 헤더만 소비하면 프레임 준비 완료.
        s.setSoTimeout(0);
        return s;
    }

    /** 클라이언트→서버 프레임은 마스킹 필수(RFC 6455). */
    private void writeFrame(OutputStream out, byte[] payload, int opcode) throws Exception {
        int len = payload.length;
        byte[] header;
        int hlen;
        if (len < 126) {
            header = new byte[2];
            header[1] = (byte) (0x80 | len);
            hlen = 2;
        } else if (len < 65536) {
            header = new byte[4];
            header[1] = (byte) (0x80 | 126);
            header[2] = (byte) ((len >> 8) & 0xff);
            header[3] = (byte) (len & 0xff);
            hlen = 4;
        } else {
            header = new byte[10];
            header[1] = (byte) (0x80 | 127);
            long wireLength = len & 0xffffffffL;
            for (int i = 0; i < 8; i++) {
                header[9 - i] = (byte) ((wireLength >>> (8 * i)) & 0xff);
            }
            hlen = 10;
        }
        header[0] = (byte) (0x80 | (opcode & 0x0f));

        byte[] mask = new byte[4];
        rnd.nextBytes(mask);
        byte[] masked = new byte[len];
        for (int i = 0; i < len; i++) {
            masked[i] = (byte) (payload[i] ^ mask[i & 3]);
        }
        out.write(header, 0, hlen);
        out.write(mask);
        out.write(masked);
        out.flush();
    }

    private void closeState() {
        try { if (stateSock != null) stateSock.close(); } catch (Throwable ignored) { }
        stateSock = null; stateOut = null;
    }

    private void closeMapAndSignalOnReset() { closeSignal(); }

    private void closeMap() {
        try { if (mapSock != null) mapSock.close(); } catch (Throwable ignored) { }
        mapSock = null; mapOut = null;
        closeSignal();
    }

    private void closeAll() {
        synchronized (stateLock) { closeState(); }
        synchronized (mapLock) { closeMap(); }
        for (ImageChannel channel : images.values()) channel.close();
    }

    /** CNV2 프레임을 /api/navi/ws/v2/image/kakao/{name} 오버레이 소켓으로 보낸다. */
    void sendImage(String name, byte[] framed) {
        if (name == null || framed == null || framed.length == 0) return;
        ImageChannel channel = images.get(name);
        if (channel == null) {
            ImageChannel created = new ImageChannel(name);
            channel = images.putIfAbsent(name, created);
            if (channel == null) channel = created;
        }
        channel.pending.set(framed);
        channel.schedule();
    }

    private final class ImageChannel {
        final String name;
        final AtomicReference<byte[]> pending = new AtomicReference<>();
        final AtomicBoolean scheduled = new AtomicBoolean(false);
        // 그림마다 따로 보낸다. 지도 전송 스레드를 같이 쓰면 큰 PNG·재연결 동안 지도가 멈췄다.
        final ExecutorService sender;
        Socket sock;
        OutputStream out;

        ImageChannel(String name) {
            this.name = name;
            this.sender = Executors.newSingleThreadExecutor(runnable -> {
                Thread thread = new Thread(runnable, "kakao-hud-image-" + name);
                thread.setDaemon(true);
                return thread;
            });
        }

        void schedule() {
            if (scheduled.compareAndSet(false, true)) sender.execute(this::drain);
        }

        void drain() {
            try {
                byte[] framed;
                while ((framed = pending.getAndSet(null)) != null) sendNow(framed);
            } finally {
                scheduled.set(false);
                if (pending.get() != null) schedule();
            }
        }

        synchronized void sendNow(byte[] framed) {
            try {
                if (out == null) {
                    if (host == null) return;
                    Socket s = connect(IMAGE_PATH + name);
                    if (s == null) return;
                    sock = s;
                    out = s.getOutputStream();
                    KakaoHudLog.line(name + " socket connected");
                }
                writeFrame(out, framed, 2);
            } catch (Throwable t) {
                KakaoHudLog.ex("sendImage " + name, t);
                close();
            }
        }

        synchronized void close() {
            try { if (sock != null) sock.close(); } catch (Throwable ignored) { }
            sock = null;
            out = null;
        }
    }
}
