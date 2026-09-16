package ai.comma.remotehud;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Locale;

/**
 * Low-rate NAVER Static Map downloader. Credentials are read only by the S9
 * app and sent solely to NAVER as HTTPS request headers, never to EON.
 */
final class NaverStaticMapClient {
    private static final String TAG = "NaverStaticMap";
    private static final String ENDPOINT =
            "https://maps.apigw.ntruss.com/map-static/v2/raster";
    private static final long IDLE_POLL_MS = 1000L;
    private static final long ERROR_RETRY_MS = 15000L;
    private static final long AUTH_RETRY_MS = 60000L;
    private static final long QUOTA_RETRY_MS = 300000L;

    interface Listener {
        void onStaticMap(Bitmap bitmap, double centerLat, double centerLon);
    }

    private final Context context;
    private final Listener listener;
    private final Object wakeLock = new Object();
    private volatile boolean running;
    private volatile double latitude = Double.NaN;
    private volatile double longitude = Double.NaN;
    private volatile double speedKph;
    private volatile String status = "인증정보 필요";
    private volatile double fetchedLat = Double.NaN;
    private volatile double fetchedLon = Double.NaN;
    private volatile long lastFetchElapsed;
    private volatile long retryAfterElapsed;
    private volatile boolean hasFrame;
    private volatile long credentialGeneration;

    NaverStaticMapClient(Context context, Listener listener) {
        this.context = context.getApplicationContext();
        this.listener = listener;
    }

    void start() {
        running = true;
    }

    void stop() {
        running = false;
        wake();
    }

    String status() {
        return status;
    }

    void update(JSONObject state) {
        if (state == null) return;
        JSONArray pose = state.optJSONArray("mapPose");
        if (pose == null || pose.length() < 2) return;
        latitude = pose.optDouble(0, Double.NaN);
        longitude = pose.optDouble(1, Double.NaN);
        speedKph = Math.max(0.0, state.optDouble("speed", 0.0));
        wake();
    }

    void credentialsChanged() {
        credentialGeneration++;
        fetchedLat = Double.NaN;
        fetchedLon = Double.NaN;
        lastFetchElapsed = 0L;
        retryAfterElapsed = 0L;
        hasFrame = false;
        status = AppPrefs.hasNaverStaticCredentials(context) ? "지도 요청 대기" : "인증정보 필요";
        wake();
    }

    void runLoop() {
        while (running) {
            try {
                if (!AppPrefs.hasNaverStaticCredentials(context)) {
                    status = "인증정보 필요";
                    waitFor(IDLE_POLL_MS);
                    continue;
                }
                double lat = latitude;
                double lon = longitude;
                if (!StaticMapPolicy.validPose(lat, lon)) {
                    status = "위치 대기";
                    waitFor(IDLE_POLL_MS);
                    continue;
                }
                long now = SystemClock.elapsedRealtime();
                if (now < retryAfterElapsed) {
                    waitFor(Math.min(IDLE_POLL_MS, retryAfterElapsed - now));
                    continue;
                }
                if (!StaticMapPolicy.shouldRefresh(hasFrame, fetchedLat, fetchedLon,
                        lastFetchElapsed, lat, lon, speedKph, now)) {
                    waitFor(IDLE_POLL_MS);
                    continue;
                }
                fetch(lat, lon);
            } catch (Throwable error) {
                status = "오류 · 재시도 대기";
                retryAfterElapsed = SystemClock.elapsedRealtime() + ERROR_RETRY_MS;
                Log.w(TAG, "Static Map loop failed", error);
            }
        }
    }

    private void fetch(double lat, double lon) throws Exception {
        long generation = credentialGeneration;
        status = "지도 받는 중";
        String center = String.format(Locale.US, "%.7f,%.7f", lon, lat);
        URL url = new URL(ENDPOINT + "?crs=EPSG:4326&w=1024&h=1024"
                + "&center=" + center + "&level=17&maptype=basic&format=jpg&scale=1&lang=ko");
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setConnectTimeout(5000);
        connection.setReadTimeout(7000);
        connection.setUseCaches(true);
        connection.setRequestProperty("x-ncp-apigw-api-key-id",
                AppPrefs.getNaverStaticClientId(context));
        connection.setRequestProperty("x-ncp-apigw-api-key",
                AppPrefs.getNaverStaticClientSecret(context));
        try {
            int response = connection.getResponseCode();
            if (response != HttpURLConnection.HTTP_OK) {
                if (response == HttpURLConnection.HTTP_UNAUTHORIZED
                        || response == HttpURLConnection.HTTP_FORBIDDEN) {
                    status = "인증 실패 (" + response + ")";
                    retryAfterElapsed = SystemClock.elapsedRealtime() + AUTH_RETRY_MS;
                } else if (response == 429) {
                    status = "사용 한도 초과";
                    retryAfterElapsed = SystemClock.elapsedRealtime() + QUOTA_RETRY_MS;
                } else {
                    status = "서버 오류 (" + response + ")";
                    retryAfterElapsed = SystemClock.elapsedRealtime() + ERROR_RETRY_MS;
                }
                return;
            }
            Bitmap bitmap;
            try (InputStream input = connection.getInputStream()) {
                bitmap = BitmapFactory.decodeStream(input);
            }
            if (bitmap == null || bitmap.getWidth() < 2 || bitmap.getHeight() < 2) {
                status = "지도 이미지 오류";
                retryAfterElapsed = SystemClock.elapsedRealtime() + ERROR_RETRY_MS;
                if (bitmap != null) bitmap.recycle();
                return;
            }
            if (!running || generation != credentialGeneration) {
                bitmap.recycle();
                return;
            }
            fetchedLat = lat;
            fetchedLon = lon;
            lastFetchElapsed = SystemClock.elapsedRealtime();
            retryAfterElapsed = 0L;
            hasFrame = true;
            status = "정상 · 50m 갱신";
            listener.onStaticMap(bitmap, lat, lon);
        } finally {
            connection.disconnect();
        }
    }

    private void wake() {
        synchronized (wakeLock) {
            wakeLock.notifyAll();
        }
    }

    private void waitFor(long milliseconds) {
        synchronized (wakeLock) {
            if (!running) return;
            try {
                wakeLock.wait(Math.max(1L, milliseconds));
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
                running = false;
            }
        }
    }
}
