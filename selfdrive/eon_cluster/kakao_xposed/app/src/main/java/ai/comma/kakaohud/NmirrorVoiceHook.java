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
    private static long lastSent;

    static boolean isNmirror(String pkg) {
        return pkg != null && pkg.toLowerCase(Locale.ROOT).contains("nmirror");
    }

    private static long lastStackMs;

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
        hookLookupStacks();
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
        notifyKakao("hook ready x" + hooked);
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
        if (gap < REPEAT_GAP_MS) {
            notifyKakao("repeat +" + gap + "ms" + how);
        } else if (msInMinute < MINUTE_TICK_WINDOW_MS) {
            notifyKakao("minute tick ignored" + how + " via " + callers());
        } else {
            notifyKakao(PRESS + how + " via " + callers());
        }
    }

    private static String callers() {
        StringBuilder sb = new StringBuilder();
        int n = 0;
        for (StackTraceElement e : new Throwable().getStackTrace()) {
            String c = e.getClassName();
            if (c.startsWith("de.robv") || c.startsWith("ai.comma") || c.startsWith("LSPHooker")
                    || c.startsWith("org.lsposed") || c.startsWith("P.") || c.startsWith("java.lang.reflect")) continue;
            if (n > 0) sb.append(" < ");
            sb.append(c.substring(c.lastIndexOf('.') + 1)).append('.').append(e.getMethodName()).append(':').append(e.getLineNumber());
            if (++n >= 6) break;
        }
        return sb.toString();
    }

    /**
     * nMirror 버전과 무관하게: 음성 버튼 ID 조회가 어느 nMirror 함수에서 불리는지 호출
     * 경로를 남긴다(5초에 한 번). 매분 조회와 실제 누름의 경로가 다르면 그걸로 구분한다.
     */
    private static void hookLookupStacks() {
        try {
            XposedHelpers.findAndHookMethod(android.view.accessibility.AccessibilityNodeInfo.class,
                    "findAccessibilityNodeInfosByViewId", String.class, new XC_MethodHook() {
                @Override protected void beforeHookedMethod(MethodHookParam param) {
                    String id = (String) param.args[0];
                    if (id == null || !id.startsWith(KAKAO_PKG)) return;
                    long now = android.os.SystemClock.elapsedRealtime();
                    if (now - lastStackMs < 5000L) return;
                    lastStackMs = now;
                    StringBuilder sb = new StringBuilder("lookup ").append(id.substring(id.indexOf('/') + 1)).append(" via ");
                    int n = 0;
                    for (StackTraceElement e : new Throwable().getStackTrace()) {
                        String c = e.getClassName();
                        if (c.startsWith("android.") || c.startsWith("java.") || c.startsWith("de.robv")
                                || c.startsWith("ai.comma") || c.startsWith("LSPHooker") || c.startsWith("org.lsposed")) continue;
                        sb.append(c).append('.').append(e.getMethodName()).append(':').append(e.getLineNumber()).append(" < ");
                        if (++n >= 8) break;
                    }
                    notifyKakao(sb.toString());
                }
            });
            XposedBridge.log("KakaoHud: nMirror lookup stack hook ready");
        } catch (Throwable t) {
            XposedBridge.log("KakaoHud: nMirror lookup stack hook failed: " + t);
        }
    }

    private static int sentInWindow;

    private static void notifyKakao(String where) {
        long now = android.os.SystemClock.elapsedRealtime();
        if (now - lastSent >= 1000L) {
            lastSent = now;
            sentInWindow = 0;
        }
        if (++sentInWindow > 10) return;   // 초당 10건까지
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
