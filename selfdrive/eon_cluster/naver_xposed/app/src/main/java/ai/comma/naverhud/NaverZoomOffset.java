package ai.comma.naverhud;

import android.graphics.PointF;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Map;
import java.util.WeakHashMap;

import de.robv.android.xposed.XC_MethodHook;
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
 * 넓게 보인다. 클래스·메서드 이름은 6.10.0.16 기준이라 검증 버전에서만 설치한다.
 */
final class NaverZoomOffset {
    /** 네비모드 축소 비율: 지물·경로선·내 차 마커 모두 이만큼(30% 작게). */
    static final float WIDE_SCALE = 0.7f;
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
    private static boolean failureLogged;

    private NaverZoomOffset() { }

    static void install(ClassLoader cl) throws Exception {
        Class<?> nativeView = cl.loadClass("com.naver.maps.map.NativeMapView");
        Class<?> latLng = cl.loadClass("com.naver.maps.geometry.LatLng");
        Class<?> animation = cl.loadClass("com.naver.maps.map.CameraAnimation");
        Class<?> position = cl.loadClass("com.naver.maps.map.CameraPosition");
        Class<?> naverMap = cl.loadClass("com.naver.maps.map.NaverMap");
        positionCtor = position.getConstructor(latLng, double.class, double.class, double.class);
        getCamera = naverMap.getMethod("M");
        setCamera = naverMap.getMethod("C1", position);
        if (getCamera.getReturnType() != position) {
            throw new NoSuchMethodException("NaverMap.M is not getCameraPosition");
        }
        for (Field field : naverMap.getDeclaredFields()) {
            if (field.getType() == nativeView) {
                field.setAccessible(true);
                nativeField = field;
                break;
            }
        }
        if (nativeField == null) {
            throw new NoSuchFieldException("NaverMap NativeMapView field");
        }
        XposedHelpers.findAndHookMethod(nativeView, "h0", latLng, double.class, double.class,
                double.class, PointF.class, int.class, animation, long.class, boolean.class,
                new XC_MethodHook() {
                    @Override protected void beforeHookedMethod(MethodHookParam param) {
                        double levels = offset;
                        if (levels == 0d || param.thisObject != target) return;
                        double zoom = (Double) param.args[1];
                        if (!Double.isNaN(zoom)) {
                            param.args[1] = Math.max(0d, zoom - levels);
                        }
                    }
                });
        XposedHelpers.findAndHookMethod(nativeView, "p", new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam param) {
                double levels = offset;
                Object result = param.getResult();
                if (levels == 0d || result == null || param.thisObject != target) return;
                try {
                    double zoom = result.getClass().getField("zoom").getDouble(result);
                    if (Double.isNaN(zoom)) return;   // CameraPosition.INVALID
                    Object latLngValue = result.getClass().getField("target").get(result);
                    double tilt = result.getClass().getField("tilt").getDouble(result);
                    double bearing = result.getClass().getField("bearing").getDouble(result);
                    param.setResult(positionCtor.newInstance(latLngValue, zoom + levels, tilt, bearing));
                } catch (Throwable error) {
                    logOnce("read", error);
                }
            }
        });
        // 글자·지명·POI 아이콘(심볼 배율): NativeMapView.a1(setSymbolScale) 에 WIDE_SCALE 을 곱하고
        // V(getSymbolScale) 에서는 나눠 돌려준다. 앱(글자 크기 설정)은 원래 값만 본다.
        XposedHelpers.findAndHookMethod(nativeView, "a1", float.class, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam param) {
                if (offset == 0d || param.thisObject != target) return;
                param.args[0] = (Float) param.args[0] * WIDE_SCALE;
            }
        });
        XposedHelpers.findAndHookMethod(nativeView, "V", new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam param) {
                Object result = param.getResult();
                if (offset == 0d || param.thisObject != target || !(result instanceof Float)) return;
                float value = (Float) result;
                if (value > 0f) param.setResult(value / WIDE_SCALE);
            }
        });
        getSymbolScale = nativeView.getMethod("V");
        setSymbolScale = nativeView.getMethod("a1", float.class);
        // 경로선 굵기: 앱이 넣는 값(픽셀)을 기억해 두고, 네비모드에서는 WIDE_SCALE 을 곱한다.
        for (String name : PATH_CLASSES) {
            Class<?> path = cl.loadClass(name);
            XposedHelpers.findAndHookMethod(path, "setWidth", int.class, pathWidthHook(0));
            XposedHelpers.findAndHookMethod(path, "setOutlineWidth", int.class, pathWidthHook(1));
        }
        installed = true;
        NaverHudLog.line("HUD map zoom offset hook ready (" + String.format(java.util.Locale.US,
                "%.2f", WIDE_LEVELS) + " levels in navi layout)");
    }

    /** 지금 경로선·마커에 곱할 비율. 네비모드면 WIDE_SCALE, 아니면 1. */
    static float scale() {
        return offset != 0d ? WIDE_SCALE : 1f;
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
            double wanted = EonDiscovery.wideMap ? WIDE_LEVELS : 0d;
            if (nativeView == target && wanted == offset) return;
            Object seen = getCamera.invoke(map);      // 이전 OFFSET 기준, 앱이 보던 카메라
            Object symbols = getSymbolScale.invoke(nativeView);   // 앱이 보던 심볼 배율
            boolean modeChanged = wanted != offset;
            target = nativeView;
            offset = wanted;
            if (symbols instanceof Float && (Float) symbols > 0f) {
                setSymbolScale.invoke(nativeView, symbols);   // 새 비율로 다시 적용(훅이 곱함)
            }
            if (modeChanged) reapplyPathWidths();
            if (seen != null) {
                double zoom = seen.getClass().getField("zoom").getDouble(seen);
                if (!Double.isNaN(zoom)) setCamera.invoke(map, seen);
            }
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
