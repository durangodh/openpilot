package ai.comma.remotehud;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.TextView;

/**
 * Non-focusable phone HUD shown above the already-running TMAP activity.
 *
 * The frame and the small TMAP/HUD switch are separate windows. Hiding the
 * frame therefore reveals the same TMAP task without relaunching it, while the
 * switch remains available to restore the HUD. The frame never receives touch
 * input, so TMAP stays resumed and nMirror can keep forwarding its display.
 */
final class PhoneHudOverlay {

    interface FrameDrawer {
        void draw(Canvas canvas, int width, int height);
    }

    private final Context context;
    private final FrameDrawer drawer;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final WindowManager windowManager;

    private FrameView frameView;
    private TextView toggleView;
    private boolean attached;
    private boolean hudVisible = true;

    PhoneHudOverlay(Context context, FrameDrawer drawer) {
        this.context = context.getApplicationContext();
        this.drawer = drawer;
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

    void invalidateFrame() {
        main.post(new Runnable() {
            @Override
            public void run() {
                if (frameView != null && hudVisible) {
                    frameView.postInvalidateOnAnimation();
                }
            }
        });
    }

    private void attachOnMainThread() {
        if (attached || !Settings.canDrawOverlays(context)) {
            return;
        }

        frameView = new FrameView(context);
        WindowManager.LayoutParams frameParams = params(
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
                        | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
                        | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS);
        frameParams.gravity = Gravity.TOP | Gravity.START;

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
                setHudVisible(!hudVisible);
            }
        });

        WindowManager.LayoutParams toggleParams = params(
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN);
        toggleParams.gravity = Gravity.TOP | Gravity.END;
        toggleParams.x = dp(14);
        toggleParams.y = dp(56);

        try {
            windowManager.addView(frameView, frameParams);
            windowManager.addView(toggleView, toggleParams);
            attached = true;
            setHudVisible(true);
        } catch (RuntimeException e) {
            detachOnMainThread();
        }
    }

    private void setHudVisible(boolean visible) {
        hudVisible = visible;
        if (frameView != null) {
            frameView.setVisibility(visible ? View.VISIBLE : View.GONE);
        }
        if (toggleView != null) {
            toggleView.setText(visible ? "TMAP" : "HUD");
            toggleView.setContentDescription(visible
                    ? "TMAP 전체 화면 열기" : "EON HUD 화면 열기");
        }
    }

    private void detachOnMainThread() {
        if (frameView != null) {
            try {
                windowManager.removeView(frameView);
            } catch (RuntimeException ignored) {
            }
        }
        if (toggleView != null) {
            try {
                windowManager.removeView(toggleView);
            } catch (RuntimeException ignored) {
            }
        }
        frameView = null;
        toggleView = null;
        attached = false;
        hudVisible = true;
    }

    private WindowManager.LayoutParams params(int width, int height, int flags) {
        WindowManager.LayoutParams result = new WindowManager.LayoutParams(
                width, height, WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                flags, PixelFormat.TRANSLUCENT);
        if (Build.VERSION.SDK_INT >= 28) {
            result.layoutInDisplayCutoutMode =
                    WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;
        }
        return result;
    }

    private GradientDrawable toggleBackground() {
        GradientDrawable result = new GradientDrawable();
        result.setColor(Color.argb(205, 8, 13, 19));
        result.setStroke(dp(1), Color.argb(220, 0, 208, 132));
        result.setCornerRadius(dp(18));
        return result;
    }

    private int dp(int value) {
        return Math.round(value * context.getResources().getDisplayMetrics().density);
    }

    private final class FrameView extends View {
        FrameView(Context context) {
            super(context);
            setBackgroundColor(Color.BLACK);
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            drawer.draw(canvas, getWidth(), getHeight());
        }
    }
}
