package ai.comma.kakaohud;

import android.app.Application;
import android.content.Context;
import android.app.Activity;
import android.content.res.Resources;
import android.graphics.Rect;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.view.accessibility.AccessibilityNodeInfo;

import java.lang.reflect.Method;
import java.util.concurrent.atomic.AtomicBoolean;

import de.robv.android.xposed.IXposedHookLoadPackage;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.XposedHelpers;
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

    private static final String NMIRROR_VOICE_ID =
            KAKAO_PKG + ":id/btn_speech_recognition";
    private static final String NMIRROR_VOICE_ENTRY = "btn_speech_recognition";
    private static final int NMIRROR_VOICE_RES_ID = 0x7f0bffff;
    private static final String VOICE_COMPOSABLE = "com.kakaomobility.knmsdk.g4.C1309j";
    private volatile Object voiceCallback;
    private View voiceProxy;

    private static boolean started = false;
    private final AtomicBoolean voiceNodeLogged = new AtomicBoolean(false);

    @Override
    public void handleLoadPackage(LoadPackageParam lpparam) {
        if (!KAKAO_PKG.equals(lpparam.packageName) || started) {
            return;
        }
        started = true;
        KakaoHudLog.line("=== KakaoHudModule loaded in " + lpparam.packageName + " ===");

        final KakaoNaviClient client = new KakaoNaviClient();
        final KakaoMap map = new KakaoMap(client);
        final KakaoBridge bridge = new KakaoBridge(client, map);
        bridge.setClassLoader(lpparam.classLoader);
        new EonDiscovery(client).start();

        hookVoice(lpparam);
        hookApplicationContext(lpparam, map);
        hookScreenCamera(lpparam, map);
        hookRepository(lpparam, bridge);
        hookGuideCallbacks(lpparam, bridge);
    }

    /**
     * nMirror 의 findAccessibilityNodeInfosByViewId 요청은 카카오 프로세스의
     * AccessibilityInteractionController 에서 숫자 ID 로 변환된다. 실제 View
     * 프록시의 ID 와 같은 값을 반환한다. 다른 리소스 조회는 변경하지 않는다.
     */
    /**
     * 네이버 hookVoiceButton 과 같은 전략:
     *  (1) 음성 Composable(C1309j.d)의 onClick 콜백을 잡아 둔다.
     *  (2) getIdentifier 후킹으로 btn_speech_recognition -> 고정 숫자ID.
     *  (3) 카카오 Compose 루트 View 에 그 ID 를 박고 OnClickListener 로 (1)의
     *      콜백을 실행. nMirror 가 ID 로 찾아 클릭하면 카카오 음성이 실행된다.
     */
    private void hookVoice(final LoadPackageParam lpparam) {
        hookVoiceResourceId(lpparam);
        hookVoiceCallback(lpparam);
        hookVoiceProxyAttach(lpparam);
    }

    /** nMirror 의 이름->숫자ID 변환(getIdentifier)이 0 이 되지 않게 고정값을 준다. */
    private void hookVoiceResourceId(LoadPackageParam lpparam) {
        try {
            Method getId = android.content.res.Resources.class.getMethod(
                    "getIdentifier", String.class, String.class, String.class);
            XposedBridge.hookMethod(getId, new XC_MethodHook() {
                @Override protected void afterHookedMethod(MethodHookParam param) {
                    try {
                        if (!(param.getResult() instanceof Integer)
                                || ((Integer) param.getResult()) != 0) return;
                        String name = (String) param.args[0];
                        String type = (String) param.args[1];
                        String pkg = (String) param.args[2];
                        boolean full = NMIRROR_VOICE_ID.equals(name)
                                && (type == null || type.isEmpty());
                        boolean entry = NMIRROR_VOICE_ENTRY.equals(name) && "id".equals(type)
                                && (pkg == null || KAKAO_PKG.equals(pkg));
                        if (full || entry) param.setResult(NMIRROR_VOICE_RES_ID);
                    } catch (Throwable ignored) { }
                }
            });
            KakaoHudLog.line("voice resource-id hook ready");
        } catch (Throwable t) {
            KakaoHudLog.ex("hookVoiceResourceId", t);
        }
    }

    /** 음성 Composable C1309j.d 의 onClick(Function0) 인자를 캡처한다. */
    private void hookVoiceCallback(LoadPackageParam lpparam) {
        try {
            Class<?> comp = XposedHelpers.findClass(VOICE_COMPOSABLE, lpparam.classLoader);
            XposedBridge.hookAllMethods(comp, "d", new XC_MethodHook() {
                @Override protected void beforeHookedMethod(MethodHookParam param) {
                    try {
                        for (Object a : param.args) {
                            if (a instanceof kotlin.jvm.functions.Function0) {
                                if (voiceCallback == null) KakaoHudLog.line("voice onClick captured");
                                voiceCallback = a;
                                break;
                            }
                        }
                    } catch (Throwable ignored) { }
                }
            });
            KakaoHudLog.line("voice callback hook ready");
        } catch (Throwable t) {
            KakaoHudLog.ex("hookVoiceCallback", t);
        }
    }

    /** 카카오 Activity 의 Compose 루트 View 에 프록시를 올린다. */
    private void hookVoiceProxyAttach(final LoadPackageParam lpparam) {
        try {
            XposedHelpers.findAndHookMethod(android.app.Activity.class, "onResume",
                    new XC_MethodHook() {
                @Override protected void afterHookedMethod(MethodHookParam param) {
                    try {
                        Activity act = (Activity) param.thisObject;
                        if (!KAKAO_PKG.equals(act.getPackageName())) return;
                        View root = act.getWindow() == null ? null
                                : act.getWindow().getDecorView();
                        if (root instanceof ViewGroup) attachVoiceProxy((ViewGroup) root);
                    } catch (Throwable t) {
                        KakaoHudLog.ex("voiceProxyAttach", t);
                    }
                }
            });
        } catch (Throwable t) {
            KakaoHudLog.ex("hookVoiceProxyAttach", t);
        }
    }

    private void attachVoiceProxy(final ViewGroup container) {
        if (voiceProxy != null && voiceProxy.getParent() == container) return;
        if (voiceProxy != null && voiceProxy.getParent() instanceof ViewGroup) {
            ((ViewGroup) voiceProxy.getParent()).removeView(voiceProxy);
        }
        final View proxy = new View(container.getContext());
        proxy.setId(NMIRROR_VOICE_RES_ID);
        proxy.setContentDescription("음성서비스");
        proxy.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
        proxy.setClickable(true);
        proxy.setFocusable(true);
        proxy.setAccessibilityDelegate(new View.AccessibilityDelegate() {
            @Override public void onInitializeAccessibilityNodeInfo(View host,
                    AccessibilityNodeInfo info) {
                super.onInitializeAccessibilityNodeInfo(host, info);
                try { info.setViewIdResourceName(NMIRROR_VOICE_ID); } catch (Throwable ignored) { }
            }
        });
        proxy.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                Object cb = voiceCallback;
                if (cb instanceof kotlin.jvm.functions.Function0) {
                    try {
                        ((kotlin.jvm.functions.Function0<?>) cb).invoke();
                        KakaoHudLog.line("voice callback invoked");
                    } catch (Throwable t) {
                        KakaoHudLog.ex("voice invoke", t);
                    }
                } else {
                    KakaoHudLog.line("voice clicked but no callback yet");
                }
            }
        });
        ViewGroup.LayoutParams lp = new ViewGroup.LayoutParams(1, 1);
        try {
            container.addView(proxy, lp);
            voiceProxy = proxy;
            KakaoHudLog.line("voice proxy attached id=" + Integer.toHexString(NMIRROR_VOICE_RES_ID));
        } catch (Throwable t) {
            KakaoHudLog.ex("addVoiceProxy", t);
        }
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

    private void hookApplicationContext(final LoadPackageParam lpparam, final KakaoMap map) {
        try {
            XposedBridge.hookMethod(
                    Application.class.getMethod("onCreate"),
                    new XC_MethodHook() {
                        private boolean done = false;
                        @Override
                        protected void afterHookedMethod(MethodHookParam param) {
                            if (done) return;
                            try {
                                Context ctx = (Context) param.thisObject;
                                if (!KAKAO_PKG.equals(ctx.getPackageName())) return;
                                done = true;
                                map.start(lpparam.classLoader, ctx.getApplicationContext());
                                KakaoHudLog.line("app context ready, map capture started");
                                logAppVersion(ctx);
                            } catch (Throwable t) {
                                KakaoHudLog.ex("appContext", t);
                            }
                        }
                    });
        } catch (Throwable t) {
            KakaoHudLog.ex("hookApplicationContext", t);
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
