package ai.comma.tmaphud;

import android.content.Context;
import android.hardware.display.DisplayManager;
import android.util.DisplayMetrics;
import android.view.Display;

/**
 * 휴대폰 본 화면(기본 디스플레이)의 밀도·크기. 내비 앱이 nMirror 가상 화면(HUD) 위에서 실행되면
 * getResources() 가 그 화면의 낮은 밀도를 돌려줘 HUD 전용 지도가 2~3배 작게 그려졌다
 * (2026-10-23, 부팅 직후 작게 나오고 설정 40 → 110 이 필요했다). 지도 크기 계산은 항상 이 값으로 한다.
 */
final class PhoneDisplay {
    private PhoneDisplay() {
    }

    static DisplayMetrics metrics(Context context) {
        try {
            DisplayManager displays = (DisplayManager) context.getSystemService(Context.DISPLAY_SERVICE);
            Display display = displays == null ? null : displays.getDisplay(Display.DEFAULT_DISPLAY);
            if (display != null) {
                DisplayMetrics metrics = new DisplayMetrics();
                display.getRealMetrics(metrics);
                if (metrics.densityDpi > 0 && metrics.widthPixels > 0) return metrics;
            }
        } catch (Throwable ignored) {
            // 아래 앱 화면 값으로 대신한다.
        }
        return context.getResources().getDisplayMetrics();
    }

    /** 로그용: 앱 화면 값과 본 화면 값. */
    static String describe(Context context) {
        DisplayMetrics app = context.getResources().getDisplayMetrics();
        DisplayMetrics phone = metrics(context);
        return "app dpi=" + app.densityDpi + " " + app.widthPixels + "x" + app.heightPixels
                + " / phone dpi=" + phone.densityDpi + " " + phone.widthPixels + "x" + phone.heightPixels;
    }
}
