package ai.comma.remotehud;

import android.content.Context;
import android.os.SystemClock;
import android.util.Log;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;

/** Bounded, app-private boot/session evidence which survives batteryless reboots. */
final class HudDiagnostics {
    private static File file;
    /** 마지막 USB 기록 시각(elapsedRealtime). HudLogUploader 가 이걸 보고 빨리 올린다. */
    private static volatile long lastUsbEventElapsed;

    static long lastUsbEventElapsed() {
        return lastUsbEventElapsed;
    }
    static synchronized void init(Context context) {
        file = new File(context.getFilesDir(), "hud-session.log");
        try {
            log("service-created version=" + context.getPackageManager()
                    .getPackageInfo(context.getPackageName(), 0).versionName);
        } catch (Exception ignored) { log("service-created"); }
    }
    static synchronized void log(String message) {
        Log.i("RemoteHudBoot", message);
        if (message.startsWith("usb-")) lastUsbEventElapsed = SystemClock.elapsedRealtime();
        if (file == null) return;
        try {
            if (file.length() > 512 * 1024) {
                File previous = new File(file.getParentFile(), "hud-session.previous.log");
                if (previous.exists() && !previous.delete()) return;
                if (!file.renameTo(previous)) return;
            }
            String line = System.currentTimeMillis() + " elapsed=" + SystemClock.elapsedRealtime()
                    + " pid=" + android.os.Process.myPid() + " " + message + "\n";
            try (FileOutputStream output = new FileOutputStream(file, true)) {
                output.write(line.getBytes(StandardCharsets.UTF_8));
            }
        } catch (Exception ignored) { }
    }
    private HudDiagnostics() { }
}
