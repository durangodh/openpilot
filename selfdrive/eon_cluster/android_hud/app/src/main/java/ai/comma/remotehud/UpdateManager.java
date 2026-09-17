package ai.comma.remotehud;

import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

/** Checks GitHub releases and only offers APKs signed like the installed app. */
final class UpdateManager {
    private static final String RELEASES_API =
            "https://api.github.com/repos/durangodh/openpilot/releases?per_page=30";
    private static final String ASSET_PREFIX = "EON-Remote-HUD-fixed-";
    private static final long MAX_APK_BYTES = 50L * 1024L * 1024L;

    interface Callback {
        void onProgress(String message);
        void onNoUpdate(String message);
        void onUpdateReady(String versionName, File apk);
        void onError(String message);
    }

    private final Context context;
    private final Handler main = new Handler(Looper.getMainLooper());

    UpdateManager(Context context) {
        this.context = context.getApplicationContext();
    }

    void check(Callback callback) {
        new Thread(() -> {
            try {
                postProgress(callback, "GitHub에서 새 버전을 확인하는 중…");
                JSONObject asset = findNewestFixedAsset();
                if (asset == null) {
                    post(() -> callback.onNoUpdate("배포된 수정판 업데이트가 없습니다."));
                    return;
                }

                postProgress(callback, "업데이트 APK를 내려받는 중…");
                File apk = download(asset.getString("browser_download_url"));
                PackageInfo candidate = archiveInfo(apk);
                PackageInfo current = installedInfo();
                if (candidate == null || !context.getPackageName().equals(candidate.packageName)) {
                    apk.delete();
                    throw new SecurityException("패키지명이 다른 APK입니다.");
                }
                if (!signers(candidate).equals(signers(current))) {
                    apk.delete();
                    throw new SecurityException("현재 앱과 서명이 다른 APK라서 차단했습니다.");
                }

                long candidateCode = versionCode(candidate);
                long currentCode = versionCode(current);
                if (candidateCode <= currentCode) {
                    apk.delete();
                    post(() -> callback.onNoUpdate("현재 버전이 최신입니다."));
                    return;
                }
                String name = candidate.versionName == null
                        ? String.valueOf(candidateCode) : candidate.versionName;
                post(() -> callback.onUpdateReady(name, apk));
            } catch (Exception error) {
                String message = error.getMessage();
                if (message == null || message.trim().isEmpty()) {
                    message = error.getClass().getSimpleName();
                }
                String finalMessage = message;
                post(() -> callback.onError("업데이트 확인 실패: " + finalMessage));
            }
        }, "hud-update-check").start();
    }

    void launchInstaller(File apk) {
        Uri uri = Uri.parse("content://" + context.getPackageName()
                + ".updateprovider/update.apk");
        Intent intent = new Intent(Intent.ACTION_VIEW)
                .setDataAndType(uri, "application/vnd.android.package-archive")
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        context.startActivity(intent);
    }

    private JSONObject findNewestFixedAsset() throws Exception {
        HttpURLConnection connection = open(RELEASES_API);
        String body;
        try {
            if (connection.getResponseCode() != 200) {
                throw new IllegalStateException("GitHub 응답 " + connection.getResponseCode());
            }
            body = readText(connection);
        } finally {
            connection.disconnect();
        }
        JSONArray releases = new JSONArray(body);
        for (int i = 0; i < releases.length(); i++) {
            JSONArray assets = releases.getJSONObject(i).optJSONArray("assets");
            if (assets == null) continue;
            for (int j = 0; j < assets.length(); j++) {
                JSONObject asset = assets.getJSONObject(j);
                String name = asset.optString("name");
                if (name.startsWith(ASSET_PREFIX) && name.endsWith(".apk")) {
                    return asset;
                }
            }
        }
        return null;
    }

    private File download(String url) throws Exception {
        if (!url.startsWith("https://")) {
            throw new SecurityException("HTTPS가 아닌 다운로드 주소입니다.");
        }
        File directory = new File(context.getCacheDir(), "updates");
        if (!directory.exists() && !directory.mkdirs()) {
            throw new IllegalStateException("업데이트 폴더를 만들 수 없습니다.");
        }
        File partial = new File(directory, "update.apk.part");
        File target = new File(directory, "update.apk");
        partial.delete();
        target.delete();

        HttpURLConnection connection = open(url);
        try {
            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) {
                throw new IllegalStateException("APK 다운로드 응답 " + status);
            }
            long declared = connection.getContentLengthLong();
            if (declared <= 0 || declared > MAX_APK_BYTES) {
                throw new IllegalStateException("APK 크기가 허용 범위를 벗어났습니다.");
            }
            long total = 0;
            byte[] buffer = new byte[32 * 1024];
            try (BufferedInputStream input = new BufferedInputStream(connection.getInputStream());
                 FileOutputStream output = new FileOutputStream(partial)) {
                int count;
                while ((count = input.read(buffer)) != -1) {
                    total += count;
                    if (total > MAX_APK_BYTES) {
                        throw new IllegalStateException("APK가 너무 큽니다.");
                    }
                    output.write(buffer, 0, count);
                }
                output.getFD().sync();
            }
            if (total != declared || !partial.renameTo(target)) {
                throw new IllegalStateException("APK 다운로드가 완전하지 않습니다.");
            }
            return target;
        } finally {
            connection.disconnect();
            if (partial.exists()) partial.delete();
        }
    }

    private HttpURLConnection open(String url) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(url).openConnection();
        connection.setConnectTimeout(12_000);
        connection.setReadTimeout(30_000);
        connection.setRequestProperty("Accept", "application/vnd.github+json");
        connection.setRequestProperty("User-Agent", "EON-Remote-HUD-Updater");
        connection.setInstanceFollowRedirects(true);
        return connection;
    }

    private static String readText(HttpURLConnection connection) throws Exception {
        try (BufferedInputStream input = new BufferedInputStream(connection.getInputStream());
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            int count;
            while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
            return output.toString("UTF-8");
        }
    }

    @SuppressWarnings("deprecation")
    private PackageInfo archiveInfo(File apk) {
        int flags = Build.VERSION.SDK_INT >= 28
                ? PackageManager.GET_SIGNING_CERTIFICATES : PackageManager.GET_SIGNATURES;
        return context.getPackageManager().getPackageArchiveInfo(apk.getAbsolutePath(), flags);
    }

    @SuppressWarnings("deprecation")
    private PackageInfo installedInfo() throws Exception {
        int flags = Build.VERSION.SDK_INT >= 28
                ? PackageManager.GET_SIGNING_CERTIFICATES : PackageManager.GET_SIGNATURES;
        return context.getPackageManager().getPackageInfo(context.getPackageName(), flags);
    }

    @SuppressWarnings("deprecation")
    private static Set<String> signers(PackageInfo info) throws Exception {
        Signature[] signatures;
        if (Build.VERSION.SDK_INT >= 28) {
            signatures = info.signingInfo == null
                    ? new Signature[0] : info.signingInfo.getApkContentsSigners();
        } else {
            signatures = info.signatures == null ? new Signature[0] : info.signatures;
        }
        Set<String> result = new HashSet<>();
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        for (Signature signature : signatures) {
            byte[] hash = digest.digest(signature.toByteArray());
            StringBuilder hex = new StringBuilder(hash.length * 2);
            for (byte value : hash) hex.append(String.format("%02x", value & 0xff));
            result.add(hex.toString());
        }
        if (result.isEmpty()) throw new SecurityException("APK 서명을 읽을 수 없습니다.");
        return result;
    }

    @SuppressWarnings("deprecation")
    private static long versionCode(PackageInfo info) {
        return Build.VERSION.SDK_INT >= 28 ? info.getLongVersionCode() : info.versionCode;
    }

    private void postProgress(Callback callback, String message) {
        post(() -> callback.onProgress(message));
    }

    private void post(Runnable runnable) {
        main.post(runnable);
    }
}
