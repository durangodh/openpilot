package ai.comma.remotehud;

import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.graphics.drawable.GradientDrawable;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.TextView;

/**
 * TMAP 위에는 작은 전환 버튼만 표시하고 HUD 영상은 독립 전체화면 Activity로 연다.
 * 버튼 이외 영역은 터치를 받지 않으므로 TMAP 조작과 nMirror 입력을 방해하지 않는다.
 */
final class HudSwitchOverlay {

    private final Context context;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final WindowManager windowManager;

    private TextView toggleView;
    private boolean attached;
    private boolean hudVisible;

    HudSwitchOverlay(Context context) {
        this.context = context.getApplicationContext();
        this.windowManager = (WindowManager) this.context.getSystemService(Context.WINDOW_SERVICE);
    }

    void start() {
        main.post(new Runnable() {
            @Override
            public void run() {
                attachOnMainThread();
            }
        });
    }

    void stop() {
        main.post(new Runnable() {
            @Override
            public void run() {
                detachOnMainThread();
            }
        });
    }

    void setHudVisible(final boolean visible) {
        main.post(new Runnable() {
            @Override
            public void run() {
                hudVisible = visible;
                updateLabel();
            }
        });
    }

    private void attachOnMainThread() {
        if (attached || !Settings.canDrawOverlays(context)) {
            return;
        }

        toggleView = new TextView(context);
        toggleView.setTextColor(Color.WHITE);
        toggleView.setTextSize(16f);
        toggleView.setGravity(Gravity.CENTER);
        toggleView.setTypeface(android.graphics.Typeface.DEFAULT_BOLD);
        toggleView.setPadding(dp(18), dp(10), dp(18), dp(10));
        toggleView.setBackground(toggleBackground());
        toggleView.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                switchScreen();
            }
        });

        WindowManager.LayoutParams params = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT);
        params.gravity = Gravity.TOP | Gravity.END;
        params.x = dp(14);
        params.y = dp(56);

        try {
            windowManager.addView(toggleView, params);
            attached = true;
            updateLabel();
        } catch (RuntimeException e) {
            detachOnMainThread();
        }
    }

    private void switchScreen() {
        Intent intent = new Intent(context, HudFullscreenActivity.class)
                .setAction(hudVisible
                        ? HudFullscreenActivity.ACTION_SHOW_TMAP
                        : HudFullscreenActivity.ACTION_SHOW_HUD)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                        | Intent.FLAG_ACTIVITY_CLEAR_TOP
                        | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        try {
            context.startActivity(intent);
        } catch (RuntimeException ignored) {
        }
    }

    private void updateLabel() {
        if (toggleView == null) {
            return;
        }
        toggleView.setText(hudVisible ? "TMAP" : "HUD");
        toggleView.setContentDescription(hudVisible
                ? "TMAP 화면으로 돌아가기" : "EON HUD 전체화면 열기");
    }

    private void detachOnMainThread() {
        if (toggleView != null) {
            try {
                windowManager.removeView(toggleView);
            } catch (RuntimeException ignored) {
            }
        }
        toggleView = null;
        attached = false;
        hudVisible = false;
    }

    private GradientDrawable toggleBackground() {
        GradientDrawable result = new GradientDrawable();
        result.setColor(Color.argb(220, 8, 13, 19));
        result.setStroke(dp(1), Color.argb(235, 0, 208, 132));
        result.setCornerRadius(dp(18));
        return result;
    }

    private int dp(int value) {
        return Math.round(value * context.getResources().getDisplayMetrics().density);
    }
}
