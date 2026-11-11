package ai.comma.remotehud;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.SystemClock;
import android.util.Base64;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.Proxy;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.function.BooleanSupplier;
import java.util.function.Supplier;

final class RoadPreviewClient {
    private static final long FRAME_INTERVAL_MS = 340;
    private static final long MAX_FRAME_AGE_MS = 1200;
    private static final int MAX_JPEG_BYTES = 256 * 1024;

    private static final class Connection {
        final String address;
        final String authorization;

        Connection(String address, String password) {
            this.address = address;
            authorization = "Basic " + Base64.encodeToString(
                    ("eon:" + password).getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
        }
    }

    private static final class Frame {
        final Connection connection;
        final Bitmap bitmap;
        final long timestamp;

        Frame(Connection connection, Bitmap bitmap, long timestamp) {
            this.connection = connection;
            this.bitmap = bitmap;
            this.timestamp = timestamp;
        }
    }

    private static volatile Connection connection;
    private volatile Frame latest;

    static boolean enabled() {
        return connection != null;
    }

    static void disable() {
        connection = null;
    }

    static boolean enable(String address, String password) {
        if (password.isEmpty() || !address.matches("[0-9]{1,3}(\\.[0-9]{1,3}){3}")) return false;
        String[] parts = address.split("\\.");
        for (String part : parts) {
            if (Integer.parseInt(part) > 255 || (part.length() > 1 && part.startsWith("0"))) return false;
        }
        int first = Integer.parseInt(parts[0]);
        int second = Integer.parseInt(parts[1]);
        if (!(first == 10 || (first == 172 && second >= 16 && second <= 31)
                || (first == 192 && second == 168))) return false;
        connection = new Connection(address, password);
        return true;
    }

    Bitmap currentFrame() {
        Frame frame = latest;
        if (frame == null || frame.connection != connection
                || SystemClock.elapsedRealtime() - frame.timestamp >= MAX_FRAME_AGE_MS) return null;
        return frame.bitmap;
    }

    void run(BooleanSupplier active, BooleanSupplier requested, Supplier<String> telemetryAddress) {
        android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_BACKGROUND);
        try {
            while (active.getAsBoolean() && !Thread.currentThread().isInterrupted()) {
                Connection target = connection;
                if (target == null || !requested.getAsBoolean() || !target.address.equals(telemetryAddress.get())) {
                    latest = null;
                    Thread.sleep(FRAME_INTERVAL_MS);
                    continue;
                }

                long start = SystemClock.elapsedRealtime();
                long interval = FRAME_INTERVAL_MS;
                try {
                    Bitmap bitmap = fetch(target, start);
                    if (bitmap != null && connection == target && requested.getAsBoolean()
                            && target.address.equals(telemetryAddress.get())) {
                        latest = new Frame(target, bitmap, start);
                    } else {
                        interval = 1000;
                    }
                } catch (IOException e) {
                    interval = 1000;
                }
                Thread.sleep(Math.max(0L, interval - (SystemClock.elapsedRealtime() - start)));
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } finally {
            latest = null;
        }
    }

    private Bitmap fetch(Connection target, long start) throws IOException {
        URL url = new URL("http", target.address, 7000, "/api/road.jpg?hud=1");
        HttpURLConnection request = (HttpURLConnection) url.openConnection(Proxy.NO_PROXY);
        try {
            request.setConnectTimeout(500);
            request.setReadTimeout(500);
            request.setInstanceFollowRedirects(false);
            request.setUseCaches(false);
            request.setRequestProperty("Authorization", target.authorization);
            if (request.getResponseCode() != HttpURLConnection.HTTP_OK
                    || !"image/jpeg".equalsIgnoreCase(request.getContentType())
                    || request.getContentLength() > MAX_JPEG_BYTES) return null;

            try (InputStream input = request.getInputStream();
                 ByteArrayOutputStream output = new ByteArrayOutputStream(32 * 1024)) {
                byte[] buffer = new byte[8192];
                int count;
                while ((count = input.read(buffer)) != -1) {
                    if (output.size() + count > MAX_JPEG_BYTES || connection != target
                            || SystemClock.elapsedRealtime() - start >= MAX_FRAME_AGE_MS) return null;
                    output.write(buffer, 0, count);
                }
                byte[] bytes = output.toByteArray();
                BitmapFactory.Options options = new BitmapFactory.Options();
                options.inJustDecodeBounds = true;
                BitmapFactory.decodeByteArray(bytes, 0, bytes.length, options);
                if (options.outWidth <= 0 || options.outWidth > 480
                        || options.outHeight <= 0 || options.outHeight > 360) return null;
                options.inJustDecodeBounds = false;
                return BitmapFactory.decodeByteArray(bytes, 0, bytes.length, options);
            }
        } finally {
            request.disconnect();
        }
    }
}
