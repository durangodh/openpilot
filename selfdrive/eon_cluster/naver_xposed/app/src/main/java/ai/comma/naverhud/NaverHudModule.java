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
                            if (!VERIFIED_VERSION.equals(info.versionName)) {
                                log("unsupported Naver version " + info.versionName
                                        + "; leaving the app unchanged");
                                return;
                            }
                            bridge = new NaverBridge();
                            hookStore(target.classLoader);
                            hookActivity(target.classLoader);
                            hookMapProvider(target.classLoader);
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
                            attached.set(true);
                            log("ready for Naver " + info.versionName);
                        } catch (Throwable error) {
                            log("attach failed: " + error);
                            XposedBridge.log(error);
                        }
                    }
                });
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
