package ai.comma.naverhud;

import android.app.Activity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.lang.reflect.Proxy;

/**
 * Naver 6.12: the HUD traffic signal drawn by Naver's own signal composable (2026-11-24), the same
 * way as NaverLaneCompose and as Android Auto's TrafficSignalControl:
 *   state = TrafficSignalKt.p(enabled, visible, TrafficSignalInfo, z)   (phone/AA state builder)
 *   TrafficSignalKt.h(state, false, null, null, composer, TrafficSignalUiState.f, 4)   (phone call)
 * Lamps, arrows and the countdown are Naver's pixels. Fails: the module's own signal image.
 */
final class NaverSignalCompose {
    private static final String COMPOSE_VIEW = "androidx.compose.ui.platform.ComposeView";
    private static final String SIGNAL_STATE = "com.naver.map.core.navigation.compose.TrafficSignalUiState";
    private static final String SIGNAL_KT = "com.naver.map.core.navigation.compose.TrafficSignalKt";
    private static final String SIGNAL_INFO = "com.naver.map.core.navigation.model.TrafficSignalInfo";

    private boolean failed, logged;
    private View view;
    private Activity host;
    private Method setContent, signalDraw, signalState;
    private Class<?> function2;
    private Object unit;
    private int version;

    boolean failed() {
        return failed;
    }

    /** The view NaverViewImage draws, or null. */
    View view() {
        return failed ? null : view;
    }

    /** Main thread: show this TrafficSignalInfo (null: nothing). Returns false when unusable. */
    boolean update(Activity activity, Object item) {
        if (failed || activity == null) return false;
        try {
            ClassLoader cl = activity.getClassLoader();
            if (item != null) cl = item.getClass().getClassLoader();
            if (signalDraw == null) resolve(cl);
            if (view == null || host != activity) attach(activity, cl);
            final Object state = signalState.invoke(null, item != null, item != null, item, false);
            Object content = Proxy.newProxyInstance(cl, new Class<?>[]{function2}, new Content(state));
            setContent.invoke(view, content);
            if (!logged) {
                logged = true;
                NaverHudLog.line("signal compose: drawing Naver's TrafficSignalKt off screen (6.12)");
            }
            return true;
        } catch (Throwable error) {
            fail("signal compose", error);
            return false;
        }
    }

    private void resolve(ClassLoader cl) throws Exception {
        Class<?> composeView = cl.loadClass(COMPOSE_VIEW);
        Class<?> stateClass = cl.loadClass(SIGNAL_STATE);
        Class<?> infoClass = cl.loadClass(SIGNAL_INFO);
        function2 = cl.loadClass("kotlin.jvm.functions.Function2");
        setContent = composeView.getMethod("setContent", function2);
        Class<?> function0 = cl.loadClass("kotlin.jvm.functions.Function0");
        for (Method m : cl.loadClass(SIGNAL_KT).getDeclaredMethods()) {
            Class<?>[] p = m.getParameterTypes();
            if (!Modifier.isStatic(m.getModifiers())) continue;
            if (p.length == 4 && p[0] == boolean.class && p[1] == boolean.class && p[2] == infoClass
                    && p[3] == boolean.class && m.getReturnType() == stateClass) {
                signalState = m;
            }
            if (p.length == 7 && p[0] == stateClass && p[1] == boolean.class && p[3] == function0
                    && p[5] == int.class && p[6] == int.class) {
                signalDraw = m;
            }
        }
        if (signalState == null || signalDraw == null) throw new NoSuchMethodException("TrafficSignalKt state/draw");
        signalState.setAccessible(true);
        signalDraw.setAccessible(true);
        Class<?> unitClass = cl.loadClass("kotlin.Unit");
        for (Field f : unitClass.getDeclaredFields()) {
            if (Modifier.isStatic(f.getModifiers()) && f.getType() == unitClass) {
                f.setAccessible(true);
                unit = f.get(null);
            }
        }
    }

    private void attach(Activity activity, ClassLoader cl) throws Exception {
        detach();
        Class<?> composeView = cl.loadClass(COMPOSE_VIEW);
        View v = null;
        for (Constructor<?> c : composeView.getConstructors()) {
            Class<?>[] p = c.getParameterTypes();
            if (p.length == 1 && p[0] == android.content.Context.class) {
                v = (View) c.newInstance(activity);
                break;
            }
        }
        if (v == null) {
            for (Constructor<?> c : composeView.getDeclaredConstructors()) {
                Class<?>[] p = c.getParameterTypes();
                // Kotlin default-args constructor: (Context, AttributeSet, int, int mask, DefaultConstructorMarker)
                if (p.length == 5 && p[0] == android.content.Context.class && p[2] == int.class && p[3] == int.class) {
                    c.setAccessible(true);
                    v = (View) c.newInstance(activity, null, 0, 6, null);
                    break;
                }
            }
        }
        if (v == null) throw new NoSuchMethodException("ComposeView(Context)");
        int w = activity.getResources().getDisplayMetrics().widthPixels;
        // Wrap content: Android Auto gives the signal no width either; the full screen width let the
        // pill stretch long and thin (2026-11-24).
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        v.setLayoutParams(lp);
        // Outside the window: composed and laid out with the activity, never drawn on the phone or
        // touched; NaverViewImage draws it into its own bitmap.
        v.setTranslationX(-2f * w - 100f);
        v.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
        v.setClickable(false);
        v.setFocusable(false);
        ((ViewGroup) activity.getWindow().getDecorView()).addView(v);
        view = v;
        host = activity;
    }

    private void detach() {
        View v = view;
        view = null;
        host = null;
        if (v != null && v.getParent() instanceof ViewGroup) {
            try {
                ((ViewGroup) v.getParent()).removeView(v);
            } catch (Throwable ignored) {
                // gone with its window
            }
        }
    }

    private final android.os.Handler main = new android.os.Handler(android.os.Looper.getMainLooper());
    private int blanks;

    private void fail(String where, Throwable error) {
        if (failed) return;
        failed = true;
        NaverHudLog.ex(where + " (back to the module signal image)", error);
        // not from inside a composition: remove the view on the next loop
        main.post(() -> {
            try {
                detach();
            } catch (Throwable ignored) {
                // nothing more
            }
        });
    }

    /**
     * Main thread, ~1.5 s after a lane item: did the view draw anything? Three blank checks in a row
     * with lanes to show (e.g. Compose refusing a software canvas) give up on this path.
     */
    void verify(boolean lanesShown) {
        View v = view;
        if (failed || v == null || !lanesShown) {
            blanks = 0;
            return;
        }
        boolean drawn = false;
        try {
            int w = v.getWidth(), h = v.getHeight();
            if (w > 0 && h > 0) {
                float s = Math.min(1f, 360f / w);
                android.graphics.Bitmap b = android.graphics.Bitmap.createBitmap(Math.max(1, (int) (w * s)),
                        Math.max(1, (int) (h * s)), android.graphics.Bitmap.Config.ARGB_8888);
                android.graphics.Canvas c = new android.graphics.Canvas(b);
                c.scale(s, s);
                v.draw(c);
                int[] px = new int[b.getWidth() * b.getHeight()];
                b.getPixels(px, 0, b.getWidth(), 0, 0, b.getWidth(), b.getHeight());
                for (int p : px) {
                    if ((p >>> 24) > 16) {
                        drawn = true;
                        break;
                    }
                }
                b.recycle();
            }
        } catch (Throwable error) {
            fail("signal compose check", error);
            return;
        }
        blanks = drawn ? 0 : blanks + 1;
        if (!drawn) NaverHudLog.line("signal compose: blank view " + v.getWidth() + "x" + v.getHeight() + " (" + blanks + ")");
        if (blanks >= 3) fail("signal compose", new IllegalStateException("view stays blank"));
    }

    /** The composable content: Naver's lane control with this state, phone arguments. */
    private final class Content implements InvocationHandler {
        private final Object state;

        Content(Object state) {
            this.state = state;
        }

        @Override public Object invoke(Object proxy, Method method, Object[] args) {
            String name = method.getName();
            if ("invoke".equals(name) && args != null && args.length == 2) {
                if (!failed) {
                    try {
                        signalDraw.invoke(null, state, false, null, null, args[0], 8, 4);
                    } catch (Throwable error) {
                        fail("signal compose draw", error);
                    }
                }
                return unit;
            }
            if ("equals".equals(name)) return proxy == (args == null ? null : args[0]);
            if ("hashCode".equals(name)) return System.identityHashCode(proxy);
            if ("toString".equals(name)) return "NaverSignalCompose.Content";
            return null;
        }
    }
}
