package ai.comma.remotehud;

import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.SystemClock;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.Proxy;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.KeyStore;
import java.util.function.BooleanSupplier;
import java.util.function.IntSupplier;
import java.util.function.Supplier;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

final class RoadPreviewClient {
    // 4 fps, as the EON now serves it. At 10 fps the phone's Wi-Fi traffic broke up its
    // Bluetooth audio (navi voice, music; 2026-11-14). The HUD phone draws the lines
    // (RoadOverlay) from telemetry matched to each photo, so they stay smooth.
    private static final long FRAME_INTERVAL_MS = 250;
    private static final long MAX_FRAME_AGE_MS = 1200;
    private static final int MAX_JPEG_BYTES = 256 * 1024;
    private static final int MAX_FRAME_SIDE = 640;
    private static final String PREFS = "road_preview";
    private static final String KEY_ALIAS = "remote_hud_road_preview";
    private static final String CIPHER = "AES/GCM/NoPadding";

    private static final class Connection {
        final String address;
        final String authorization;

        Connection(String address, String password) {
            this.address = address;
            authorization = "Basic " + Base64.encodeToString(
                    ("eon:" + password).getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
        }
    }

    static final class Frame {
        final Connection connection;
        final Bitmap bitmap;
        final long timestamp;
        /** Camera crop {u0, v0, u1, v1}; null when the EON drew the lines itself. */
        final int[] crop;
        /** EON capture time (epoch ms), matched against telemetry "t". */
        final long eonTime;

        Frame(Connection connection, Bitmap bitmap, long timestamp, int[] crop, long eonTime) {
            this.connection = connection;
            this.bitmap = bitmap;
            this.timestamp = timestamp;
            this.crop = crop;
            this.eonTime = eonTime;
        }
    }

    private static volatile Connection connection;
    private static Connection savedConnection;
    private static boolean restored;
    private volatile Frame latest;
    /** Upper road region for the traffic light networks; the HUD sets the rate (heat). */
    private static final int MAX_SIGNAL_JPEG_BYTES = 256 * 1024;

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static synchronized void restore(Context context) {
        if (restored) return;
        restored = true;
        SharedPreferences prefs = prefs(context);
        String address = prefs.getString("address", "");
        String encrypted = prefs.getString("password", "");
        if (!validAddress(address) || encrypted.isEmpty()) return;
        try {
            String[] parts = encrypted.split(":", 2);
            Cipher cipher = Cipher.getInstance(CIPHER);
            cipher.init(Cipher.DECRYPT_MODE, passwordKey(false),
                    new GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP)));
            cipher.updateAAD(address.getBytes(StandardCharsets.UTF_8));
            String password = new String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)),
                    StandardCharsets.UTF_8);
            if (!password.isEmpty()) {
                savedConnection = new Connection(address, password);
                if (prefs.getBoolean("enabled", false)) connection = savedConnection;
            }
        } catch (GeneralSecurityException | IOException | RuntimeException e) {
            android.util.Log.w("HudRoadPreview", "Could not restore camera credentials");
        }
    }

    static boolean enabled() {
        return connection != null;
    }

    static synchronized boolean setEnabled(Context context, boolean enabled) {
        restore(context);
        if (enabled && savedConnection == null) return false;
        prefs(context).edit().putBoolean("enabled", enabled).apply();
        connection = enabled ? savedConnection : null;
        return true;
    }

    static synchronized String savedAddress(Context context) {
        restore(context);
        return savedConnection == null ? "" : savedConnection.address;
    }

    static synchronized boolean saveAndEnable(Context context, String address, String password) {
        if (password.isEmpty() || !validAddress(address)) return false;
        restore(context);
        try {
            Cipher cipher = Cipher.getInstance(CIPHER);
            cipher.init(Cipher.ENCRYPT_MODE, passwordKey(true));
            cipher.updateAAD(address.getBytes(StandardCharsets.UTF_8));
            String iv = Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP);
            String encrypted = Base64.encodeToString(
                    cipher.doFinal(password.getBytes(StandardCharsets.UTF_8)), Base64.NO_WRAP);
            if (!prefs(context).edit().putString("address", address)
                    .putString("password", iv + ":" + encrypted).putBoolean("enabled", true).commit()) {
                return false;
            }
            savedConnection = new Connection(address, password);
            connection = savedConnection;
            return true;
        } catch (GeneralSecurityException | IOException | RuntimeException e) {
            return false;
        }
    }

    private static SecretKey passwordKey(boolean create) throws GeneralSecurityException, IOException {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        SecretKey key = (SecretKey) store.getKey(KEY_ALIAS, null);
        if (key != null || !create) return key;
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build());
        return generator.generateKey();
    }

    static boolean validAddress(String address) {
        if (!address.matches("[0-9]{1,3}(\\.[0-9]{1,3}){3}")) return false;
        String[] parts = address.split("\\.");
        for (String part : parts) {
            if (Integer.parseInt(part) > 255 || (part.length() > 1 && part.startsWith("0"))) return false;
        }
        int first = Integer.parseInt(parts[0]);
        int second = Integer.parseInt(parts[1]);
        return first == 10 || (first == 172 && second >= 16 && second <= 31)
                || (first == 192 && second == 168);
    }

    Frame currentFrame() {
        Frame frame = latest;
        if (frame == null || frame.connection != connection
                || SystemClock.elapsedRealtime() - frame.timestamp >= MAX_FRAME_AGE_MS) return null;
        return frame;
    }

    /** view is the HUD pane size; the EON sends its own screen cropped to that shape. */
    void run(BooleanSupplier active, BooleanSupplier requested, Supplier<String> telemetryAddress, IntSupplier pathEnd,
             String view) {
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
                    Frame frame = fetch(target, start, pathEnd.getAsInt(), view);
                    if (frame != null && connection == target && requested.getAsBoolean()
                            && target.address.equals(telemetryAddress.get())) {
                        latest = frame;
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

    /**
     * Fetches the EON's upper road region at native resolution (/api/road.jpg?signal=1) and
     * hands each photo to sink on this thread (the traffic light networks run there). Shares
     * the road preview's EON address and password.
     */
    void runSignal(BooleanSupplier active, java.util.function.LongSupplier intervalMs, Supplier<String> telemetryAddress,
                   java.util.function.Consumer<Frame> sink) {
        android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_BACKGROUND);
        try {
            while (active.getAsBoolean() && !Thread.currentThread().isInterrupted()) {
                Connection target = connection;
                long interval = intervalMs.getAsLong();   // <= 0: not now
                if (target == null || interval <= 0L || !target.address.equals(telemetryAddress.get())) {
                    Thread.sleep(500);
                    continue;
                }
                long start = SystemClock.elapsedRealtime();
                try {
                    Frame frame = fetchSignal(target, start);
                    if (frame != null && connection == target) {
                        sink.accept(frame);
                        frame.bitmap.recycle();
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
        }
    }

    private Frame fetchSignal(Connection target, long start) throws IOException {
        URL url = new URL("http", target.address, 7000, "/api/road.jpg?signal=1");
        HttpURLConnection request = (HttpURLConnection) url.openConnection(Proxy.NO_PROXY);
        try {
            request.setConnectTimeout(500);
            request.setReadTimeout(800);
            request.setInstanceFollowRedirects(false);
            request.setUseCaches(false);
            request.setRequestProperty("Authorization", target.authorization);
            if (request.getResponseCode() != HttpURLConnection.HTTP_OK
                    || !"image/jpeg".equalsIgnoreCase(request.getContentType())
                    || request.getContentLength() > MAX_SIGNAL_JPEG_BYTES) return null;
            int[] crop = parseCrop(request.getHeaderField("X-EON-View"));
            long eonTime;
            try {
                String time = request.getHeaderField("X-Frame-Time");
                eonTime = time == null ? 0L : Long.parseLong(time.trim());
            } catch (NumberFormatException e) {
                return null;
            }
            if (crop == null || eonTime <= 0L) return null;   // an older EON without the region
            try (InputStream input = request.getInputStream();
                 ByteArrayOutputStream output = new ByteArrayOutputStream(64 * 1024)) {
                byte[] buffer = new byte[8192];
                int count;
                while ((count = input.read(buffer)) != -1) {
                    if (output.size() + count > MAX_SIGNAL_JPEG_BYTES) return null;
                    output.write(buffer, 0, count);
                }
                byte[] bytes = output.toByteArray();
                Bitmap bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.length);
                return bitmap == null ? null : new Frame(target, bitmap, start, crop, eonTime);
            }
        } finally {
            request.disconnect();
        }
    }

    private static int[] parseCrop(String value) {
        if (value == null) return null;
        String[] parts = value.split(",");
        if (parts.length != 4) return null;
        int[] crop = new int[4];
        try {
            for (int i = 0; i < 4; i++) crop[i] = Integer.parseInt(parts[i].trim());
        } catch (NumberFormatException e) {
            return null;
        }
        return crop[2] > crop[0] && crop[3] > crop[1] ? crop : null;
    }

    private Frame fetch(Connection target, long start, int pathEnd, String view) throws IOException {
        URL url = new URL("http", target.address, 7000, "/api/road.jpg?hud=1");
        HttpURLConnection request = (HttpURLConnection) url.openConnection(Proxy.NO_PROXY);
        try {
            request.setConnectTimeout(500);
            request.setReadTimeout(500);
            request.setInstanceFollowRedirects(false);
            request.setUseCaches(false);
            request.setRequestProperty("Authorization", target.authorization);
            request.setRequestProperty("X-HUD-Path-End", Integer.toString(pathEnd));
            request.setRequestProperty("X-HUD-View", view);
            request.setRequestProperty("X-HUD-Overlay", "0");
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
                if (options.outWidth <= 0 || options.outWidth > MAX_FRAME_SIDE
                        || options.outHeight <= 0 || options.outHeight > MAX_FRAME_SIDE) return null;
                options.inJustDecodeBounds = false;
                Bitmap bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.length, options);
                if (bitmap == null) return null;
                // An older EON ignores X-HUD-Overlay and sends lines already drawn,
                // without these headers; then the HUD must not draw them again.
                int[] crop = parseCrop(request.getHeaderField("X-EON-View"));
                long eonTime = 0L;
                try {
                    String time = request.getHeaderField("X-Frame-Time");
                    if (time != null) eonTime = Long.parseLong(time.trim());
                } catch (NumberFormatException ignored) {
                    crop = null;
                }
                return new Frame(target, bitmap, start, crop, eonTime);
            }
        } finally {
            request.disconnect();
        }
    }
}
