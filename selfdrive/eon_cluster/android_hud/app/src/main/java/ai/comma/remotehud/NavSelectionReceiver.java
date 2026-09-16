package ai.comma.remotehud;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

/**
 * Receives an explicit navigation selection from nMirror.
 *
 * The receiver only accepts the two documented values and then enters the
 * exact same request/switch path as a tap in Remote HUD. EON is updated through
 * HUDNAV1, while HudService owns the stop/start ordering.
 */
public final class NavSelectionReceiver extends BroadcastReceiver {
    public static final String ACTION_SELECT_NAV = "ai.comma.remotehud.SELECT_NAV";
    public static final String EXTRA_NAV_APP = "nav_app";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !ACTION_SELECT_NAV.equals(intent.getAction())) return;
        int app = intent.getIntExtra(EXTRA_NAV_APP, 0);
        if (app != 1 && app != 2) return;

        AppPrefs.requestNavApp(context, app);
        Intent service = new Intent(context, HudService.class);
        service.setAction(HudService.ACTION_SELECT_NAV);
        try {
            if (Build.VERSION.SDK_INT >= 26) {
                context.startForegroundService(service);
            } else {
                context.startService(service);
            }
        } catch (RuntimeException ignored) {
            // The saved request is retried as soon as the already-configured
            // HUD service starts or reconnects to EON.
        }
    }
}
