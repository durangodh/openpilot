package ai.comma.tmaphud;

import android.app.Application;
import android.content.Context;
import android.view.View;

import java.lang.reflect.Method;

import de.robv.android.xposed.IXposedHookLoadPackage;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.callbacks.XC_LoadPackage.LoadPackageParam;

/**
 * 플레이스토어 원본 티맵(com.skt.tmap.ku)용 LSPosed 모듈.
 *
 * 캐롯 패치판(smali 편집 + 재서명)이 EON 으로 보내던 7714 데이터를 원본 APK 를
 * 고치지 않고 후킹으로 만든다. 모든 후킹은 afterHookedMethod 에서 값을 읽기만
 * 하며 인자·반환값·앱 상태를 바꾸지 않는다.
 *
 * 후킹(전부 난독화되지 않은 이름):
 *   NavigationManager.setLastRGData(RGData)                 안내·차로·SDI·차량·남은 거리
 *   TrafficSignalInfoRepository.onSignalInfoChanged(...)    C-ITS 신호등
 *   VSMMapView 생성자                                        지도 캡처 대상
 *
 * 버전 정책: VERIFIED_VERSION 에서 확인했다. 다른 버전에서도 읽기 전용 후킹은
 * 그대로 켜고(이름이 없으면 해당 후킹만 빠지고 로그에 남는다), 앱 동작을 바꾸는
 * 후킹은 켜지 않는다. 1차 구현에는 앱 동작을 바꾸는 후킹이 없다.
 */
public final class TmapHudModule implements IXposedHookLoadPackage {

    static final String TMAP_PKG = "com.skt.tmap.ku";
    /** 정적 분석·후킹 이름을 확인한 버전(versionCode 4003). */
    static final String VERIFIED_VERSION = "11.8.3.4061";

    private static final String NAV_MANAGER =
            "com.skt.tmap.engine.navigation.NavigationManager";
    private static final String RG_DATA =
            "com.skt.tmap.engine.navigation.data.RGData";
    private static final String SIGNAL_REPOSITORY =
            "com.skt.tmap.engine.navigation.TrafficSignalInfoRepository";
    private static final String SIGNAL_STATE =
            "com.skt.tmap.engine.navigation.data.TrafficSignalStateInfo";
    private static final String MAP_VIEW = "com.skt.tmap.vsm.map.VSMMapView";

    /** 확인한 버전에서만 true. 앱 동작을 바꾸는 후킹은 이 값이 true 일 때만 설치한다. */
    static volatile boolean behaviorHooksAllowed = false;

    private static boolean loaded = false;

    @Override
    public void handleLoadPackage(LoadPackageParam lpparam) {
        if (!TMAP_PKG.equals(lpparam.packageName) || loaded) return;
        // 티맵은 보조 프로세스를 쓴다. 내비 엔진과 지도가 있는 주 프로세스만.
        if (lpparam.processName != null && !TMAP_PKG.equals(lpparam.processName)) return;
        loaded = true;
        TmapHudLog.line("=== TmapHudModule loaded in " + lpparam.processName + " ===");

        final TmapNaviClient client = new TmapNaviClient();
        final TmapSignal signal = new TmapSignal(client);
        final TmapImages images = new TmapImages(client);
        final TmapBridge bridge = new TmapBridge(client, signal, images);
        final TmapMapCapture map = new TmapMapCapture(client);
        final TmapMapRender render = new TmapMapRender(client, map);
        final ClassLoader loader = lpparam.classLoader;
        new EonDiscovery(client).start();

        ClassLoader cl = lpparam.classLoader;
        int hooks = 0;
        hooks += hookRGData(cl, bridge);
        hooks += hookTrafficSignal(cl, bridge);
        hooks += hookMapViews(cl, map);
        hookApplication(bridge, map, images, render, loader);
        TmapHudLog.line("read-only hooks installed = " + hooks + "/3");
    }

    private static void hookApplication(final TmapBridge bridge, final TmapMapCapture map,
                                        final TmapImages images, final TmapMapRender render,
                                        final ClassLoader loader) {
        try {
            XposedBridge.hookMethod(Application.class.getMethod("onCreate"), new XC_MethodHook() {
                private boolean done = false;

                @Override
                protected void afterHookedMethod(MethodHookParam param) {
                    if (done) return;
                    try {
                        Context ctx = (Context) param.thisObject;
                        if (!TMAP_PKG.equals(ctx.getPackageName())) return;
                        done = true;
                        checkVersion(ctx);
                        final Context app = ctx.getApplicationContext();
                        images.setContext(app);
                        // 패치판처럼 지도 엔진을 하나 더 만들어 그린다. 티맵 내부에 지도 보기를
                        // 등록하므로 확인한 버전에서만. 그 외에는 화면 캡처(PixelCopy).
                        if (behaviorHooksAllowed) {
                            bridge.setOnLive(() -> render.maybeStart(app, loader));
                        } else {
                            TmapHudLog.line("map render engine off on unverified version; screen capture only");
                        }
                        bridge.start();
                        map.start();
                    } catch (Throwable t) {
                        TmapHudLog.ex("appContext", t);
                    }
                }
            });
        } catch (Throwable t) {
            TmapHudLog.ex("hookApplication", t);
        }
    }

    private static void checkVersion(Context ctx) {
        try {
            android.content.pm.PackageInfo info = ctx.getPackageManager().getPackageInfo(TMAP_PKG, 0);
            boolean verified = VERIFIED_VERSION.equals(info.versionName);
            behaviorHooksAllowed = verified;
            TmapHudLog.line("TMAP " + info.versionName + (verified
                    ? " verified"
                    : " UNVERIFIED (read-only features only; behavior hooks stay off)"));
        } catch (Throwable t) {
            behaviorHooksAllowed = false;
            TmapHudLog.ex("appVersion", t);
        }
    }

    private static int hookRGData(ClassLoader cl, final TmapBridge bridge) {
        try {
            Class<?> manager = cl.loadClass(NAV_MANAGER);
            Method m = manager.getDeclaredMethod("setLastRGData", cl.loadClass(RG_DATA));
            XposedBridge.hookMethod(m, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
                    try {
                        bridge.onRGData(param.thisObject, param.args[0]);
                    } catch (Throwable t) {
                        TmapHudLog.ex("hook-rg", t);
                    }
                }
            });
            TmapHudLog.line("hooked NavigationManager.setLastRGData");
            return 1;
        } catch (Throwable t) {
            TmapHudLog.ex("hookRGData", t);
            return 0;
        }
    }

    private static int hookTrafficSignal(ClassLoader cl, final TmapBridge bridge) {
        try {
            Class<?> repo = cl.loadClass(SIGNAL_REPOSITORY);
            Method m = repo.getDeclaredMethod("onSignalInfoChanged", cl.loadClass(SIGNAL_STATE));
            XposedBridge.hookMethod(m, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
                    try {
                        bridge.onSignalInfo(param.thisObject, param.args[0]);
                    } catch (Throwable t) {
                        TmapHudLog.ex("hook-signal", t);
                    }
                }
            });
            TmapHudLog.line("hooked TrafficSignalInfoRepository.onSignalInfoChanged");
            return 1;
        } catch (Throwable t) {
            TmapHudLog.ex("hookTrafficSignal", t);
            return 0;
        }
    }

    private static int hookMapViews(ClassLoader cl, final TmapMapCapture map) {
        try {
            Class<?> view = cl.loadClass(MAP_VIEW);
            XposedBridge.hookAllConstructors(view, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
                    if (param.thisObject instanceof View) map.addView((View) param.thisObject);
                }
            });
            TmapHudLog.line("hooked VSMMapView constructors");
            return 1;
        } catch (Throwable t) {
            TmapHudLog.ex("hookMapViews", t);
            return 0;
        }
    }
}
