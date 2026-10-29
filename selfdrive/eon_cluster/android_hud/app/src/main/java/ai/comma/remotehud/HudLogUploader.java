package ai.comma.remotehud;

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * HUD 진단 로그(hud-session*.log)를 EON 7714 로 올린다. 폰 브라우저에서
 * http://EON_IP:7714/trace 의 "HUD 앱 로그" 에서 내려받을 수 있다.
 * 바뀐 파일만, 평소 1분마다, USB 이상이 기록되면 10초 안에 올린다.
 */
final class HudLogUploader {
    static final String[] NAMES = {"hud-session.log", "hud-session.previous.log"};
    static final long PERIOD_MS = 60_000L;
    static final long URGENT_MS = 10_000L;
    private static final int PORT = 7714;
    private static final int MAX_BYTES = 1024 * 1024;

    /** 네비 모듈 로그(안전운전 코드 기록 등). 각 네비 앱 폴더라 루트로 읽는다. */
    static final String[][] MODULE_LOGS = {
            {"naver-hud.log", "/sdcard/Android/data/com.nhn.android.nmap/files/naver_hud.log"},
            {"tmap-hud.log", "/sdcard/Android/data/com.skt.tmap.ku/files/tmap_hud.log"},
            {"kakao-hud.log", "/sdcard/Android/data/com.locnall.KimGiSa/files/kakao_hud.log"},
    };

    private final File dir;
    private final long[] sentStamp = new long[NAMES.length];
    private final String[] moduleStamp = {"", "", ""};

    HudLogUploader(File dir) {
        this.dir = dir;
    }

    /** EON 주소가 없거나 실패하면 다음에 다시 시도한다. */
    void uploadChanged(String eonIp) {
        if (eonIp == null || eonIp.isEmpty() || "--".equals(eonIp)) return;
        for (int i = 0; i < NAMES.length; i++) {
            File file = new File(dir, NAMES[i]);
            if (!file.isFile()) continue;
            long stamp = file.lastModified() * 31 + file.length();
            if (stamp == sentStamp[i]) continue;
            if (upload(eonIp, NAMES[i], read(file))) sentStamp[i] = stamp;
        }
        for (int i = 0; i < MODULE_LOGS.length; i++) {
            uploadModuleLog(eonIp, i);
        }
    }

    private void uploadModuleLog(String eonIp, int index) {
        String path = MODULE_LOGS[index][1];
        byte[] raw = suRead("stat -c %s_%Y " + path, 64);
        String stamp = raw == null ? "" : new String(raw, java.nio.charset.StandardCharsets.UTF_8).trim();
        if (stamp.isEmpty() || stamp.equals(moduleStamp[index])) return;
        byte[] body = suRead("tail -c " + MAX_BYTES + " " + path, MAX_BYTES);
        if (body != null && body.length > 0 && upload(eonIp, MODULE_LOGS[index][0], body)) {
            moduleStamp[index] = stamp;
        }
    }

    /** Root command stdout, at most max bytes, null on failure or after 5 s. */
    private static byte[] suRead(String command, int max) {
        Process process = null;
        try {
            process = Runtime.getRuntime().exec(new String[] {"su", "-c", command});
            final Process running = process;
            final java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
            Thread reader = new Thread(() -> {
                try (InputStream in = running.getInputStream()) {
                    byte[] buffer = new byte[8192];
                    int n;
                    while ((n = in.read(buffer)) >= 0) {
                        if (out.size() + n > max) n = max - out.size();
                        if (n > 0) out.write(buffer, 0, n);
                        if (out.size() >= max) break;
                    }
                } catch (Exception ignored) {
                }
            }, "hud-naver-log");
            reader.setDaemon(true);
            reader.start();
            if (!process.waitFor(5, java.util.concurrent.TimeUnit.SECONDS) || process.exitValue() != 0) {
                return null;
            }
            reader.join(1000L);
            return reader.isAlive() ? null : out.toByteArray();
        } catch (Exception e) {
            return null;
        } finally {
            if (process != null) process.destroy();
        }
    }

    private static boolean upload(String eonIp, String name, byte[] body) {
        HttpURLConnection connection = null;
        try {
            if (body == null) return false;
            connection = (HttpURLConnection) new URL("http://" + eonIp + ":" + PORT
                    + "/trace/upload/hud/" + name).openConnection();
            connection.setConnectTimeout(3000);
            connection.setReadTimeout(5000);
            connection.setDoOutput(true);
            connection.setRequestMethod("POST");
            connection.setRequestProperty("Content-Type", "text/plain; charset=utf-8");
            connection.setFixedLengthStreamingMode(body.length);
            try (OutputStream out = connection.getOutputStream()) {
                out.write(body);
            }
            return connection.getResponseCode() == 200;
        } catch (Exception e) {
            return false;
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    /** 파일이 1MB 를 넘으면 뒤쪽(최근) 1MB 만 보낸다. */
    private static byte[] read(File file) {
        try (InputStream in = new FileInputStream(file)) {
            long length = file.length();
            long skip = Math.max(0L, length - MAX_BYTES);
            if (skip > 0 && in.skip(skip) != skip) return null;
            byte[] body = new byte[(int) (length - skip)];
            int off = 0;
            while (off < body.length) {
                int n = in.read(body, off, body.length - off);
                if (n < 0) break;
                off += n;
            }
            if (off == body.length) return body;
            byte[] shorter = new byte[off];
            System.arraycopy(body, 0, shorter, 0, off);
            return shorter;
        } catch (Exception e) {
            return null;
        }
    }
}
