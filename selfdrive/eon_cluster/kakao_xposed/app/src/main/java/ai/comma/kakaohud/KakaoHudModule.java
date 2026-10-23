package ai.comma.kakaohud;

import android.app.Application;
import android.content.Context;

import java.lang.reflect.Method;

import de.robv.android.xposed.IXposedHookLoadPackage;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.callbacks.XC_LoadPackage.LoadPackageParam;

/**
 * 카카오내비(com.locnall.KimGiSa) 전용 LSPosed 모듈.
 *
 * 티맵/네이버가 smali 편집+재서명으로 한 것을, 카카오는 재서명 없이 SDK→앱
 * 안내 콜백을 후킹해 얻는다(AppSuit 무결성 회피).
 *
 * 후킹(KNUSDKRepository 내부 리스너 구현체):
 *   위치안내: 인자 KNGuide_Location
 *   경로안내: 인자 KNGuide_Route
 *   안전:     인자 List (또는 KNGuide_Safety)
 * 클래스명이 바뀌어도 "인자 타입"으로 찾으므로 견딘다.
 */
public final class KakaoHudModule implements IXposedHookLoadPackage {

    private static final String KAKAO_PKG = "com.locnall.KimGiSa";
    private static final String REPO_CLASS =
            "com.kakaomobility.navi.drive.core.repository.KNUSDKRepository";
    private static final String CAMERA_STATE_CLASS =
            "com.kakaomobility.navi.drive.core.common.map.KNUCameraPositionState";

    private static final String LOC_GUIDE = "com.kakaomobility.knmsdk.p60.a";
    private static final String ROUTE_GUIDE = "com.kakaomobility.knmsdk.q60.a";
    private static final String SAFETY_GUIDE = "com.kakaomobility.knmsdk.s60.a";

    private static boolean started = false;

    @Override
    public void handleLoadPackage(LoadPackageParam lpparam) {
        if (!KAKAO_PKG.equals(lpparam.packageName) || started) {
            return;
        }
        started = true;
        ai.comma.hudcommon.NavigationForeground.install(KAKAO_PKG, 3);
        KakaoHudLog.line("=== KakaoHudModule b" + BuildConfig.HUD_BUILD + " loaded in " + lpparam.packageName + " ===");

        final KakaoNaviClient client = new KakaoNaviClient();
        KakaoHudLog.forwardTo(client);
        final KakaoMap map = new KakaoMap(client);
        final KakaoBridge bridge = new KakaoBridge(client, map);
        bridge.setClassLoader(lpparam.classLoader);
        final EonDiscovery discovery = new EonDiscovery(client);
        discovery.start();

        hookApplicationContext(lpparam, map, discovery);
        hookForeground();
        hookScreenCamera(lpparam, map);
        hookJunction(lpparam, new KakaoJunction(client));
        hookLane(lpparam, new KakaoLane(client));
        hookCits(lpparam, bridge.cits);
        hookRepository(lpparam, bridge);
        hookGuideCallbacks(lpparam, bridge);
        new KakaoVoice().install();
    }

    private static final String VERIFIED_KAKAO_VERSION = "4.51.0";

    private static void logAppVersion(Context ctx) {
        try {
            android.content.pm.PackageInfo info =
                    ctx.getPackageManager().getPackageInfo(KAKAO_PKG, 0);
            boolean verified = VERIFIED_KAKAO_VERSION.equals(info.versionName);
            KakaoHudLog.line("KakaoNavi version " + info.versionName
                    + (verified ? " (verified)" : " (UNVERIFIED: 기능 일부가 빠질 수 있음)"));
        } catch (Throwable t) {
            KakaoHudLog.ex("appVersion", t);
        }
    }

    // 이 앱 화면이 앞으로 나오면(엔미러 바탕화면에서 누름 등) HUD 앱에 알려 HUD·EON 내비 선택을
    // 이 앱으로 바꾸게 한다. 이미 선택된 앱이면 HUD 앱이 무시한다.
    private static long hudNotifiedAt;

    static void notifyHudForeground(android.app.Activity activity, int app) {
        if (!activity.hasWindowFocus()) return;
        long now = android.os.SystemClock.elapsedRealtime();
        if (now - hudNotifiedAt < 1000) return;
        hudNotifiedAt = now;
        activity.sendBroadcast(new android.content.Intent("ai.comma.remotehud.NAV_FOREGROUND")
                .setPackage("ai.comma.remotehud").putExtra("app", app));
    }

    private void hookForeground() {
        try {
            XposedBridge.hookMethod(android.app.Activity.class.getDeclaredMethod("onResume"),
                    new XC_MethodHook() {
                        @Override
                        protected void afterHookedMethod(MethodHookParam param) {
                            try {
                                notifyHudForeground((android.app.Activity) param.thisObject, 3);
                            } catch (Throwable t) {
                                KakaoHudLog.ex("notifyHud", t);
                            }
                        }
                    });
        } catch (Throwable t) {
            KakaoHudLog.ex("hookForeground", t);
        }
    }


    private boolean mapStarted;

    private void startMap(LoadPackageParam target, KakaoMap map, EonDiscovery discovery,
                          Context context) {
        if (!KAKAO_PKG.equals(context.getPackageName())) return;
        new android.os.Handler(android.os.Looper.getMainLooper()).post(() -> {
            if (mapStarted) return;
            try {
                Context app = context.getApplicationContext();
                Context actual = app == null ? context : app;
                discovery.initialize(actual);
                map.start(target.classLoader, actual);
                mapStarted = true;
                KakaoHudLog.line("app context ready, map capture started");
                logAppVersion(context);
            } catch (Throwable error) {
                KakaoHudLog.ex("appContext", error);
            }
        });
    }

    private void hookApplicationContext(final LoadPackageParam target, final KakaoMap map,
                                        final EonDiscovery discovery) {
        try {
            // AppSuit/Application overrides can bypass the base Application.onCreate.
            // Instrumentation still dispatches both lifecycle methods.
            XposedBridge.hookMethod(android.app.Instrumentation.class.getDeclaredMethod(
                    "callApplicationOnCreate", Application.class), new XC_MethodHook() {
                @Override protected void afterHookedMethod(MethodHookParam param) {
                    if (!param.hasThrowable()) startMap(target, map, discovery,
                            (Application) param.args[0]);
                }
            });
            XposedBridge.hookMethod(android.app.Instrumentation.class.getDeclaredMethod(
                    "callActivityOnResume", android.app.Activity.class), new XC_MethodHook() {
                @Override protected void afterHookedMethod(MethodHookParam param) {
                    if (!param.hasThrowable()) startMap(target, map, discovery,
                            (Context) param.args[0]);
                }
            });
        } catch (Throwable error) {
            KakaoHudLog.ex("hookApplicationContext", error);
        }
    }

    /** Follow the camera attached to Kakao's own driving map when available. */
    private void hookScreenCamera(LoadPackageParam lpparam, final KakaoMap map) {
        try {
            Class<?> stateClass = lpparam.classLoader.loadClass(CAMERA_STATE_CLASS);
            Class<?> mapApiClass = lpparam.classLoader.loadClass(
                    "com.kakaomobility.knmsdk.KNMMapApi");
            Method attach = stateClass.getMethod("A", mapApiClass);
            XposedBridge.hookMethod(attach, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
                    if (param.hasThrowable()) return;
                    map.setScreenCameraSource(param.thisObject, param.args[0] != null);
                }
            });
            KakaoHudLog.line("screen camera hook ready");
        } catch (Throwable t) {
            KakaoHudLog.ex("hookScreenCamera", t);
        }
    }

    /** 교차로 확대 이미지: 주행 화면의 KNUJCViewModel 을 잡아 둔다(읽기 전용). */
    private void hookJunction(LoadPackageParam lpparam, final KakaoJunction junction) {
        try {
            Class<?> vm = lpparam.classLoader.loadClass(KakaoJunction.VIEW_MODEL);
            XposedBridge.hookAllConstructors(vm, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
                    if (param.hasThrowable()) return;
                    junction.setViewModel(param.thisObject);
                }
            });
            KakaoHudLog.line("junction hook ready");
        } catch (Throwable t) {
            KakaoHudLog.ex("hookJunction", t);
        }
    }

    /** 신호등: 폰 신호등 표시의 KNUCitsViewModel 을 잡아 둔다(읽기 전용). */
    private void hookCits(LoadPackageParam lpparam, final KakaoCits cits) {
        try {
            Class<?> vm = lpparam.classLoader.loadClass(KakaoCits.VIEW_MODEL);
            XposedBridge.hookAllConstructors(vm, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
                    if (param.hasThrowable()) return;
                    cits.setViewModel(param.thisObject);
                }
            });
            KakaoHudLog.line("cits hook ready");
        } catch (Throwable t) {
            KakaoHudLog.ex("hookCits", t);
        }
    }

    /** 차로 안내: 주행 화면의 KNULaneViewModel 을 잡아 둔다(읽기 전용). */
    private void hookLane(LoadPackageParam lpparam, final KakaoLane lane) {
        try {
            Class<?> vm = lpparam.classLoader.loadClass(KakaoLane.VIEW_MODEL);
            XposedBridge.hookAllConstructors(vm, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
                    if (param.hasThrowable()) return;
                    lane.setViewModel(param.thisObject);
                }
            });
            KakaoHudLog.line("lane hook ready");
        } catch (Throwable t) {
            KakaoHudLog.ex("hookLane", t);
        }
    }

    /** 현재 경로와 목적지까지의 남은 거리/시간을 읽기 위해 Repository 인스턴스를 잡는다. */
    private void hookRepository(LoadPackageParam lpparam, final KakaoBridge bridge) {
        try {
            Class<?> repo = lpparam.classLoader.loadClass(REPO_CLASS);
            XposedBridge.hookAllConstructors(repo, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
                    bridge.setRepository(param.thisObject);
                }
            });
            KakaoHudLog.line("repository constructors hooked");
        } catch (Throwable t) {
            KakaoHudLog.ex("hookRepository", t);
        }
    }

    private void hookGuideCallbacks(LoadPackageParam lpparam, final KakaoBridge bridge) {
        try {
            ClassLoader cl = lpparam.classLoader;
            final Class<?> locGuideClass = safeClass(cl, LOC_GUIDE);
            final Class<?> routeGuideClass = safeClass(cl, ROUTE_GUIDE);

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
            // The screen sign flow also calls $e.b(KNGuide_Safety), which wraps
            // the list used by the displayed safety sign.
            Class<?> safetyGuideClass = safeClass(cl, SAFETY_GUIDE);
            if (safetyGuideClass != null) {
                hooked += hookByArgType(cl, safetyGuideClass, new Extractor() {
                    @Override public void extract(Object arg) { bridge.onSafetyGuide(arg); }
                }, "safety-guide");
            }
            if (safetyGuideClass == null) {
                hooked += hookByArgType(cl, java.util.List.class, new Extractor() {
                    @Override public void extract(Object arg) { bridge.onSafeties(arg); }
                }, "safety");
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

    private int hookByArgType(ClassLoader cl, final Class<?> argType,
                              final Extractor extractor, final String tag) {
        int count = 0;
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
            int argIndex = -1;
            for (int i = 0; i < params.length; i++) {
                // List 후킹은 정확히 List 인자만(오검출 방지). 그 외는 대입가능이면 매칭.
                boolean ok = argType == java.util.List.class
                        ? java.util.List.class.isAssignableFrom(params[i])
                        : argType.isAssignableFrom(params[i]);
                if (ok) { argIndex = i; break; }
            }
            if (argIndex < 0) continue;
            final int idx = argIndex;
            try {
                XposedBridge.hookMethod(m, new XC_MethodHook() {
                    @Override
                    protected void afterHookedMethod(MethodHookParam param) {
                        try {
                            extractor.extract(param.args[idx]);
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
        try { return cl.loadClass(name); } catch (Throwable t) { return null; }
    }

    private interface Extractor {
        void extract(Object arg);
    }
}
