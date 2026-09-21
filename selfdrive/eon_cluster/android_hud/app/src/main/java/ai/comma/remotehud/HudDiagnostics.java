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
    static synchronized void init(Context context) {
        file = new File(context.getFilesDir(), "hud-session.log");
        try {
            log("service-created version=" + context.getPackageManager()
                    .getPackageInfo(context.getPackageName(), 0).versionName);
        } catch (Exception ignored) { log("service-created"); }
    }
    static synchronized void log(String message) {
        Log.i("RemoteHudBoot", message);
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
