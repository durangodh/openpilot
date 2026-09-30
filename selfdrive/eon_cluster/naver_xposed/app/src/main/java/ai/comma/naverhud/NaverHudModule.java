package ai.comma.naverhud;

import android.app.Application;
import android.content.Context;
import android.content.pm.PackageInfo;
import android.os.Build;
import android.os.SystemClock;
import android.view.View;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.lang.reflect.Method;
import java.nio.ByteBuffer;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.WeakHashMap;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

import dalvik.system.InMemoryDexClassLoader;
import de.robv.android.xposed.IXposedHookLoadPackage;
import de.robv.android.xposed.IXposedHookZygoteInit;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.XposedHelpers;
import de.robv.android.xposed.callbacks.XC_LoadPackage.LoadPackageParam;

/** Attach the existing HUD bridge to the Play Store-signed Naver Map app. */
public final class NaverHudModule implements IXposedHookLoadPackage, IXposedHookZygoteInit {
    private static final String NAVER_PACKAGE = "com.nhn.android.nmap";
    private static final String VERIFIED_VERSION = "6.10.0.16";
    private static volatile String modulePath;
    private static final AtomicBoolean attached = new AtomicBoolean(false);
    private static volatile Method update;
    private static volatile Method setActivity;
    private static final ThreadLocal<Object> composingClova = new ThreadLocal<>();
    private static final WeakHashMap<View, VoiceClick> voiceClicks = new WeakHashMap<>();

    @Override public void initZygote(StartupParam startupParam) {
        modulePath = startupParam.modulePath;
    }

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
                            if (Build.VERSION.SDK_INT < 26) return;
                            Class<?> bridge = loadBridge(target.classLoader);
                            update = bridge.getMethod("update", Object.class);
                            setActivity = bridge.getMethod("setActivity", Object.class);
                            hookStore(target.classLoader);
                            hookActivity(target.classLoader);
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

    private static Class<?> loadBridge(ClassLoader appLoader) throws Exception {
        if (modulePath == null) throw new IllegalStateException("module APK path unavailable");
        byte[] dex;
        try (ZipFile apk = new ZipFile(modulePath)) {
            ZipEntry entry = apk.getEntry("assets/naver_bridge.dex");
            if (entry == null) throw new IllegalStateException("bridge DEX missing from module APK");
            try (InputStream stream = apk.getInputStream(entry);
             ByteArrayOutputStream bytes = new ByteArrayOutputStream(65536)) {
                byte[] chunk = new byte[8192];
                int count;
                while ((count = stream.read(chunk)) != -1) bytes.write(chunk, 0, count);
                dex = bytes.toByteArray();
            }
        }
        // The bridge refers to Naver's classes through reflection. Parent it to
        // the target app so those classes remain visible without altering its APK.
        ClassLoader bridgeLoader = new InMemoryDexClassLoader(ByteBuffer.wrap(dex), appLoader);
        return Class.forName("com.naver.map.carrot.CarrotNaverBridge", true, bridgeLoader);
    }

    private static void hookStore(ClassLoader appLoader) {
        Class<?> store = XposedHelpers.findClass("com.naver.map.core.navigation.NaviStore", appLoader);
        XposedBridge.hookAllConstructors(store, new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam param) {
                try {
                    update.invoke(null, param.thisObject);
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
                    setActivity.invoke(null, param.thisObject);
                } catch (Throwable error) {
                    log("MainActivity capture attach failed: " + error);
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
