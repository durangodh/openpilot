package ai.comma.remotehud;

import android.content.Context;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.TimeZone;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 티맵·네이버·카카오 LSPosed 모듈이 GitHub 릴리스의 최신 빌드인지 확인한다.
 *
 * 모듈 APK 를 내려받아 열면 안드로이드 설치 화면은 같은 버전이어도 "업데이트" 버튼을
 * 띄운다(시스템 화면이라 문구를 바꿀 수 없다). 그래서 여기서 먼저 최신인지 알려 준다.
 *
 * 비교: CI 는 versionCode 를 빌드 번호(GITHUB_RUN_NUMBER)로 두고, 릴리스 파일 라벨에
 * "(b<번호>)" 를 붙인다. 라벨이 없는 예전 릴리스는 설치 시각과 업로드 시각을 비교한다.
 */
final class ModuleVersionChecker {
    private static final String API = "https://api.github.com/repos/durangodh/openpilot/releases/tags/";
    private static final Pattern BUILD = Pattern.compile("\\(b(\\d+)\\)");

    static final class Module {
        final String label, packageName, releaseTag;

        Module(String label, String packageName, String releaseTag) {
            this.label = label;
            this.packageName = packageName;
            this.releaseTag = releaseTag;
        }
    }

    static final Module[] MODULES = {
            new Module("티맵 모듈", "ai.comma.tmaphud", "tmap-hud-auto"),
            new Module("네이버 모듈", "ai.comma.naverhud", "naver-hud-auto"),
            new Module("카카오 모듈", "ai.comma.kakaohud", "kakao-hud-auto"),
    };

    static final class Result {
        final Module module;
        final String status;
        /** 새 버전이 있으면 내려받을 주소, 아니면 null. */
        final String downloadUrl;

        Result(Module module, String status, String downloadUrl) {
            this.module = module;
            this.status = status;
            this.downloadUrl = downloadUrl;
        }
    }

    interface Callback {
        void onDone(List<Result> results);
    }

    private final Context context;
    private final Handler main = new Handler(Looper.getMainLooper());

    ModuleVersionChecker(Context context) {
        this.context = context.getApplicationContext();
    }

    void check(Callback callback) {
        new Thread(() -> {
            List<Result> results = new ArrayList<>();
            for (Module m : MODULES) results.add(checkOne(m));
            main.post(() -> callback.onDone(results));
        }, "hud-module-check").start();
    }

    private Result checkOne(Module m) {
        PackageInfo installed;
        try {
            installed = context.getPackageManager().getPackageInfo(m.packageName, 0);
        } catch (PackageManager.NameNotFoundException e) {
            installed = null;
        }
        JSONObject asset;
        try {
            asset = latestApk(m.releaseTag);
        } catch (Exception error) {
            String why = error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage();
            return new Result(m, installed == null ? "설치 안 됨 (최신 확인 실패: " + why + ")"
                    : "설치 " + build(installed) + " · 최신 확인 실패: " + why, null);
        }
        if (asset == null) {
            return new Result(m, installed == null ? "설치 안 됨 · 배포 파일 없음"
                    : "설치 " + build(installed) + " · 배포 파일 없음", null);
        }
        String url = asset.optString("browser_download_url", null);
        if (installed == null) {
            return new Result(m, "설치 안 됨", url);
        }
        long latest = latestBuild(asset);
        long code = versionCode(installed);
        if (latest > 0) {
            return code >= latest
                    ? new Result(m, "최신 버전입니다 (b" + code + ")", null)
                    : new Result(m, "새 버전 b" + latest + " 있음 (설치 b" + code + ")", url);
        }
        // 라벨이 없는 예전 릴리스: 업로드 뒤에 설치했으면 최신으로 본다.
        long uploaded = parseTime(asset.optString("updated_at", ""));
        if (uploaded > 0 && installed.lastUpdateTime >= uploaded) {
            return new Result(m, "최신 버전입니다 (b" + code + ")", null);
        }
        return new Result(m, uploaded > 0 ? "새 버전 있음 (설치 b" + code + ")"
                : "설치 b" + code + " · 최신 여부를 알 수 없음", uploaded > 0 ? url : null);
    }

    private static String build(PackageInfo info) {
        return "b" + versionCode(info);
    }

    @SuppressWarnings("deprecation")
    private static long versionCode(PackageInfo info) {
        return Build.VERSION.SDK_INT >= 28 ? info.getLongVersionCode() : info.versionCode;
    }

    /** 라벨 "(b123)" 의 빌드 번호. 없으면 0. */
    static long latestBuild(JSONObject asset) {
        Matcher m = BUILD.matcher(asset.optString("label", ""));
        return m.find() ? Long.parseLong(m.group(1)) : 0L;
    }

    static long parseTime(String iso) {
        try {
            SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US);
            f.setTimeZone(TimeZone.getTimeZone("UTC"));
            Date d = f.parse(iso);
            return d == null ? 0L : d.getTime();
        } catch (Exception e) {
            return 0L;
        }
    }

    private static JSONObject latestApk(String tag) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(API + tag).openConnection();
        c.setConnectTimeout(10000);
        c.setReadTimeout(15000);
        c.setRequestProperty("Accept", "application/vnd.github+json");
        c.setRequestProperty("User-Agent", "EON-Remote-HUD");
        try {
            int code = c.getResponseCode();
            if (code == 404) return null;
            if (code != 200) throw new IllegalStateException("GitHub 응답 " + code);
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            try (InputStream in = c.getInputStream()) {
                byte[] buf = new byte[8192];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            }
            JSONArray assets = new JSONObject(out.toString("UTF-8")).optJSONArray("assets");
            if (assets == null) return null;
            for (int i = 0; i < assets.length(); i++) {
                JSONObject a = assets.getJSONObject(i);
                if (a.optString("name").endsWith(".apk")) return a;
            }
            return null;
        } finally {
            c.disconnect();
        }
    }
}
