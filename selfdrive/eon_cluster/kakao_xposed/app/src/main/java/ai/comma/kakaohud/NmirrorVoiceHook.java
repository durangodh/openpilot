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
 * nMirror 프로세스 쪽: 핸들 음성 버튼 동작을 잡아 카카오 모듈에 알린다.
 *
 * 카카오 쪽에서는 nMirror 의 음성 버튼 ID 조회만 보이는데, 그 조회는 매분 정각에도
 * 오고 한 번 누르면 쉬지 않고 반복돼(2026-10-05 b61/b65 로그) 두 번째 누름부터 구분할
 * 수 없었다. nMirror 의 NavigationButtonService(nMirrorOS 20260926 분석: a(String,
 * boolean, boolean) 가 버튼 동작 이름으로 노드를 찾아 클릭)를 후킹해, 음성 동작이
 * 실행될 때마다 카카오에 브로드캐스트를 보낸다. 로그는 LSPosed 로그(KakaoHud: nMirror ...).
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
            boolean hasString = false;
            for (Class<?> c : p) if (c == String.class) hasString = true;
            if (!hasString) continue;
            XposedBridge.hookMethod(m, new XC_MethodHook() {
                @Override protected void beforeHookedMethod(MethodHookParam param) {
                    // 1단계(진단): 모든 호출을 인자와 함께 카카오 로그로 보낸다. 매분 반복
                    // 조회와 실제 누름이 어떤 인자로 구분되는지 확인한 뒤 실행 규칙을 정한다.
                    StringBuilder sb = new StringBuilder(param.method.getName()).append('(');
                    for (int i = 0; i < param.args.length; i++) {
                        if (i > 0) sb.append(',');
                        Object a = param.args[i];
                        sb.append(a instanceof String || a instanceof Boolean || a instanceof Number
                                ? String.valueOf(a) : (a == null ? "null" : a.getClass().getSimpleName()));
                    }
                    notifyKakao(sb.append(')').toString());
                }
            });
            hooked++;
        }
        XposedBridge.log("KakaoHud: nMirror " + lpparam.packageName + " voice hook on "
                + SERVICE + " x" + hooked);
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
