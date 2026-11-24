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
 * Naver 6.12: the HUD lane strip drawn by Naver's own lane composable, exactly like 6.11's
 * off-screen NaviLaneControlView (2026-11-24, from the 6.12.2.4 APK).
 *
 * 6.12 has no lane View any more: the phone's route-guidance screen is one Compose tree. But
 * Android Auto's LaneControl renders the very same composable on its own:
 *   LaneControlKt.p(LaneUiState(item, visible, version, z), modifier, isAuto, composer, 8, mask)
 * So the module hosts that composable in its own ComposeView (attached to Naver's window so Compose
 * gets the activity's lifecycle, moved outside the screen so it is never seen or touched) and
 * NaverViewImage draws that view to a bitmap every 0.5 s, as it did with the 6.11 view. Pixels are
 * Naver's: arrows, colours, active lanes and the distance text.
 *
 * Call shape from the phone screen (NaviRouteGuidanceScreenKt): p(state, null, false, composer,
 * LaneUiState.e, 6); LaneUiState(NaviLaneItem, boolean, int, boolean) as in LaneStateHolder.q().
 * Anything missing or throwing disables this path for the session and the screen capture is used.
 */
final class NaverLaneCompose {
    private static final String COMPOSE_VIEW = "androidx.compose.ui.platform.ComposeView";
    private static final String LANE_STATE = "com.naver.map.core.navigation.compose.LaneUiState";
    private static final String LANE_CONTROL = "com.naver.map.core.navigation.compose.LaneControlKt";
    private static final String NAVI_LANE_ITEM = "com.naver.map.core.navigation.lane.NaviLaneItem";

    private boolean failed, logged;
    private View view;
    private Activity host;
    private Method setContent, laneControl;
    private Constructor<?> stateCtor;
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

    /** Main thread: show this lane item (null: nothing). Returns false when unusable. */
    boolean update(Activity activity, Object item) {
        if (failed || activity == null) return false;
        try {
            ClassLoader cl = activity.getClassLoader();
            if (item != null) cl = item.getClass().getClassLoader();
            if (laneControl == null) resolve(cl);
            if (view == null || host != activity) attach(activity, cl);
            final Object state = stateCtor.newInstance(item, true, version++, true);
            Object content = Proxy.newProxyInstance(cl, new Class<?>[]{function2}, new Content(state));
            setContent.invoke(view, content);
            if (!logged) {
                logged = true;
                NaverHudLog.line("lane compose: drawing Naver's LaneControlKt off screen (6.12)");
            }
            return true;
        } catch (Throwable error) {
            fail("lane compose", error);
            return false;
        }
    }

    private void resolve(ClassLoader cl) throws Exception {
        Class<?> composeView = cl.loadClass(COMPOSE_VIEW);
        Class<?> stateClass = cl.loadClass(LANE_STATE);
        Class<?> itemClass = cl.loadClass(NAVI_LANE_ITEM);
        function2 = cl.loadClass("kotlin.jvm.functions.Function2");
        setContent = composeView.getMethod("setContent", function2);
        stateCtor = stateClass.getConstructor(itemClass, boolean.class, int.class, boolean.class);
        for (Method m : cl.loadClass(LANE_CONTROL).getDeclaredMethods()) {
            Class<?>[] p = m.getParameterTypes();
            if (Modifier.isStatic(m.getModifiers()) && p.length == 6 && p[0] == stateClass
                    && p[2] == boolean.class && p[4] == int.class && p[5] == int.class) {
                laneControl = m;
            }
        }
        if (laneControl == null) throw new NoSuchMethodException("LaneControlKt(LaneUiState, Modifier, boolean, Composer, int, int)");
        laneControl.setAccessible(true);
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
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(w, ViewGroup.LayoutParams.WRAP_CONTENT);
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
        NaverHudLog.ex(where + " (back to screen capture)", error);
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
            fail("lane compose check", error);
            return;
        }
        blanks = drawn ? 0 : blanks + 1;
        if (!drawn) NaverHudLog.line("lane compose: blank view " + v.getWidth() + "x" + v.getHeight() + " (" + blanks + ")");
        if (blanks >= 3) fail("lane compose", new IllegalStateException("view stays blank"));
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
                        laneControl.invoke(null, state, null, false, args[0], 8, 6);
                    } catch (Throwable error) {
                        fail("lane compose draw", error);
                    }
                }
                return unit;
            }
            if ("equals".equals(name)) return proxy == (args == null ? null : args[0]);
            if ("hashCode".equals(name)) return System.identityHashCode(proxy);
            if ("toString".equals(name)) return "NaverLaneCompose.Content";
            return null;
        }
    }
}
