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
        final int width, height;

        State(Object icon, float density, int width, int height) {
            this.icon = icon;
            this.density = density;
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
        XposedBridge.hookAllMethods(wrapper, "t", new XC_MethodHook() {
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
        State previous = states.get(overlay);
        if (previous != null && previous.icon == icon && previous.density == current
                && existingWidth == previous.width && existingHeight == previous.height) return;

        Float source = sources.get(icon);
        boolean resource = isResource(icon);
        if (source == null && !resource) {
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
        if (width <= 0 || height <= 0) return;
        if (existingWidth != width) XposedHelpers.callMethod(overlay, "setIconWidth", width);
        if (existingHeight != height) XposedHelpers.callMethod(overlay, "setIconHeight", height);
        states.put(overlay, new State(icon, current, width, height));
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
