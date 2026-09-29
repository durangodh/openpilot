package ai.comma.kakaohud;

import android.app.Application;
import android.content.Context;
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

    private static final String KAKAO_VOICE_DESCRIPTION = "음성서비스";
    private static final String NMIRROR_VOICE_ID =
            KAKAO_PKG + ":id/btn_speech_recognition";

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

        hookVoiceAccessibility();
        hookApplicationContext(lpparam, map);
        hookScreenCamera(lpparam, map);
        hookRepository(lpparam, bridge);
        hookGuideCallbacks(lpparam, bridge);
    }

    /**
     * nMirror NavigationButtonService 는 resource-id 로 음성 버튼을 찾아
     * ACTION_CLICK 한다. 카카오 Compose 의 가상 노드는 ID 없이 "음성서비스"
     * 설명만 노출한다. 이 기기에서는 setContentDescription 후크가 호출되지
     * 않아 ID 가 비어 있었으므로, IPC 직전의 parcel 경로에서도 보강한다.
     */
    private void hookVoiceAccessibility() {
        try {
            Method setter = AccessibilityNodeInfo.class.getMethod(
                    "setContentDescription", CharSequence.class);
            XposedBridge.hookMethod(setter, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
                    try {
                        CharSequence description = (CharSequence) param.args[0];
                        if (description == null
                                || !KAKAO_VOICE_DESCRIPTION.contentEquals(description)) return;
                        exposeVoiceNode((AccessibilityNodeInfo) param.thisObject);
                    } catch (Throwable t) {
                        KakaoHudLog.ex("voiceAccessibility", t);
                    }
                }
            });

            XC_MethodHook parcelHook = new XC_MethodHook() {
                @Override
                protected void beforeHookedMethod(MethodHookParam param) {
                    try {
                        exposeVoiceNode((AccessibilityNodeInfo) param.thisObject);
                    } catch (Throwable t) {
                        KakaoHudLog.ex("voiceParcel", t);
                    }
                }
            };
            int parcelHooks = XposedBridge.hookAllMethods(
                    AccessibilityNodeInfo.class, "writeToParcel", parcelHook).size();
            try {
                parcelHooks += XposedBridge.hookAllMethods(
                        AccessibilityNodeInfo.class, "writeToParcelNoRecycle", parcelHook).size();
            } catch (Throwable t) {
                KakaoHudLog.ex("voiceParcelNoRecycle", t);
            }
            KakaoHudLog.line("voice accessibility hook ready, parcel methods=" + parcelHooks);
        } catch (Throwable t) {
            KakaoHudLog.ex("hookVoiceAccessibility", t);
        }
    }

    private void exposeVoiceNode(AccessibilityNodeInfo node) {
        CharSequence description = node.getContentDescription();
        if (description == null || !KAKAO_VOICE_DESCRIPTION.contentEquals(description)
                || !node.isClickable() || !node.isEnabled()) return;
        CharSequence packageName = node.getPackageName();
        if (packageName != null && !KAKAO_PKG.contentEquals(packageName)) return;
        String existingId = node.getViewIdResourceName();
        if (existingId != null && !existingId.isEmpty()) return;
        try {
            node.setViewIdResourceName(NMIRROR_VOICE_ID);
        } catch (IllegalStateException sealed) {
            // AccessibilityInteractionController may seal the node before IPC.
            XposedHelpers.setObjectField(node, "mViewIdResourceName", NMIRROR_VOICE_ID);
        }
        if (voiceNodeLogged.compareAndSet(false, true)) {
            KakaoHudLog.line("voice accessibility node exposed: " + NMIRROR_VOICE_ID);
        }
    }

    /** Application.onCreate 를 후킹해 Context 를 얻어 지도 캡처 스레드를 시작한다. */
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
            // 안전: 인자가 List 인 콜백($e.a). List 를 인자로 받는 KNUSDKRepository
            // 내부 콜백을 후킹한다(오검출 방지 위해 List 전용).
            hooked += hookByArgType(cl, java.util.List.class, new Extractor() {
                @Override public void extract(Object arg) { bridge.onSafeties(arg); }
            }, "safety");

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
