package ai.comma.naverhud;

import android.app.Application;
import android.app.Activity;
import android.content.Context;
import android.content.pm.PackageInfo;
import android.os.SystemClock;
import android.view.View;

import java.util.concurrent.atomic.AtomicBoolean;
import java.util.WeakHashMap;

import de.robv.android.xposed.IXposedHookLoadPackage;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.XposedHelpers;
import de.robv.android.xposed.callbacks.XC_LoadPackage.LoadPackageParam;

/** Attach the existing HUD bridge to the Play Store-signed Naver Map app. */
public final class NaverHudModule implements IXposedHookLoadPackage {
    private static final String NAVER_PACKAGE = "com.nhn.android.nmap";
    private static final String VERIFIED_VERSION = "6.10.0.16";
    // 6.12 은 클로바 버튼이 Compose 로 바뀌었다. 이 버전에서만 Compose 용 음성 버튼 훅을 건다
    // (jadx 로 확인: ClovaButtonController.onClick -> l(), ClovaButtonKt 의 onClick Function0).
    private static final String VOICE_COMPOSE_VERSION = "6.12.2.4";
    private static final AtomicBoolean attached = new AtomicBoolean(false);
    private static volatile NaverBridge bridge;
    private static final ThreadLocal<Object> composingClova = new ThreadLocal<>();
    private static final WeakHashMap<View, VoiceClick> voiceClicks = new WeakHashMap<>();

    @Override public void handleLoadPackage(final LoadPackageParam target) {
        if (!NAVER_PACKAGE.equals(target.packageName)
                || !NAVER_PACKAGE.equals(target.processName)) return;
        ai.comma.hudcommon.NavigationForeground.install(NAVER_PACKAGE, 2);
        XposedHelpers.findAndHookMethod(Application.class, "attach", Context.class,
                new XC_MethodHook() {
                    @Override protected void afterHookedMethod(MethodHookParam param) {
                        if (attached.get()) return;
                        Context app = (Context) param.args[0];
                        try {
                            PackageInfo info = app.getPackageManager().getPackageInfo(NAVER_PACKAGE, 0);
                            boolean verified = VERIFIED_VERSION.equals(info.versionName);
                            // 버전 제한 없이 동작한다. 단, 역할에 따라 나눈다.
                            //  - 읽기 전용(안내 상태 폴링, 지도 스냅샷): 모든 버전에서 켠다.
                            //    클래스 이름(NaviStore/MainActivity/MapProvider)은 난독화되지 않았고,
                            //    게터가 바뀌면 값이 비어 올 뿐 앱 동작에는 영향이 없다.
                            //  - 앱 동작을 바꾸는 훅(마커 크기, 음성 버튼): 검증된 버전에서만 켠다.
                            //    난독화 이름(Q/E/x/t)이 다른 메서드를 가리키면 엉뚱한 콜백이
                            //    실행되거나 아이콘이 깨질 수 있기 때문이다.
                            String mode = verified ? "verified" : "UNVERIFIED (read-only features only)";
                            log("Naver " + info.versionName + " " + mode);
                            NaverHudLog.line("Naver " + info.versionName + " " + mode);
                            // Keep music transport independent of navigation bridge startup.
                            int hooks = safeHook("Music", () -> NaverMusic.install(app, target.classLoader));
                            bridge = new NaverBridge(app);
                            bridge.start();
                            hooks += safeHook("NaviStore", () -> hookStore(target.classLoader));
                            hooks += safeHook("SignalDistance", () -> hookSignalDistance(target.classLoader));
                            hooks += safeHook("MainActivity", () -> hookActivity(target.classLoader));
                            hooks += safeHook("MapProvider", () -> hookMapProvider(target.classLoader));
                            hooks += safeHook("LaneView", () -> hookLaneView(target.classLoader));
                            hooks += safeHook("SignalView", () -> hookSignalView(target.classLoader));
                            // HUD 전용 지도(NaverHudMap)가 안내 지도의 경로선을 복사할 수 있게 기억한다.
                            NaverHudMap.installOverlayTracking(target.classLoader);
                            // HUD 네비모드 지도 축소: 메서드를 모양으로 찾으므로 모든 버전에서 시도한다
                            // (플레이스토어가 6.11 로 자동 업데이트해 축소가 꺼졌었다).
                            try {
                                NaverZoomOffset.install(target.classLoader, verified);
                            } catch (Throwable error) {
                                log("HUD zoom offset hook unavailable: " + error);
                                NaverHudLog.line("HUD zoom offset hook unavailable: " + error);
                            }
                            // 내 차 아이콘 크기 보정: 아이콘 설정 메서드를 모양으로 찾으므로 모든 버전에서
                            // 시도한다. 6.11 에서 꺼져 있어 첫 실행 때 내려받은 아이콘이 폰·HUD 모두 아주
                            // 크게 보였다(2026-10-15 실차).
                            try {
                                NaverMarkerSize.install(target.classLoader);
                            } catch (Throwable error) {
                                log("marker size hook unavailable: " + error);
                                NaverHudLog.line("marker size hook unavailable: " + error);
                            }
                            if (verified) {
                                try {
                                    hookVoiceButton(target.classLoader, app);
                                } catch (Throwable error) {
                                    log("voice hook unavailable: " + error);
                                }
                            } else if (VOICE_COMPOSE_VERSION.equals(info.versionName)) {
                                try {
                                    hookVoiceButtonCompose(target.classLoader, app);
                                } catch (Throwable error) {
                                    log("compose voice hook unavailable: " + error);
                                    NaverHudLog.line("compose voice hook unavailable: " + error);
                                }
                            } else {
                                NaverHudLog.line("voice hook skipped on unverified version");
                            }
                            attached.set(true);
                            log("ready for Naver " + info.versionName + " hooks=" + hooks);
                        } catch (Throwable error) {
                            log("attach failed: " + error);
                            XposedBridge.log(error);
                        }
                    }
                });
    }

    private interface Hook { void run() throws Throwable; }

    /** 한 훅이 실패해도 나머지는 계속 건다. 성공하면 1. */
    private static int safeHook(String name, Hook hook) {
        try {
            hook.run();
            return 1;
        } catch (Throwable error) {
            log(name + " hook unavailable: " + error);
            NaverHudLog.line(name + " hook unavailable: " + error);
            return 0;
        }
    }

    private static void hookStore(ClassLoader appLoader) {
        Class<?> store = XposedHelpers.findClass("com.naver.map.core.navigation.NaviStore", appLoader);
        XposedBridge.hookAllConstructors(store, new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam param) {
                try {
                    bridge.setStore(param.thisObject);
                } catch (Throwable error) {
                    log("NaviStore update failed: " + error);
                }
            }
        });
    }

    /**
     * 신호등까지 거리(읽기 전용). 6.12.2.4 의 TrafficSignalInfo 에는 거리가 없다(mid=교차로 번호,
     * 신호 목록, 점멸, 디버그 문자열). 예전처럼 j() 를 거리로 보내면 교차로 번호가 거리로 갔다.
     * 거리는 NaviStore 가 TrafficSignalInfo 를 만들 때 넘겨받는 GuidanceTrafficSignal 에 있다.
     * 그 메서드(6.12.2.4 이름 z)를 이름 대신 모양 (GuidanceTrafficSignal, TrafficSignalInfo) ->
     * TrafficSignalInfo 로 찾아, 들어온 거리와 교차로 번호를 기억한다(하나일 때만).
     */
    private static void hookSignalDistance(ClassLoader appLoader) {
        Class<?> store = XposedHelpers.findClass("com.naver.map.core.navigation.NaviStore", appLoader);
        Class<?> guidance = XposedHelpers.findClass(
                "com.naver.maps.navi.v2.api.guidance.model.GuidanceTrafficSignal", appLoader);
        Class<?> info = XposedHelpers.findClass("com.naver.map.core.navigation.model.TrafficSignalInfo", appLoader);
        java.lang.reflect.Method target = null;
        for (java.lang.reflect.Method m : store.getDeclaredMethods()) {
            Class<?>[] p = m.getParameterTypes();
            if (m.getReturnType() == info && p.length == 2 && p[0] == guidance && p[1] == info) {
                if (target != null) throw new IllegalStateException("several signal builders");
                target = m;
            }
        }
        if (target == null) throw new IllegalStateException("signal builder not found");
        XposedBridge.hookMethod(target, new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam param) {
                try {
                    NaverBridge.onSignalDistance(param.args[0]);
                } catch (Throwable error) {
                    log("signal distance failed: " + error);
                }
            }
        });
        NaverHudLog.line("signal distance hook: NaviStore." + target.getName());
    }

    /** 폰 차로 표시가 바뀔 때마다 HUD 차로 띠 그림을 다시 뜬다(읽기 전용). */
    private static void hookLaneView(ClassLoader appLoader) {
        Class<?> view = XposedHelpers.findClassIfExists(NaverViewImage.LANE_VIEW_611, appLoader);
        if (view == null) view = XposedHelpers.findClass(NaverViewImage.LANE_VIEW, appLoader);
        Class<?> item = XposedHelpers.findClass("com.naver.map.core.navigation.lane.NaviLaneItem", appLoader);
        XposedHelpers.findAndHookMethod(view, "a", item, boolean.class, new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam param) {
                try {
                    bridge.onLaneView((View) param.thisObject, param.args[0]);
                } catch (Throwable error) {
                    log("lane view update failed: " + error);
                }
            }
        });
    }

    /** 폰 신호등 표시가 바뀔 때마다 HUD 신호등 그림을 다시 뜬다(읽기 전용). */
    private static void hookSignalView(ClassLoader appLoader) {
        Class<?> view = XposedHelpers.findClass(NaverViewImage.SIGNAL_VIEW, appLoader);
        Class<?> info = XposedHelpers.findClass("com.naver.map.core.navigation.model.TrafficSignalInfo", appLoader);
        XposedHelpers.findAndHookMethod(view, "a", boolean.class, info, new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam param) {
                try {
                    bridge.onSignalView((View) param.thisObject, (Boolean) param.args[0], param.args[1]);
                } catch (Throwable error) {
                    log("signal view update failed: " + error);
                }
            }
        });
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

    private static void hookActivity(ClassLoader appLoader) {
        Class<?> activity = XposedHelpers.findClass("com.naver.map.MainActivity", appLoader);
        XposedBridge.hookAllMethods(activity, "onResume", new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam param) {
                try {
                    notifyHudForeground((Activity) param.thisObject, 2);
                } catch (Throwable error) {
                    log("HUD foreground notify failed: " + error);
                }
                try {
                    bridge.setActivity((Activity) param.thisObject);
                } catch (Throwable error) {
                    log("MainActivity capture attach failed: " + error);
                }
            }
        });
    }

    private static void hookMapProvider(ClassLoader appLoader) {
        Class<?> provider = XposedHelpers.findClass("com.naver.map.core.auto.map.MapProvider", appLoader);
        XposedBridge.hookAllConstructors(provider, new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam param) {
                try {
                    bridge.setMapProvider(param.thisObject);
                } catch (Throwable error) {
                    log("MapProvider capture failed: " + error);
                }
            }
        });
    }

    private static void hookVoiceButton(ClassLoader appLoader, Context app) {
        final int speechId = app.getResources().getIdentifier(
                "btn_speech_recognition", "id", NAVER_PACKAGE);
        if (speechId == 0) throw new IllegalStateException("Naver voice resource missing");
        Class<?> component = XposedHelpers.findClass(
                "com.naver.map.feature.navigation.renewal.clova.NaviClovaButtonComponent", appLoader);
        Class<?> mapButton = XposedHelpers.findClass(
                "com.naver.map.core.common.map.controls.MapButtonKt", appLoader);

        // Q() creates the real Clova Function0 and passes it to MapButtonKt.E().
        // Capture only that call, not the callbacks of unrelated map buttons.
        XposedBridge.hookAllMethods(component, "Q", new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam param) {
                if (param.args.length > 3) composingClova.set(param.args[3]);
            }

            @Override protected void afterHookedMethod(MethodHookParam param) {
                composingClova.remove();
            }
        });
        XposedBridge.hookAllMethods(mapButton, "E", new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam param) {
                Object current = composingClova.get();
                if (current == null || param.args.length < 5 || param.args[4] == null) return;
                try {
                    Object host = XposedHelpers.callMethod(current, "x");
                    if (host instanceof View) bindVoice((View) host, param.args[4], speechId);
                } catch (Throwable error) {
                    log("voice button bind failed: " + error);
                }
            }
        });
        log("voice accessibility hook ready, id=" + Integer.toHexString(speechId));
    }

    private static final String CLOVA_CONTROLLER =
            "com.naver.map.feature.navigation.renewal.clova.ClovaButtonController";
    private static final String CLOVA_BUTTON_KT =
            "com.naver.map.feature.navigation.renewal.clova.compose.ClovaButtonKt";
    // 마지막으로 만들어진 ClovaButtonController 의 화면(Fragment). 누름은 이 화면이 열려 있을 때만.
    private static volatile java.lang.ref.WeakReference<Object> clovaFragment;
    private static volatile View composeVoiceHost;

    /**
     * 6.12: 클로바 버튼이 Compose 라 버튼 자체에 View 가 없다. 엔미러 접근성 서비스가 찾는 id
     * (btn_speech_recognition)를 단 아주 작은(2x2 px, 거의 투명) View 를 화면 맨 위 구석에 붙이고,
     * 누르면 네이버가 버튼에 넘긴 onClick(ClovaButtonController.onClick)을 그대로 부른다.
     * 클래스·메서드는 이름 대신 모양으로 찾고, 하나가 아니면 걸지 않는다.
     */
    private static void hookVoiceButtonCompose(ClassLoader appLoader, Context app) {
        final int speechId = app.getResources().getIdentifier(
                "btn_speech_recognition", "id", NAVER_PACKAGE);
        if (speechId == 0) throw new IllegalStateException("Naver voice resource missing");
        Class<?> controller = XposedHelpers.findClass(CLOVA_CONTROLLER, appLoader);
        Class<?> buttonKt = XposedHelpers.findClass(CLOVA_BUTTON_KT, appLoader);
        Class<?> fragmentType = XposedHelpers.findClass("androidx.fragment.app.Fragment", appLoader);
        Class<?> function0 = XposedHelpers.findClass("kotlin.jvm.functions.Function0", appLoader);

        java.lang.reflect.Field fragmentField = null;
        for (java.lang.reflect.Field f : controller.getDeclaredFields()) {
            if (java.lang.reflect.Modifier.isStatic(f.getModifiers())) continue;
            if (fragmentType.isAssignableFrom(f.getType())) {
                if (fragmentField != null) throw new IllegalStateException("several fragment fields");
                f.setAccessible(true);
                fragmentField = f;
            }
        }
        if (fragmentField == null) throw new IllegalStateException("controller fragment field");
        java.lang.reflect.Method button = null;
        for (java.lang.reflect.Method m : buttonKt.getDeclaredMethods()) {
            Class<?>[] p = m.getParameterTypes();
            if (java.lang.reflect.Modifier.isStatic(m.getModifiers()) && m.getReturnType() == void.class
                    && p.length >= 4 && p[3] == function0
                    && "VoiceAssistantSessionViewModel".equals(p[0].getSimpleName())) {
                if (button != null) throw new IllegalStateException("several clova buttons");
                button = m;
            }
        }
        if (button == null) throw new IllegalStateException("clova button composable");

        final java.lang.reflect.Field fragmentOf = fragmentField;
        XposedBridge.hookAllConstructors(controller, new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam param) {
                try {
                    clovaFragment = new java.lang.ref.WeakReference<>(fragmentOf.get(param.thisObject));
                } catch (Throwable error) {
                    log("clova controller capture failed: " + error);
                }
            }
        });
        XposedBridge.hookMethod(button, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam param) {
                Object callback = param.args[3];
                if (callback == null) return;
                try {
                    bindComposeVoice(callback, speechId);
                } catch (Throwable error) {
                    log("compose voice bind failed: " + error);
                }
            }
        });
        log("compose voice accessibility hook ready (" + button.getName() + "), id="
                + Integer.toHexString(speechId));
        NaverHudLog.line("compose voice hook ready: ClovaButtonKt." + button.getName());
    }

    private static void bindComposeVoice(Object callback, int speechId) {
        java.lang.ref.WeakReference<Object> ref = clovaFragment;
        Object fragment = ref == null ? null : ref.get();
        if (fragment == null) return;
        Object activity = XposedHelpers.callMethod(fragment, "getActivity");
        if (!(activity instanceof Activity)) return;
        View decor = ((Activity) activity).getWindow().getDecorView();
        if (!(decor instanceof android.view.ViewGroup)) return;
        View host = composeVoiceHost;
        if (host == null || host.getContext() != activity || host.getParent() == null) {
            host = new View((Activity) activity);
            // 접근성은 alpha 0 인 View 를 "안 보임"으로 본다. 아주 작게, 거의 투명하게.
            host.setAlpha(0.02f);
            host.setBackgroundColor(android.graphics.Color.TRANSPARENT);
            android.widget.FrameLayout.LayoutParams lp = new android.widget.FrameLayout.LayoutParams(
                    2, 2, android.view.Gravity.TOP | android.view.Gravity.START);
            ((android.view.ViewGroup) decor).addView(host, lp);
            composeVoiceHost = host;
            NaverHudLog.line("compose voice host attached");
        }
        bindVoice(host, callback, speechId);
        VoiceClick click;
        synchronized (voiceClicks) {
            click = voiceClicks.get(host);
        }
        if (click != null) click.fragment = ref;
    }

    private static void bindVoice(View host, Object callback, int speechId) {
        host.setId(speechId);
        host.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
        host.setContentDescription("클로바 음성인식");
        VoiceClick click;
        synchronized (voiceClicks) {
            click = voiceClicks.get(host);
            if (click == null) {
                click = new VoiceClick();
                voiceClicks.put(host, click);
                host.setOnClickListener(click);
            }
            click.callback = callback;
        }
    }

    private static final class VoiceClick implements View.OnClickListener {
        private volatile Object callback;
        // 6.12 Compose 경로: 버튼을 만든 화면. 그 화면이 붙어 있고 앞에 있을 때만 누른다
        // (닫힌 화면의 onClick 을 부르면 네이버가 requireContext 에서 죽을 수 있다).
        private volatile java.lang.ref.WeakReference<Object> fragment;
        private long lastClick = -1L;

        @Override public void onClick(View view) {
            if (!view.isAttachedToWindow() || !view.isShown() || !view.isEnabled()) return;
            java.lang.ref.WeakReference<Object> ref = fragment;
            if (ref != null) {
                Object owner = ref.get();
                try {
                    if (owner == null || !Boolean.TRUE.equals(XposedHelpers.callMethod(owner, "isAdded"))
                            || !Boolean.TRUE.equals(XposedHelpers.callMethod(owner, "isResumed"))) {
                        log("voice click ignored: Clova screen not open");
                        return;
                    }
                } catch (Throwable error) {
                    log("voice click check failed: " + error);
                    return;
                }
            }
            long now = SystemClock.uptimeMillis();
            if (lastClick >= 0 && now - lastClick < 350L) return;
            lastClick = now;
            try {
                Object current = callback;
                if (current != null) {
                    current.getClass().getMethod("invoke").invoke(current);
                    log("voice accessibility click invoked original Clova callback");
                }
            } catch (Throwable error) {
                log("voice click failed: " + error);
            }
        }
    }

    private static void log(String message) {
        XposedBridge.log("NaverHudModule: " + message);
    }
}
