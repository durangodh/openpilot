package ai.comma.remotehud;

import android.content.Context;
import android.content.SharedPreferences;

public final class AppPrefs {

    private static final String AUTO_START = "auto_start";
    private static final String FILE = "remote_hud_settings";
    private static final String GUIDE_SHOWN = "guide_shown_v37";
    private static final String ORIENTATION = "hud_orientation";
    private static final String MIRROR = "hud_mirror";
    private static final String NAV_APP = "hud_nav_app";

    private AppPrefs() {
    }

    public static boolean isAutoStart(Context context) {
        return prefs(context).getBoolean(AUTO_START, true);
    }

    public static void setAutoStart(Context context, boolean z) {
        prefs(context).edit().putBoolean(AUTO_START, z).apply();
    }

    /**
     * 패널 방향(0 또는 2)과 좌우 미러는 EON 패킷으로만 오기 때문에, EON 이 붙기
     * 전에는 앱이 알 수 없어 기본값으로 그리다 뒤집혀 보인다. 마지막으로 받은
     * 값을 남겨 두고 서비스 시작 시 그 값으로 시작한다.
     */
    public static int getOrientation(Context context) {
        return prefs(context).getInt(ORIENTATION, 0) == 2 ? 2 : 0;
    }

    public static void setOrientation(Context context, int orientation) {
        prefs(context).edit().putInt(ORIENTATION, orientation == 2 ? 2 : 0).apply();
    }

    public static boolean isMirror(Context context) {
        return prefs(context).getBoolean(MIRROR, false);
    }

    public static void setMirror(Context context, boolean mirror) {
        prefs(context).edit().putBoolean(MIRROR, mirror).apply();
    }

    /**
     * EON 에서 마지막으로 선택한 내비(1=티맵, 2=네이버지도). EON 은 S9 보다
     * 부팅이 훨씬 늦으므로, 부팅 직후엔 이 값으로 먼저 내비를 띄우고 EON 이
     * 붙은 뒤 선택이 다르면 그때 바꾼다.
     */
    public static int getNavApp(Context context) {
        return prefs(context).getInt(NAV_APP, 1) == 2 ? 2 : 1;
    }

    public static void setNavApp(Context context, int navApp) {
        prefs(context).edit().putInt(NAV_APP, navApp == 2 ? 2 : 1).apply();
    }

    public static boolean wasGuideShown(Context context) {
        return prefs(context).getBoolean(GUIDE_SHOWN, false);
    }

    public static void markGuideShown(Context context) {
        prefs(context).edit().putBoolean(GUIDE_SHOWN, true).apply();
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(FILE, 0);
    }
}
