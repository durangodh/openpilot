package ai.comma.naverhud;

import android.graphics.PointF;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Map;
import java.util.WeakHashMap;

import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.XposedHelpers;

/**
 * S9 HUD 네비모드에서 HUD 로 찍는 네이버 지도를 티맵처럼 전부 30% 작게 그린다:
 * 지도 범위(줌), 글자·지명(심볼 배율), 경로선 굵기, 내 차 아이콘(NaverMarkerSize).
 *
 * 예전 방식(e79d704)은 앱 카메라를 직접 0.7단계 빼서 다시 설정했다. 네이버 안내가 줌을 계속
 * 다시 맞추므로 서로 줌을 고치다 지도가 깜빡였다. 여기서는 SDK 의 모든 카메라 이동이 지나가는
 * NativeMapView.h0(moveCamera) 에서 실제로 그릴 줌만 OFFSET 만큼 낮추고, 카메라를 읽는
 * NativeMapView.p(getCameraPosition) 에서는 그만큼 다시 더해 돌려준다. 앱(안내·제스처)은 늘
 * 원래 줌을 보고 원래 줌을 요청하므로 되먹임이 없다. 같은 NaverMap 을 쓰는 폰 화면도 같이
 * 넓게 보인다. 메서드 이름은 6.10.0.16 기준이고, 다른 버전에서는 모양으로 찾는다(install).
 */
final class NaverZoomOffset {
    /** 네비모드 축소 비율: 지물·경로선·내 차 마커 모두 이만큼(30% 작게). */
    static final float WIDE_SCALE = 0.7f;
    // 실제 비율은 EON 설정(EonClusterHudMapScaleNaver, 기본 55%: 다른 앱보다 20% 더 작게).
    /**
     * 네비모드에서 실제로 쓰는 비율. 세로 폰 화면은 HUD 네비 칸(1536)에 맞추느라 주행모드보다
     * 약 2배 크게 늘어나므로(NaverMapCapture.reportCaptureRatio) 그만큼 더 줄여 주행모드 대비
     * WIDE_SCALE 이 되게 한다.
     */
    private static volatile float wideScale = WIDE_SCALE;
    /** 지금 훅에 적용 중인 비율(offset 과 함께 바뀐다). */
    private static volatile float appliedScale = 1f;
    /** log2(1/0.7): 지물이 30% 작게 보이는 줌 단계. */
    static final double WIDE_LEVELS = Math.log(1d / WIDE_SCALE) / Math.log(2d);
    private static final String[] PATH_CLASSES = {
            "com.naver.maps.map.overlay.PathOverlay",
            "com.naver.maps.map.overlay.MultipartPathOverlay",
            "com.naver.maps.map.overlay.ArrowheadPathOverlay"};
    /** 경로선마다 앱이 요청한 원래 굵기 {width, outline}. 모드가 바뀌면 다시 넣어 비율을 바꾼다. */
    private static final Map<Object, int[]> pathWidths =
            Collections.synchronizedMap(new WeakHashMap<>());

    private static volatile Object target;        // 줌을 바꿀 NativeMapView(HUD 로 찍는 지도)
    private static volatile double offset;        // 지금 적용 중인 단계(0 = 끔)
    private static volatile boolean installed;
    private static Constructor<?> positionCtor;
    private static Field nativeField;
    private static Method getCamera;
    private static Method setCamera;
    private static Method getSymbolScale;
    private static Method setSymbolScale;
    private static Method destroyedCheck;    // NativeMapView 해제 확인(c0)
    private static boolean failureLogged;
    /** 이 스레드의 moveCamera 를 래퍼(h0) 훅이 이미 보정했으면 네이티브 훅은 건너뛴다. */
    private static final ThreadLocal<Boolean> MOVE_ADJUSTED = new ThreadLocal<>();
    /** 네이티브 getCameraPosition 훅이 이미 보정했으면 래퍼(p) 훅은 건너뛴다. */
    private static final ThreadLocal<Boolean> READ_ADJUSTED = new ThreadLocal<>();

    private NaverZoomOffset() { }

    /**
     * verified=false(확인하지 않은 네이버 버전): 난독화 이름(h0/p/M/C1)이 바뀌어도 되도록
     * 메서드를 이름이 아니라 모양(반환·인자 타입)으로 찾는다. 6.10.0.16 에서 각 모양은
     * 클래스 안에 하나뿐이었다. 둘 이상이거나 없으면 설치하지 않는다(엉뚱한 메서드를 후킹하지
     * 않게). 글자 배율은 이름이 그대로인 JNI 메서드로 건다(verified 와 무관).
     */
    static void install(ClassLoader cl, boolean verified) throws Exception {
        Class<?> nativeView = cl.loadClass("com.naver.maps.map.NativeMapView");
        Class<?> latLng = cl.loadClass("com.naver.maps.geometry.LatLng");
        Class<?> animation = cl.loadClass("com.naver.maps.map.CameraAnimation");
        Class<?> position = cl.loadClass("com.naver.maps.map.CameraPosition");
        Class<?> naverMap = cl.loadClass("com.naver.maps.map.NaverMap");
        positionCtor = position.getConstructor(latLng, double.class, double.class, double.class);
        // NaverMap.getCameraPosition / setCameraPosition, NativeMapView.moveCamera /
        // getCameraPosition (6.10.0.16: M, C1, h0, p).
        getCamera = byShape(naverMap, position);
        setCamera = byShape(naverMap, void.class, position);
        Method moveCamera = byShape(nativeView, void.class, latLng, double.class, double.class,
                double.class, PointF.class, int.class, animation, long.class, boolean.class);
        Method readCamera = byShape(nativeView, position);
        for (Field field : naverMap.getDeclaredFields()) {
            if (field.getType() == nativeView) {
                if (nativeField != null) throw new NoSuchFieldException("two NativeMapView fields");
                field.setAccessible(true);
                nativeField = field;
            }
        }
        if (nativeField == null) {
            throw new NoSuchFieldException("NaverMap NativeMapView field");
        }
        NaverHudLog.line("HUD map zoom methods: " + getCamera.getName() + "/" + setCamera.getName()
                + "/" + moveCamera.getName() + "/" + readCamera.getName());
        // 2026-10-14 실차(6.11.1.2): h0/p 훅은 설치되고 "zoom offset on" 까지 찍혔는데 지도는
        // 그대로였다. h0/p 는 네이티브 호출 한 줄짜리라 ART 가 호출하는 쪽(Transform)에 끼워 넣어
        // (인라인) 컴파일하면 훅을 건너뛴다. 인라인될 수 없는 JNI 메서드(nativeMoveCamera,
        // nativeGetCameraPosition; 이름은 난독화되지 않는다)에도 같은 보정을 건다. 둘 다 걸려도
        // 한 번만 적용되게 스레드별 표시로 겹침을 막는다.
        XposedBridge.hookMethod(moveCamera,
                new XC_MethodHook() {
                    @Override protected void beforeHookedMethod(MethodHookParam param) {
                        double levels = offset;
                        if (levels == 0d || param.thisObject != target) return;
                        double zoom = (Double) param.args[1];
                        if (!Double.isNaN(zoom)) {
                            param.args[1] = Math.max(0d, zoom - levels);
                            MOVE_ADJUSTED.set(Boolean.TRUE);
                        }
                    }

                    @Override protected void afterHookedMethod(MethodHookParam param) {
                        MOVE_ADJUSTED.remove();
                    }
                });
        XposedBridge.hookMethod(readCamera, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam param) {
                READ_ADJUSTED.remove();
            }

            @Override protected void afterHookedMethod(MethodHookParam param) {
                boolean done = READ_ADJUSTED.get() != null;
                READ_ADJUSTED.remove();
                if (!done) addOffset(param);
            }
        });
        String nativeHooks = "none";
        try {
            Method nativeMove = nativeView.getDeclaredMethod("nativeMoveCamera", double.class,
                    double.class, double.class, double.class, double.class, double.class,
                    double.class, int.class, int.class, long.class, boolean.class);
            Method nativeRead = nativeView.getDeclaredMethod("nativeGetCameraPosition");
            if (nativeRead.getReturnType() != position) {
                throw new NoSuchMethodException("nativeGetCameraPosition return type");
            }
            XposedBridge.hookMethod(nativeMove, new XC_MethodHook() {
                @Override protected void beforeHookedMethod(MethodHookParam param) {
                    double levels = offset;
                    if (levels == 0d || param.thisObject != target
                            || MOVE_ADJUSTED.get() != null) return;
                    double zoom = (Double) param.args[2];
                    if (!Double.isNaN(zoom)) param.args[2] = Math.max(0d, zoom - levels);
                }
            });
            XposedBridge.hookMethod(nativeRead, new XC_MethodHook() {
                @Override protected void afterHookedMethod(MethodHookParam param) {
                    if (addOffset(param)) READ_ADJUSTED.set(Boolean.TRUE);
                }
            });
            nativeHooks = "camera";
        } catch (Throwable error) {
            NaverHudLog.ex("HUD native camera hook unavailable", error);
        }
        // Camera hooks are sufficient for zoom. Optional symbol/overlay hooks
        // must not leave update() disabled if one SDK method is unavailable.
        installed = true;
        // 글자·지명·POI 아이콘(심볼 배율): setSymbolScale 에 비율을 곱하고 getSymbolScale 에서는
        // 나눠 돌려준다. 앱(글자 크기 설정)은 원래 값만 본다. 이름이 난독화되지 않는 JNI
        // 메서드(nativeSet/GetSymbolScale)를 쓰므로 버전에 상관없다. 짝으로만 건다.
        XC_MethodHook.Unhook symbolWriteHook = null;
        try {
            Method symbolSetter = nativeView.getDeclaredMethod("nativeSetSymbolScale", float.class);
            Method symbolGetter = nativeView.getDeclaredMethod("nativeGetSymbolScale");
            if (symbolGetter.getReturnType() != float.class) {
                throw new NoSuchMethodException("nativeGetSymbolScale return type");
            }
            symbolWriteHook = XposedBridge.hookMethod(symbolSetter, new XC_MethodHook() {
                @Override protected void beforeHookedMethod(MethodHookParam param) {
                    if (offset == 0d || param.thisObject != target) return;
                    param.args[0] = (Float) param.args[0] * appliedScale;
                }
            });
            XposedBridge.hookMethod(symbolGetter, new XC_MethodHook() {
                @Override protected void afterHookedMethod(MethodHookParam param) {
                    Object result = param.getResult();
                    if (offset == 0d || param.thisObject != target || !(result instanceof Float)) return;
                    float value = (Float) result;
                    if (value > 0f) param.setResult(value / appliedScale);
                }
            });
            symbolGetter.setAccessible(true);
            symbolSetter.setAccessible(true);
            // 지도가 해제된 뒤 JNI 를 직접 부르지 않도록, NativeMapView 의 해제 확인
            // (6.10.0.16: c0(String), 해제됐으면 true)을 모양으로 찾아 먼저 부른다.
            destroyedCheck = byShape(nativeView, boolean.class, String.class);
            getSymbolScale = symbolGetter;
            setSymbolScale = symbolSetter;
            nativeHooks += "+symbol";
        } catch (Throwable error) {
            // A setter without its matching getter would repeatedly shrink the
            // application's own symbol value. Keep this pair all-or-nothing.
            if (symbolWriteHook != null) symbolWriteHook.unhook();
            getSymbolScale = setSymbolScale = null;
            NaverHudLog.ex("HUD symbol scale hook unavailable (zoom remains enabled)", error);
        }
        NaverHudLog.line("HUD map native hooks: " + nativeHooks);
        // 경로선 굵기: 앱이 넣는 값(픽셀)을 기억해 두고, 네비모드에서는 WIDE_SCALE 을 곱한다.
        for (String name : PATH_CLASSES) {
            try {
                Class<?> path = cl.loadClass(name);
                for (int slot = 0; slot < 2; slot++) {
                    String method = slot == 0 ? "setWidth" : "setOutlineWidth";
                    try {
                        XposedHelpers.findAndHookMethod(path, method, int.class, pathWidthHook(slot));
                    } catch (Throwable error) {
                        NaverHudLog.ex("HUD path hook unavailable " + name + "." + method, error);
                    }
                }
            } catch (Throwable error) {
                NaverHudLog.ex("HUD path class unavailable " + name, error);
            }
        }
        NaverHudLog.line("HUD map zoom offset hook ready (" + String.format(java.util.Locale.US,
                "%.2f", WIDE_LEVELS) + " levels in navi layout)");
    }

    /** 읽은 카메라 줌에 offset 을 더해 앱에는 원래 줌으로 보이게 한다. 바꿨으면 true. */
    private static boolean addOffset(XC_MethodHook.MethodHookParam param) {
        double levels = offset;
        Object result = param.getResult();
        if (levels == 0d || result == null || param.thisObject != target) return false;
        try {
            double zoom = result.getClass().getField("zoom").getDouble(result);
            if (Double.isNaN(zoom)) return false;   // CameraPosition.INVALID
            Object latLngValue = result.getClass().getField("target").get(result);
            double tilt = result.getClass().getField("tilt").getDouble(result);
            double bearing = result.getClass().getField("bearing").getDouble(result);
            param.setResult(positionCtor.newInstance(latLngValue, zoom + levels, tilt, bearing));
            return true;
        } catch (Throwable error) {
            logOnce("read", error);
            return false;
        }
    }

    /** 공개·비정적 메서드 중 반환·인자 타입이 정확히 같은 것 하나. 없거나 여럿이면 예외. */
    private static Method byShape(Class<?> owner, Class<?> returns, Class<?>... params)
            throws NoSuchMethodException {
        Method found = null;
        for (Method m : owner.getDeclaredMethods()) {
            int mod = m.getModifiers();
            if (!Modifier.isPublic(mod) || Modifier.isStatic(mod) || m.isSynthetic()
                    || m.getReturnType() != returns
                    || !Arrays.equals(m.getParameterTypes(), params)) continue;
            if (found != null) {
                throw new NoSuchMethodException(owner.getSimpleName() + " has several "
                        + returns.getSimpleName() + Arrays.toString(params) + ": "
                        + found.getName() + ", " + m.getName());
            }
            found = m;
        }
        if (found == null) {
            throw new NoSuchMethodException(owner.getSimpleName() + " has no "
                    + returns.getSimpleName() + Arrays.toString(params));
        }
        found.setAccessible(true);
        return found;
    }

    /** 지금 경로선·마커에 곱할 비율. 네비모드면 WIDE_SCALE, 아니면 1. */
    static float scale() {
        return offset != 0d ? appliedScale : 1f;
    }

    /**
     * 캡처 쪽에서: 네비모드 HUD 에 지도가 주행모드보다 몇 배 크게 그려지는지(ratio).
     * 0.05 단위로 맞춰 자잘한 변화로 지도를 다시 그리지 않는다.
     */
    static void reportCaptureRatio(float ratio) {
        if (!(ratio > 0f)) return;
        float wanted = Math.max(0.2f, Math.min(1f, EonDiscovery.mapScale / ratio));
        wanted = Math.round(wanted * 20f) / 20f;
        if (Math.abs(wanted - wideScale) >= 0.049f) {
            wideScale = wanted;
            NaverHudLog.line("HUD map scale " + wanted + " (capture ratio "
                    + String.format(java.util.Locale.US, "%.2f", ratio) + ")");
        }
    }

    private static XC_MethodHook pathWidthHook(final int slot) {
        return new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam param) {
                int requested = (Integer) param.args[0];
                int[] widths = pathWidths.get(param.thisObject);
                if (widths == null) {
                    widths = new int[]{-1, -1};
                    pathWidths.put(param.thisObject, widths);
                }
                widths[slot] = requested;
                float k = scale();
                if (k != 1f && requested > 0) {
                    param.args[0] = Math.max(1, Math.round(requested * k));
                }
            }
        };
    }

    /** 모드가 바뀐 뒤: 기억한 원래 굵기를 다시 넣어 새 비율로 그리게 한다(메인 스레드). */
    private static void reapplyPathWidths() {
        ArrayList<Map.Entry<Object, int[]>> entries;
        synchronized (pathWidths) {
            entries = new ArrayList<>(pathWidths.entrySet());
        }
        for (Map.Entry<Object, int[]> entry : entries) {
            try {
                int[] widths = entry.getValue();
                if (widths[0] >= 0) XposedHelpers.callMethod(entry.getKey(), "setWidth", widths[0]);
                if (widths[1] >= 0) XposedHelpers.callMethod(entry.getKey(), "setOutlineWidth", widths[1]);
            } catch (Throwable ignored) {
                // 지워진 경로선은 건너뛴다.
            }
        }
    }

    /**
     * 메인 스레드(캡처 틱). HUD 모드나 찍는 지도가 바뀌면, 앱이 보던 카메라를 그대로 다시
     * 설정해 새 OFFSET 으로 한 번 다시 그리게 한다(이후에는 앱이 움직일 때마다 자동 적용).
     */
    static void update(Object map) {
        if (!installed || map == null) return;
        try {
            Object nativeView = nativeField.get(map);
            float k = wideScale;
            // HUD 전용 지도(NaverHudMap)가 돌면 폰 지도는 줄이지 않는다.
            double wanted = EonDiscovery.wideMap && !NaverHudMap.active
                    ? Math.log(1d / k) / Math.log(2d) : 0d;
            if (nativeView == target && wanted == offset) return;
            Object seen = getCamera.invoke(map);      // 이전 OFFSET 기준, 앱이 보던 카메라
            Object symbols = null;
            boolean symbolsUsable = getSymbolScale != null && setSymbolScale != null
                    && !Boolean.TRUE.equals(destroyedCheck.invoke(nativeView, "setSymbolScale"));
            if (symbolsUsable) {
                try {
                    symbols = getSymbolScale.invoke(nativeView);
                } catch (Throwable error) {
                    logOnce("symbol read", error);
                }
            }
            boolean modeChanged = wanted != offset;
            target = nativeView;
            offset = wanted;
            appliedScale = wanted != 0d ? k : 1f;
            if (seen != null) {
                double zoom = seen.getClass().getField("zoom").getDouble(seen);
                if (!Double.isNaN(zoom)) setCamera.invoke(map, seen);
            }
            if (symbolsUsable && symbols instanceof Float && (Float) symbols > 0f) {
                try {
                    setSymbolScale.invoke(nativeView, symbols);
                } catch (Throwable error) {
                    logOnce("symbol write", error);
                }
            }
            if (modeChanged) reapplyPathWidths();
            NaverHudLog.line("HUD map zoom offset " + (wanted == 0d ? "off" : "on"));
        } catch (Throwable error) {
            logOnce("update", error);
        }
    }

    private static void logOnce(String where, Throwable error) {
        if (failureLogged) return;
        failureLogged = true;
        NaverHudLog.ex("HUD map zoom offset " + where, error);
    }
}
