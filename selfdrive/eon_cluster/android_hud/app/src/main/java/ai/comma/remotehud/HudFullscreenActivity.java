package ai.comma.remotehud;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.Window;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;

/**
 * nMirror 앱 목록에서 직접 여는 S9 전용 HUD 화면.
 *
 * TMAP 위에 overlay window를 얹지 않고 독립 Activity가 화면 전체를 소유한다.
 * TMAP으로 돌아갈 때는 nMirror 왼쪽 앱 목록에서 TMAP을 선택하면 된다.
 */
public final class HudFullscreenActivity extends Activity {

    private HudFrameView frameView;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
                | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS);
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        hideSystemUi();

        frameView = new HudFrameView();
        setContentView(frameView);
        startHudService();
    }

    @Override
    protected void onResume() {
        super.onResume();
        hideSystemUi();
        startHudService();
        if (frameView != null) {
            frameView.start();
        }
    }

    @Override
    protected void onPause() {
        if (frameView != null) {
            frameView.stop();
        }
        super.onPause();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            hideSystemUi();
        }
    }

    private void startHudService() {
        try {
            startForegroundService(new Intent(this, HudService.class));
        } catch (Exception ignored) {
        }
    }

    private void hideSystemUi() {
        if (Build.VERSION.SDK_INT >= 30) {
            WindowInsetsController controller = getWindow().getInsetsController();
            if (controller != null) {
                controller.hide(WindowInsets.Type.statusBars() | WindowInsets.Type.navigationBars());
                controller.setSystemBarsBehavior(
                        WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            }
        }
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                        | View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
    }

    private final class HudFrameView extends View implements Runnable {
        private final Paint messagePaint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private boolean drawing;

        HudFrameView() {
            super(HudFullscreenActivity.this);
            setBackgroundColor(Color.BLACK);
            messagePaint.setColor(Color.rgb(190, 205, 214));
            messagePaint.setTextAlign(Paint.Align.CENTER);
            messagePaint.setTextSize(28f * getResources().getDisplayMetrics().scaledDensity);
        }

        void start() {
            if (!drawing) {
                drawing = true;
                post(this);
            }
        }

        void stop() {
            drawing = false;
            removeCallbacks(this);
        }

        @Override
        public void run() {
            if (!drawing) {
                return;
            }
            postInvalidateOnAnimation();
            postDelayed(this, 80L);
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            if (!HudService.drawFullscreenFrame(canvas, getWidth(), getHeight())) {
                canvas.drawColor(Color.BLACK);
                canvas.drawText("EON HUD 데이터 대기", getWidth() * 0.5f,
                        getHeight() * 0.5f, messagePaint);
            }
        }
    }
}
