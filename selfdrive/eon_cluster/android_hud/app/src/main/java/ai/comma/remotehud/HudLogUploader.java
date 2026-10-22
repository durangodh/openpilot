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

    private final File dir;
    private final long[] sentStamp = new long[NAMES.length];

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
            if (upload(eonIp, NAMES[i], file)) sentStamp[i] = stamp;
        }
    }

    private static boolean upload(String eonIp, String name, File file) {
        HttpURLConnection connection = null;
        try {
            byte[] body = read(file);
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
