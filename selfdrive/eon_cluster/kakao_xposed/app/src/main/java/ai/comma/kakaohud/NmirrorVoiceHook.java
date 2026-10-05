package ai.comma.kakaohud;

import android.app.AndroidAppHelper;
import android.content.Context;
import android.content.Intent;

import java.lang.reflect.Method;
import java.util.Locale;

import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.XposedHelpers;
import de.robv.android.xposed.callbacks.XC_LoadPackage.LoadPackageParam;

/**
 * nMirror 프로세스 쪽: 핸들 음성 버튼 누름을 잡아 카카오 모듈에 알린다.
 *
 * nMirror 는 누를 때마다 NavigationButtonService.a(패키지, voice, reroute) 로 내비 화면의
 * 음성 버튼을 리소스 ID 로 찾아 클릭한다. 카카오 음성 버튼은 ID 가 없어 찾지 못하고,
 * 그러면 nMirror 가 3초마다 끝없이 다시 찾는다(2026-10-05 b66 로그). 카카오 음성 호출만
 * 가로채 성공으로 돌려주고, 카카오 모듈에 "press" 브로드캐스트를 보낸다.
 */
final class NmirrorVoiceHook {
    static final String ACTION_VOICE = "ai.comma.kakaohud.VOICE";
    private static final String KAKAO_PKG = "com.locnall.KimGiSa";
    private static final String SERVICE = "com.legendn.nmirror.car.NavigationButtonService";

    static boolean isNmirror(String pkg) {
        return pkg != null && pkg.toLowerCase(Locale.ROOT).contains("nmirror");
    }

    // 2026-10-05 b66 로그: 누를 때마다 a(카카오, voice=true, reroute=false) 가 불리는데,
    // 카카오 음성 버튼(Compose)에 리소스 ID 가 없어 못 찾으면 "Waiting for navigation
    // button" 을 남기고 약 3초마다(50ms 간격 2회씩) 끝없이 다시 부른다. 그래서 두 번째
    // 누름부터는 반복 호출과 구분할 수 없었다.
    // 이제 카카오 음성 호출은 nMirror 가 찾지 않게 하고(성공으로 반환 → 반복 끝),
    // 대신 카카오 모듈에 "press" 를 보내 카카오가 직접 음성 버튼을 누른다.
    static final String PRESS = "press";
    private static final long REPEAT_GAP_MS = 4000;      // 이보다 가까운 호출은 같은 누름(반복)
    private static final long MINUTE_TICK_WINDOW_MS = 2500;
    private static long lastCallMs = -REPEAT_GAP_MS;

    static void install(LoadPackageParam lpparam) {
        Class<?> service = XposedHelpers.findClassIfExists(SERVICE, lpparam.classLoader);
        if (service == null) {
            XposedBridge.log("KakaoHud: nMirror " + lpparam.packageName + " has no " + SERVICE
                    + " (different nMirror version); voice button hook not installed");
            return;
        }
        int hooked = 0;
        for (Method m : service.getDeclaredMethods()) {
            Class<?>[] p = m.getParameterTypes();
            if (p.length != 3 || p[0] != String.class || p[1] != boolean.class || p[2] != boolean.class) continue;
            final Class<?> ret = m.getReturnType();
            XposedBridge.hookMethod(m, new XC_MethodHook() {
                @Override protected void beforeHookedMethod(MethodHookParam param) {
                    if (!KAKAO_PKG.equals(param.args[0]) || !Boolean.TRUE.equals(param.args[1])) return;
                    onKakaoVoiceCall(param, ret);
                }
            });
            hooked++;
        }
        XposedBridge.log("KakaoHud: nMirror " + lpparam.packageName + " voice hook on "
                + SERVICE + " x" + hooked);
    }

    private static void onKakaoVoiceCall(XC_MethodHook.MethodHookParam param, Class<?> ret) {
        long now = android.os.SystemClock.elapsedRealtime();
        long gap = now - lastCallMs;
        lastCallMs = now;
        long msInMinute = System.currentTimeMillis() % 60000L;
        // 원래 함수를 건너뛰고 "찾았다"로 돌려준다: nMirror 의 재시도 반복이 끝난다.
        boolean skipped = true;
        if (ret == boolean.class || ret == Boolean.class) param.setResult(Boolean.TRUE);
        else if (ret == void.class) param.setResult(null);
        else skipped = false;   // 모르는 반환형: 원래대로 두고 기록만
        String how = (skipped ? "" : " (not skipped, returns " + ret.getSimpleName() + ")");
        if (gap < REPEAT_GAP_MS) return;   // 같은 누름의 반복 호출
        if (msInMinute < MINUTE_TICK_WINDOW_MS) {
            notifyKakao("minute tick ignored" + how);
        } else {
            notifyKakao(PRESS + " b" + BuildConfig.HUD_BUILD + how);
        }
    }

    private static void notifyKakao(String where) {
        try {
            Context ctx = AndroidAppHelper.currentApplication();
            if (ctx == null) {
                XposedBridge.log("KakaoHud: nMirror voice " + where + " but no context");
                return;
            }
            ctx.sendBroadcast(new Intent(ACTION_VOICE).setPackage(KAKAO_PKG)
                    .putExtra("call", where).putExtra("wall", System.currentTimeMillis()));
        } catch (Throwable t) {
            XposedBridge.log("KakaoHud: nMirror voice broadcast failed: " + t);
        }
    }

    private NmirrorVoiceHook() {
    }
}
