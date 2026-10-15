package ai.comma.naverhud;

import android.content.Context;

import java.util.Collections;
import java.util.Map;
import java.util.WeakHashMap;

import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.XposedHelpers;

/** Keep Naver's downloaded position icon sized for its original display density. */
final class NaverMarkerSize {
    private static final Map<Object, Float> sources =
            Collections.synchronizedMap(new WeakHashMap<>());
    private static final Map<Object, State> states = new WeakHashMap<>();
    private static volatile float iconSourceDensity;

    private static final class State {
        final Object icon;
        final float density;
        final float scale;
        final int width, height;

        State(Object icon, float density, float scale, int width, int height) {
            this.icon = icon;
            this.density = density;
            this.scale = scale;
            this.width = width;
            this.height = height;
        }
    }

    static void install(ClassLoader loader) {
        Class<?> manager = XposedHelpers.findClass(
                "com.naver.map.core.navigation.NaviCarvatarIconManager", loader);
        Class<?> wrapper = XposedHelpers.findClass(
                "com.naver.maps.navi.ui.map.map.NaverMapWrapper", loader);
        XposedBridge.hookAllConstructors(manager, new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam param) {
                try {
                    Context context = (Context) XposedHelpers.getObjectField(param.thisObject, "b");
                    if (context != null) iconSourceDensity = density(context);
                } catch (Throwable error) {
                    NaverHudLog.ex("marker density", error);
                }
            }
        });
        // 위치 아이콘을 넣는 메서드: 6.10 은 t(OverlayImage), 6.11 은 A(OverlayImage)(6.11 의 t 는
        // t(LatLng, double)). 이름 대신 OverlayImage 하나를 받는 void 메서드로 찾는다(하나일 때만).
        Class<?> overlayImage = XposedHelpers.findClass("com.naver.maps.map.overlay.OverlayImage", loader);
        java.lang.reflect.Method iconSetter = null;
        try {
            iconSetter = wrapper.getDeclaredMethod("t", overlayImage);   // 6.10.0.16
        } catch (NoSuchMethodException notTen) {
            for (java.lang.reflect.Method m : wrapper.getDeclaredMethods()) {
                Class<?>[] types = m.getParameterTypes();
                if (m.getReturnType() == void.class && types.length == 1 && types[0] == overlayImage) {
                    if (iconSetter != null) throw new IllegalStateException("several icon setters");
                    iconSetter = m;
                }
            }
        }
        if (iconSetter == null) throw new IllegalStateException("no icon setter");
        NaverHudLog.line("marker size: icon setter " + iconSetter.getName());
        XposedBridge.hookMethod(iconSetter, new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam param) {
                if (param.hasThrowable()) return;
                try {
                    if (param.args.length > 0 && param.args[0] != null
                            && valid(iconSourceDensity)
                            && !isResource(param.args[0])) {
                        sources.put(param.args[0], iconSourceDensity);
                    }
                    refresh(param.thisObject);
                } catch (Throwable error) {
                    NaverHudLog.ex("marker icon", error);
                }
            }
        });
        XC_MethodHook refresh = new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam param) {
                if (!param.hasThrowable()) {
                    try {
                        refresh(param.thisObject);
                    } catch (Throwable error) {
                        NaverHudLog.ex("marker refresh", error);
                    }
                }
            }
        };
        XposedBridge.hookAllMethods(wrapper, "I", refresh);
        XposedBridge.hookAllMethods(wrapper, "z0", refresh);
    }

    private static void refresh(Object wrapper) {
        try {
            if (XposedHelpers.getBooleanField(wrapper, "b0")) return;
            Object overlay = XposedHelpers.getObjectField(wrapper, "c0");
            Context context = (Context) XposedHelpers.callMethod(wrapper, "getContext");
            apply(overlay, context);
        } catch (Throwable ignored) {
            // Leave Naver's own map renderer running if a map is being released.
        }
    }

    private static void apply(Object overlay, Context context) {
        if (overlay == null || context == null) return;
        Object icon = XposedHelpers.callMethod(overlay, "getIcon");
        if (icon == null) return;
        float current = density(context);
        if (!valid(current)) return;
        int existingWidth = (Integer) XposedHelpers.callMethod(overlay, "getIconWidth");
        int existingHeight = (Integer) XposedHelpers.callMethod(overlay, "getIconHeight");
        // S9 HUD 네비모드면 내 차 아이콘도 지도와 같이 30% 작게(NaverZoomOffset).
        float hudScale = NaverZoomOffset.scale();
        State previous = states.get(overlay);
        if (previous != null && previous.icon == icon && previous.density == current
                && previous.scale == hudScale
                && existingWidth == previous.width && existingHeight == previous.height) return;

        Float source = sources.get(icon);
        boolean resource = isResource(icon);
        if (source == null && !resource && hudScale == 1f) {
            if (previous != null) {
                XposedHelpers.callMethod(overlay, "setIconWidth", 0);
                XposedHelpers.callMethod(overlay, "setIconHeight", 0);
                states.remove(overlay);
            }
            return;
        }
        int width = (Integer) XposedHelpers.callMethod(icon, "j", context);
        int height = (Integer) XposedHelpers.callMethod(icon, "i", context);
        if (source != null) {
            width = pixels(width, source, current);
            height = pixels(height, source, current);
        }
        if (hudScale != 1f) {
            width = Math.max(1, Math.round(width * hudScale));
            height = Math.max(1, Math.round(height * hudScale));
        }
        if (width <= 0 || height <= 0) return;
        if (existingWidth != width) XposedHelpers.callMethod(overlay, "setIconWidth", width);
        if (existingHeight != height) XposedHelpers.callMethod(overlay, "setIconHeight", height);
        states.put(overlay, new State(icon, current, hudScale, width, height));
        if (source != null && Math.abs(source - current) > 0.01f
                && (previous == null || previous.icon != icon || previous.density != current)) {
            NaverHudLog.line("position icon density " + source + " -> " + current
                    + ", size " + width + "x" + height);
        }
    }

    private static int pixels(int size, float source, float current) {
        if (size <= 0 || !valid(source) || !valid(current)) return 0;
        return Math.max(1, Math.round(size * current / source));
    }

    private static float density(Context context) {
        return context.getResources().getDisplayMetrics().density;
    }

    private static boolean isResource(Object icon) {
        return icon.getClass().getName().endsWith("$ResourceDescriptor");
    }

    private static boolean valid(float value) {
        return value > 0f && !Float.isNaN(value) && !Float.isInfinite(value);
    }

    private NaverMarkerSize() { }
}
