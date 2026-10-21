package ai.comma.hudcommon;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.os.SystemClock;
import android.view.View;
import java.util.WeakHashMap;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;

/** Track real window focus, including navigation apps resumed on virtual displays. */
public final class NavigationForeground {
    private static final WeakHashMap<Activity, Boolean> watched = new WeakHashMap<>();
    private static long lastSent;

    public static void install(String packageName, int app) {
        try {
            XposedBridge.hookMethod(Instrumentation.class.getDeclaredMethod(
                    "callActivityOnResume", Activity.class), new XC_MethodHook() {
                @Override protected void afterHookedMethod(MethodHookParam param) {
                    if (param.hasThrowable()) return;
                    Activity activity = (Activity) param.args[0];
                    if (!packageName.equals(activity.getPackageName())) return;
                    View decor = activity.getWindow().getDecorView();
                    if (!watched.containsKey(activity)) {
                        watched.put(activity, true);
                        decor.getViewTreeObserver().addOnWindowFocusChangeListener(focused -> {
                            if (focused) notifyFocused(activity, app);
                        });
                    }
                    decor.post(() -> notifyFocused(activity, app));
                }
            });
            XposedBridge.log("HUD foreground focus tracking ready: " + packageName);
        } catch (Throwable error) {
            XposedBridge.log("HUD foreground focus tracking failed: " + error);
        }
    }

    private static void notifyFocused(Activity activity, int app) {
        if (!activity.hasWindowFocus() || activity.isFinishing() || activity.isDestroyed()) return;
        long now = SystemClock.elapsedRealtime();
        if (lastSent != 0 && now - lastSent < 250) return;
        lastSent = now;
        activity.sendBroadcast(new Intent("ai.comma.remotehud.NAV_FOREGROUND")
                .setPackage("ai.comma.remotehud")
                .addFlags(Intent.FLAG_RECEIVER_FOREGROUND)
                .putExtra("app", app).putExtra("event", "window-focus")
                .putExtra("source", activity.getPackageName()));
    }
}
