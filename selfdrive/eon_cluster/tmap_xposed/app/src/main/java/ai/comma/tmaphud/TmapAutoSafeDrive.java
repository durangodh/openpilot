package ai.comma.tmaphud;

import android.app.Activity;
import android.os.Handler;
import android.os.Looper;

import java.io.File;
import java.lang.reflect.Method;

/**
 * 티맵을 켜면 메인 화면에서 안심주행을 자동으로 시작한다.
 *
 * 메인 화면의 "안심주행" 버튼과 같은 함수(AppUtil.StartSafeDrive = com.skt.tmap.util.i.h(Activity),
 * 11.8.3.4061)를 부른다. 이 함수가 GPS 확인, 내비 리소스 확인, TmapNaviActivity 실행,
 * 엔진 SAFE_DRIVE 모드 전환까지 한다. 외부에서 보내는 연동 명령(EDC 204)은 차량 연동
 * 인증이 필요해서 쓸 수 없다.
 *
 * 앱 동작을 바꾸므로 확인한 버전(behaviorHooksAllowed)에서만, 앱 실행 때 한 번과 목적지
 * 경로 안내가 끝나거나 지워져 메인 화면으로 돌아올 때마다 한 번,
 * 메인 화면이 앞에 있고 팝업이 없으면(창 포커스 있음) 바로, 목적지 경로 안내 중이 아닐 때만
 * 시작한다. 끄려면
 * /sdcard/Android/data/com.skt.tmap.ku/files/tmap_hud_no_safedrive 파일을 만든다.
 */
final class TmapAutoSafeDrive {
    static final String MAIN_ACTIVITY = "com.skt.tmap.activity.TmapNewMainActivity";
    private static final String STARTER = "com.skt.tmap.util.i";
    // 2026-11-24 요청: 기다림 없이 바로 진입(전에는 3초 뒤). 화면이 포커스를 받는 데 걸리는 시간만 둔다.
    private static final long DELAY_MS = 200;
    private static final long RETRY_MS = 300;
    private static final int MAX_TRIES = 70;     // 팝업이 닫히길 최대 약 20초 기다린다
    private static final String OFF_FILE =
            "/sdcard/Android/data/com.skt.tmap.ku/files/tmap_hud_no_safedrive";

    private final TmapBridge bridge;
    private final Handler main = new Handler(Looper.getMainLooper());
    private boolean done, offFile;
    private int tries;

    TmapAutoSafeDrive(TmapBridge bridge) {
        this.bridge = bridge;
    }

    /** 메인 스레드(Activity.onResume 뒤). */
    void onResumed(final Activity activity) {
        if (!TmapHudModule.behaviorHooksAllowed) return;
        if (!MAIN_ACTIVITY.equals(activity.getClass().getName())) return;
        // 2026-11-24: 앱 실행마다 한 번뿐이라 목적지 안내를 끝내거나 지운 뒤에는 안심주행이 다시
        // 시작되지 않았다. 목적지 경로 안내가 있었고 메인 화면으로 돌아오면 다시 한 번 시작한다.
        // (안심주행만 하다가 메인으로 나온 경우는 다시 시작하지 않는다: 목적지를 찾는 중일 수 있다.)
        if (done && !offFile && bridge.takeRouteGuided()) {
            done = false;
            TmapHudLog.line("auto safe drive: armed again after route guidance");
        }
        if (done) return;
        tries = 0;
        main.removeCallbacksAndMessages(null);
        main.postDelayed(() -> maybeStart(activity), DELAY_MS);
    }

    private void maybeStart(Activity activity) {
        if (done) return;
        try {
            if (new File(OFF_FILE).exists()) {
                done = true;
                offFile = true;
                TmapHudLog.xposed("auto safe drive off (" + OFF_FILE + ")");
                return;
            }
            if (activity.isFinishing() || activity.isDestroyed()) return;   // 다음 onResume 에서 다시
            if (!activity.hasWindowFocus()) {
                // 팝업(이전 경로 안내·공지 등)이 떠 있거나 다른 화면이 앞에 있다. 잠시 뒤 다시 본다.
                if (++tries < MAX_TRIES) {
                    main.postDelayed(() -> maybeStart(activity), RETRY_MS);
                } else {
                    TmapHudLog.line("auto safe drive waits: main screen not focused");
                }
                return;
            }
            if (bridge.routeGuiding()) {
                // 목적지 경로가 아직 살아 있다(메인 화면만 잠깐 연 것일 수 있다). 경로가 지워지면 바로
                // 시작하고, 계속 안내 중이면 시작하지 않는다.
                if (++tries < MAX_TRIES) {
                    main.postDelayed(() -> maybeStart(activity), RETRY_MS);
                    return;
                }
                done = true;
                TmapHudLog.xposed("auto safe drive skipped: guidance already running");
                return;
            }
            done = true;
            bridge.takeRouteGuided();   // 지금까지의 경로 안내는 이번 시작에 포함
            Method start = activity.getClassLoader().loadClass(STARTER).getMethod("h", Activity.class);
            start.invoke(null, activity);
            TmapHudLog.xposed("auto safe drive started");
        } catch (Throwable t) {
            done = true;
            TmapHudLog.xposed("auto safe drive failed: " + t);
        }
    }
}
