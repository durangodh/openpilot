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
    private static final AtomicBoolean attached = new AtomicBoolean(false);
    private static volatile NaverBridge bridge;
    private static final ThreadLocal<Object> composingClova = new ThreadLocal<>();
    private static final WeakHashMap<View, VoiceClick> voiceClicks = new WeakHashMap<>();

    @Override public void handleLoadPackage(final LoadPackageParam target) {
        if (!NAVER_PACKAGE.equals(target.packageName)
                || !NAVER_PACKAGE.equals(target.processName)) return;
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
                            bridge = new NaverBridge();
                            bridge.start();
                            int hooks = 0;
                            hooks += safeHook("NaviStore", () -> hookStore(target.classLoader));
                            hooks += safeHook("MainActivity", () -> hookActivity(target.classLoader));
                            hooks += safeHook("MapProvider", () -> hookMapProvider(target.classLoader));
                            hooks += safeHook("LaneView", () -> hookLaneView(target.classLoader));
                            if (verified) {
                                try {
                                    NaverMarkerSize.install(target.classLoader);
                                } catch (Throwable error) {
                                    log("marker size hook unavailable: " + error);
                                }
                                try {
                                    hookVoiceButton(target.classLoader, app);
                                } catch (Throwable error) {
                                    log("voice hook unavailable: " + error);
                                }
                                // 지도 엔진 직접 렌더(앱 내부에 안내 렌더러를 하나 더 만든다).
                                bridge.enableMapRender(app);
                            } else {
                                NaverHudLog.line("marker-size/voice hooks and map render skipped on unverified version");
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

    /** 폰 차로 표시가 바뀔 때마다 HUD 차로 띠 그림을 다시 뜬다(읽기 전용). */
    private static void hookLaneView(ClassLoader appLoader) {
        Class<?> view = XposedHelpers.findClass(NaverLaneImage.VIEW, appLoader);
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

    private static void hookActivity(ClassLoader appLoader) {
        Class<?> activity = XposedHelpers.findClass("com.naver.map.MainActivity", appLoader);
        XposedBridge.hookAllMethods(activity, "onResume", new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam param) {
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
        private long lastClick = -1L;

        @Override public void onClick(View view) {
            if (!view.isAttachedToWindow() || !view.isShown() || !view.isEnabled()) return;
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
