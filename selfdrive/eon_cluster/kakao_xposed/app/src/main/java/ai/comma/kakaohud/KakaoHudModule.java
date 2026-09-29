package ai.comma.kakaohud;

import de.robv.android.xposed.IXposedHookLoadPackage;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.callbacks.XC_LoadPackage.LoadPackageParam;

/**
 * 카카오내비(com.locnall.KimGiSa) 프로세스에서만 로드되는 LSPosed 모듈.
 *
 * 티맵/네이버가 smali 편집 + 재서명으로 했던 것을, 카카오는 재서명 없이 같은
 * 지점(SDK→앱 안내 콜백)을 후킹해서 얻는다. AppSuit 무결성 검사가 재서명을
 * 막기 때문.
 *
 * 후킹 대상(KNUSDKRepository 내부 리스너 구현체):
 *   $c.a(u, p60.a)  위치안내  -> 위치/방위/도로명
 *   $d.a(u, q60.a)  경로안내  -> 회전코드/거리/차로
 *   $e.a(u, List)   안전/카메라
 *
 * 클래스명이 카카오 업데이트로 바뀔 수 있어, 앵커(KNUSDKRepository)만 이름으로
 * 잡고 내부 리스너는 "인자 타입 시그니처"로 찾는다. 1차본은 정찰 겸 동작:
 * 인자 형태를 로그로 남기면서 확인된 getter 로 값 추출·전송을 시도한다.
 */
public final class KakaoHudModule implements IXposedHookLoadPackage {

    private static final String KAKAO_PKG = "com.locnall.KimGiSa";
    private static final String REPO_CLASS =
            "com.kakaomobility.navi.drive.core.repository.KNUSDKRepository";

    // 후킹 인자 타입(SDK 안내 객체). 이름 유지되는 SDK 클래스.
    private static final String LOC_GUIDE = "com.kakaomobility.knmsdk.p60.KNGuide_Location";
    private static final String ROUTE_GUIDE = "com.kakaomobility.knmsdk.q60.KNGuide_Route";

    private static boolean started = false;

    @Override
    public void handleLoadPackage(LoadPackageParam lpparam) {
        if (!KAKAO_PKG.equals(lpparam.packageName)) {
            return;
        }
        if (started) {
            return;
        }
        started = true;
        KakaoHudLog.line("=== KakaoHudModule loaded in " + lpparam.packageName + " ===");

        final KakaoNaviClient client = new KakaoNaviClient();
        final KakaoBridge bridge = new KakaoBridge(client);
        new EonDiscovery(client).start();

        hookGuideCallbacks(lpparam, bridge);
    }

    private void hookGuideCallbacks(LoadPackageParam lpparam, final KakaoBridge bridge) {
        try {
            ClassLoader cl = lpparam.classLoader;

            // 위치/경로 안내 객체 타입을 로드해 두고, 이 타입을 인자로 받는
            // 콜백 메서드를 KNUSDKRepository 의 내부 클래스들에서 찾아 후킹한다.
            final Class<?> locGuideClass = safeClass(cl, LOC_GUIDE);
            final Class<?> routeGuideClass = safeClass(cl, ROUTE_GUIDE);

            // 후킹은 "인자 타입"으로 모든 메서드를 훑어 건다. 콜백 클래스($c/$d)가
            // 난독화로 이름이 바뀌어도, 인자 타입이 KNGuide_Location/KNGuide_Route
            // 이면 그 메서드가 우리가 원하는 콜백이다.
            int hooked = 0;

            if (locGuideClass != null) {
                hooked += hookByArgType(cl, locGuideClass, new Extractor() {
                    @Override public void extract(Object arg) { bridge.onLocationGuide(arg); }
                }, "location");
            }
            if (routeGuideClass != null) {
                hooked += hookByArgType(cl, routeGuideClass, new Extractor() {
                    @Override public void extract(Object arg) { bridge.onRouteGuide(arg); }
                }, "route");
            }

            KakaoHudLog.line("guide callbacks hooked = " + hooked
                    + " (loc=" + (locGuideClass != null) + " route=" + (routeGuideClass != null) + ")");

            if (hooked == 0) {
                KakaoHudLog.line("WARN: no callback hooked — 클래스 구조가 바뀌었을 수 있음");
            }
        } catch (Throwable t) {
            KakaoHudLog.ex("hookGuideCallbacks", t);
        }
    }

    /**
     * repoClass 및 그 내부 클래스들에서, 정확히 argType 을 인자로 받는(마지막
     * 인자) 메서드를 모두 후킹한다. 리스너 구현체($c,$d)는 KNUSDKRepository 의
     * 내부 클래스이므로 같은 로더로 "$숫자/문자"를 훑는다.
     */
    private int hookByArgType(ClassLoader cl, final Class<?> argType,
                              final Extractor extractor, final String tag) {
        int count = 0;
        // KNUSDKRepository$a ... $z 를 훑는다(디컴파일상 콜백은 $c/$d/$e).
        for (char c = 'a'; c <= 'z'; c++) {
            Class<?> inner = safeClass(cl, REPO_CLASS + "$" + c);
            if (inner == null) continue;
            count += hookMethodsWithArg(inner, argType, extractor, tag);
        }
        return count;
    }

    private int hookMethodsWithArg(Class<?> owner, final Class<?> argType,
                                   final Extractor extractor, final String tag) {
        int count = 0;
        for (final java.lang.reflect.Method m : owner.getDeclaredMethods()) {
            Class<?>[] params = m.getParameterTypes();
            boolean match = false;
            int argIndex = -1;
            for (int i = 0; i < params.length; i++) {
                if (argType.isAssignableFrom(params[i])) {
                    match = true;
                    argIndex = i;
                    break;
                }
            }
            if (!match) continue;
            final int idx = argIndex;
            try {
                XposedBridge.hookMethod(m, new XC_MethodHook() {
                    @Override
                    protected void afterHookedMethod(MethodHookParam param) {
                        try {
                            Object arg = param.args[idx];
                            extractor.extract(arg);
                        } catch (Throwable t) {
                            KakaoHudLog.ex("hook-" + tag, t);
                        }
                    }
                });
                KakaoHudLog.line("hooked " + tag + ": " + owner.getSimpleName()
                        + "." + m.getName() + " arg#" + idx);
                count++;
            } catch (Throwable t) {
                KakaoHudLog.ex("hookMethod-" + tag, t);
            }
        }
        return count;
    }

    private static Class<?> safeClass(ClassLoader cl, String name) {
        try {
            return cl.loadClass(name);
        } catch (Throwable t) {
            return null;
        }
    }

    private interface Extractor {
        void extract(Object arg);
    }
}
