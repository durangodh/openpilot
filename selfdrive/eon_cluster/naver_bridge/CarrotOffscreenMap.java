package com.naver.map.carrot;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.media.Image;
import android.media.ImageReader;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import android.view.Surface;

import java.lang.reflect.Array;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.lang.reflect.Proxy;
import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * HUD8: TMAP-style offscreen map for the HUD.
 *
 * TMAP's CarrotMapRenderStream creates a second NaviMapEngine and renders it
 * into an ImageReader so the HUD map never depends on what the phone screen is
 * showing.  This class does the same with Naver's own map SDK: a private
 * {@code com.naver.maps.map.MapSurface} (the renderer Naver uses for Android
 * Auto) is bound to an {@link ImageReader}, the camera follows the navigation
 * position/heading, the active route is drawn as a PathOverlay, and every new
 * frame is JPEG-encoded and sent as {@code map_main} through the existing
 * bridge transport.  No Activity, no window, no orientation change, no
 * Android Auto session is required.
 *
 * All SDK access is reflective: the CarrotNaver 6.9.1.3 SDK method names are
 * R8-obfuscated (verified against classes.dex / classes37.dex of the HUD7
 * release).  If anything in the SDK contract fails, {@link #capture} returns
 * {@code false} and the bridge falls back to the HUD7 capture paths.
 */
public final class CarrotOffscreenMap {
    private static final String TAG = "CarrotOffscreenMap";
    static final int WIDTH = 960;
    static final int HEIGHT = 576;
    /** Frame budget: the EON server keeps at most 5 fps; do not encode more. */
    private static final long MIN_FRAME_INTERVAL_MS = 200;
    /** Camera refresh cadence while the bridge polls us every 500 ms. */
    private static final long CAMERA_INTERVAL_MS = 240;
    private static final long ROUTE_REFRESH_MS = 3000;
    /** Restart the renderer when it produced nothing for this long. */
    private static final long STALL_RESTART_MS = 20000;
    private static final int MAX_RESTARTS = 3;

    // ---- SDK class / method names (CarrotNaver 6.9.1.3, R8 mapping) ----
    private static final String CLS_MAP_SURFACE = "com.naver.maps.map.MapSurface";
    private static final String CLS_MAP_OPTIONS = "com.naver.maps.map.NaverMapOptions";
    private static final String CLS_NAVER_MAP = "com.naver.maps.map.NaverMap";
    private static final String CLS_MAP_TYPE = "com.naver.maps.map.NaverMap$MapType";
    private static final String CLS_READY_CB = "com.naver.maps.map.OnMapReadyCallback";
    private static final String CLS_CAMERA_POSITION = "com.naver.maps.map.CameraPosition";
    private static final String CLS_CAMERA_UPDATE = "com.naver.maps.map.CameraUpdate";
    private static final String CLS_CAMERA_ANIM = "com.naver.maps.map.CameraAnimation";
    private static final String CLS_LATLNG = "com.naver.maps.geometry.LatLng";
    private static final String CLS_PATH_OVERLAY = "com.naver.maps.map.overlay.PathOverlay";
    private static final String CLS_LOCATION_OVERLAY = "com.naver.maps.map.overlay.LocationOverlay";
    // MapSurface lifecycle (MapView order): onCreate/onStart/onResume/onPause/onStop/onDestroy
    private static final String M_SURFACE_ON_CREATE = "h";      // (Bundle)
    private static final String M_SURFACE_ON_START = "n";
    private static final String M_SURFACE_ON_RESUME = "l";
    private static final String M_SURFACE_ON_PAUSE = "k";
    private static final String M_SURFACE_ON_STOP = "o";
    private static final String M_SURFACE_ON_DESTROY = "i";
    private static final String M_SURFACE_CREATED = "r";        // (Surface)
    private static final String M_SURFACE_CHANGED = "q";        // (Surface,int,int)
    private static final String M_SURFACE_DESTROYED = "s";
    private static final String M_SURFACE_GET_MAP_ASYNC = "f";  // (OnMapReadyCallback)
    // NaverMap
    private static final String M_OPTIONS_MAP_TYPE = "U0";      // NaverMapOptions.mapType(MapType)
    private static final String M_OPTIONS_SYMBOL_SCALE = "q1";  // NaverMapOptions.symbolScale(float)
    private static final String M_MAP_SET_BUILDING_HEIGHT = "A1"; // (float 0..1)
    private static final String M_MAP_SET_CONTENT_PADDING = "D1"; // (int,int,int,int)
    private static final String M_MAP_SET_NIGHT_MODE = "b2";    // (boolean)
    private static final String M_MAP_SET_FPS_LIMIT = "N1";     // (int) -> NativeMapView.E0 -> MapViewDelegate.A
    private static final String M_MAP_SET_CAMERA_POSITION = "C1"; // (CameraPosition)
    private static final String M_MAP_MOVE_CAMERA = "Y0";       // (CameraUpdate)
    private static final String M_MAP_GET_LOCATION_OVERLAY = "m0";
    private static final String M_CAMERA_UPDATE_TO_POSITION = "x"; // static (CameraPosition)
    private static final String M_CAMERA_UPDATE_ANIMATE = "b";  // (CameraAnimation,long)
    private static final String M_OVERLAY_SET_MAP = "o";        // (NaverMap)

    private static final Object lock = new Object();
    private static volatile boolean failed;
    private static volatile boolean initialized;
    private static volatile boolean mapReady;
    private static volatile CarrotNaverBridge bridge;
    private static Handler main;
    private static Handler worker;
    private static ImageReader reader;
    private static Surface surface;
    private static Object mapSurface;
    private static Object naverMap;
    private static Object pathOverlay;
    private static Object locationOverlay;
    private static Class<?> latLngClass;
    private static Constructor<?> latLngCtor;
    private static Constructor<?> cameraPositionCtor;
    private static Object cameraAnimLinear;
    private static volatile boolean inFlight;
    private static long lastFrameSentAt;
    private static long lastFrameSeenAt;
    private static long lastCameraAt;
    private static long lastRouteAt;
    private static long startedAt;
    private static int restarts;
    private static double lastBearing;
    private static double lastLat;
    private static double lastLon;
    private static Object lastPathSource;
    private static int lastPathSize;
    private static double[] pathCum;      // cumulative metres per route point
    private static double[] pathLat;
    private static double[] pathLon;
    private static int nearestIndexHint;

    private CarrotOffscreenMap() {
    }

    /** True while this renderer owns map_main (bridge skips its own captures). */
    public static boolean active() {
        return initialized && !failed;
    }

    /**
     * Called from CarrotNaverBridge.captureMap() every 500 ms on the bridge thread.
     * Returns true when the offscreen renderer handles map_main.
     */
    public static boolean capture(CarrotNaverBridge b) {
        if (b == null || failed) {
            return false;
        }
        bridge = b;
        ensureStarted();
        if (failed) {
            return false;
        }
        long now = SystemClock.elapsedRealtime();
        if (mapReady) {
            if (now - lastCameraAt >= CAMERA_INTERVAL_MS) {
                lastCameraAt = now;
                main.post(new Runnable() {
                    @Override
                    public void run() {
                        try {
                            updateCamera();
                        } catch (Throwable t) {
                            Log.w(TAG, "camera update failed: " + t);
                        }
                    }
                });
            }
        }
        long reference = lastFrameSeenAt > 0 ? lastFrameSeenAt : startedAt;
        if (reference > 0 && now - reference > STALL_RESTART_MS) {
            if (restarts >= MAX_RESTARTS) {
                Log.w(TAG, "offscreen map stalled repeatedly; falling back to capture");
                failed = true;
                main.post(new Runnable() {
                    @Override
                    public void run() {
                        destroy();
                    }
                });
                return false;
            }
            restarts++;
            Log.w(TAG, "offscreen map stalled; restart #" + restarts);
            main.post(new Runnable() {
                @Override
                public void run() {
                    destroy();
                    mapReady = false;
                    startedAt = SystemClock.elapsedRealtime();
                    lastFrameSeenAt = 0;
                    try {
                        initOnMain();
                    } catch (Throwable t) {
                        Log.e(TAG, "offscreen map restart failed: " + t);
                        failed = true;
                    }
                }
            });
        }
        return true;
    }

    private static void ensureStarted() {
        synchronized (lock) {
            if (initialized || failed) {
                return;
            }
            initialized = true;
            startedAt = SystemClock.elapsedRealtime();
            if (main == null) {
                main = new Handler(Looper.getMainLooper());
            }
            if (worker == null) {
                HandlerThread thread = new HandlerThread("carrot-offscreen-map");
                thread.start();
                worker = new Handler(thread.getLooper());
            }
        }
        main.post(new Runnable() {
            @Override
            public void run() {
                try {
                    initOnMain();
                } catch (Throwable t) {
                    Log.e(TAG, "offscreen map init failed: " + t);
                    failed = true;
                    destroy();
                }
            }
        });
    }

    // ------------------------------------------------------------------ init

    private static void initOnMain() throws Exception {
        Context context = appContext();
        if (context == null) {
            throw new IllegalStateException("no application context");
        }
        ClassLoader loader = CarrotOffscreenMap.class.getClassLoader();
        Class<?> optionsClass = Class.forName(CLS_MAP_OPTIONS, true, loader);
        Class<?> surfaceClass = Class.forName(CLS_MAP_SURFACE, true, loader);
        latLngClass = Class.forName(CLS_LATLNG, true, loader);
        latLngCtor = latLngClass.getConstructor(double.class, double.class);
        Class<?> cameraPositionClass = Class.forName(CLS_CAMERA_POSITION, true, loader);
        cameraPositionCtor = cameraPositionClass.getConstructor(latLngClass, double.class, double.class, double.class);
        Class<?> animClass = Class.forName(CLS_CAMERA_ANIM, true, loader);
        cameraAnimLinear = Enum.valueOf((Class) animClass, "Linear");

        Object options = optionsClass.getConstructor().newInstance();
        // Navi map type is fixed through the options (NaverMap.setMapType would
        // also persist the choice into the SDK preferences of the whole app).
        Class<?> mapTypeClass = Class.forName(CLS_MAP_TYPE, true, loader);
        Object navi = Enum.valueOf((Class) mapTypeClass, "Navi");
        tryInvoke(options, M_OPTIONS_MAP_TYPE, new Class<?>[]{mapTypeClass}, new Object[]{navi});
        tryInvoke(options, M_OPTIONS_SYMBOL_SCALE, new Class<?>[]{float.class}, new Object[]{Float.valueOf(1.25f)});
        Object ms = surfaceClass.getConstructor(Context.class, optionsClass).newInstance(context, options);
        mapSurface = ms;
        invoke(ms, M_SURFACE_ON_CREATE, new Class<?>[]{android.os.Bundle.class}, new Object[]{null});
        invoke(ms, M_SURFACE_ON_START, new Class<?>[0], new Object[0]);
        invoke(ms, M_SURFACE_ON_RESUME, new Class<?>[0], new Object[0]);

        ImageReader r = ImageReader.newInstance(WIDTH, HEIGHT, PixelFormat.RGBA_8888, 3);
        r.setOnImageAvailableListener(new ImageReader.OnImageAvailableListener() {
            @Override
            public void onImageAvailable(ImageReader imageReader) {
                onFrame(imageReader);
            }
        }, worker);
        reader = r;
        surface = r.getSurface();
        invoke(ms, M_SURFACE_CREATED, new Class<?>[]{Surface.class}, new Object[]{surface});
        invoke(ms, M_SURFACE_CHANGED, new Class<?>[]{Surface.class, int.class, int.class},
               new Object[]{surface, Integer.valueOf(WIDTH), Integer.valueOf(HEIGHT)});

        Class<?> readyClass = Class.forName(CLS_READY_CB, true, loader);
        Object callback = Proxy.newProxyInstance(loader, new Class<?>[]{readyClass}, new InvocationHandler() {
            @Override
            public Object invoke(Object proxy, Method method, Object[] args) {
                if (args != null && args.length == 1 && args[0] != null) {
                    try {
                        onMapReady(args[0]);
                    } catch (Throwable t) {
                        Log.e(TAG, "map ready setup failed: " + t);
                        failed = true;
                    }
                    return null;
                }
                if ("hashCode".equals(method.getName())) {
                    return Integer.valueOf(System.identityHashCode(proxy));
                }
                if ("equals".equals(method.getName())) {
                    return Boolean.valueOf(proxy == (args != null && args.length > 0 ? args[0] : null));
                }
                if ("toString".equals(method.getName())) {
                    return "CarrotOffscreenMap.ready";
                }
                return null;
            }
        });
        invoke(ms, M_SURFACE_GET_MAP_ASYNC, new Class<?>[]{readyClass}, new Object[]{callback});
        Log.i(TAG, "offscreen map surface created " + WIDTH + "x" + HEIGHT);
    }

    private static void onMapReady(Object map) throws Exception {
        naverMap = map;
        ClassLoader loader = CarrotOffscreenMap.class.getClassLoader();
        tryInvoke(map, M_MAP_SET_BUILDING_HEIGHT, new Class<?>[]{float.class}, new Object[]{Float.valueOf(0.0f)});
        tryInvoke(map, M_MAP_SET_NIGHT_MODE, new Class<?>[]{boolean.class}, new Object[]{Boolean.FALSE});
        // The HUD consumes 5 fps; cap the GL thread so the S9 does not render at 60.
        tryInvoke(map, M_MAP_SET_FPS_LIMIT, new Class<?>[]{int.class}, new Object[]{Integer.valueOf(12)});
        // Push the camera target down so the vehicle sits in the lower third.
        tryInvoke(map, M_MAP_SET_CONTENT_PADDING, new Class<?>[]{int.class, int.class, int.class, int.class},
                  new Object[]{Integer.valueOf(0), Integer.valueOf(HEIGHT / 2), Integer.valueOf(0), Integer.valueOf(0)});

        locationOverlay = invoke(map, M_MAP_GET_LOCATION_OVERLAY, new Class<?>[0], new Object[0]);
        if (locationOverlay != null) {
            tryInvoke(locationOverlay, "setVisible", new Class<?>[]{boolean.class}, new Object[]{Boolean.TRUE});
            tryInvoke(locationOverlay, "setIconWidth", new Class<?>[]{int.class}, new Object[]{Integer.valueOf(56)});
            tryInvoke(locationOverlay, "setIconHeight", new Class<?>[]{int.class}, new Object[]{Integer.valueOf(56)});
        }
        Class<?> pathClass = Class.forName(CLS_PATH_OVERLAY, true, loader);
        Object path = pathClass.getConstructor().newInstance();
        tryInvoke(path, "setWidth", new Class<?>[]{int.class}, new Object[]{Integer.valueOf(16)});
        tryInvoke(path, "setOutlineWidth", new Class<?>[]{int.class}, new Object[]{Integer.valueOf(3)});
        tryInvoke(path, "setColor", new Class<?>[]{int.class}, new Object[]{Integer.valueOf(0xFF2F72FF)});
        tryInvoke(path, "setOutlineColor", new Class<?>[]{int.class}, new Object[]{Integer.valueOf(0xFF102A66)});
        tryInvoke(path, "setPassedColor", new Class<?>[]{int.class}, new Object[]{Integer.valueOf(0xFF9AA4B2)});
        tryInvoke(path, "setPassedOutlineColor", new Class<?>[]{int.class}, new Object[]{Integer.valueOf(0xFF5C6673)});
        tryInvoke(path, "setHideCollidedSymbols", new Class<?>[]{boolean.class}, new Object[]{Boolean.TRUE});
        tryInvoke(path, "setGlobalZIndex", new Class<?>[]{int.class}, new Object[]{Integer.valueOf(-100000)});
        pathOverlay = path;
        mapReady = true;
        lastRouteAt = -ROUTE_REFRESH_MS;
        updateCamera();
        Log.i(TAG, "offscreen NaverMap ready");
    }

    // ---------------------------------------------------------------- camera

    /** Main thread. Reads the navigation store and moves the private map. */
    private static void updateCamera() throws Exception {
        Object map = naverMap;
        if (map == null) {
            return;
        }
        Object store = storeObject();
        double lat = lastLat;
        double lon = lastLon;
        double heading = lastBearing;
        double speedKph = 0.0;
        boolean havePosition = false;
        if (store != null) {
            Object p = value(call(store, "P"));
            Object location = call(p, "getLocation");
            double la = fieldNumber(location, "latitude");
            double lo = fieldNumber(location, "longitude");
            if (Math.abs(la) > 0.01 && Math.abs(lo) > 0.01) {
                lat = la;
                lon = lo;
                havePosition = true;
            }
            speedKph = number(call(p, "getSpeedKmPerHour"));
            double h = number(call(p, "getHeading"));
            // Below walking speed the heading is noise; keep the last bearing.
            if (speedKph >= 3.0 && Double.isFinite(h)) {
                heading = ((h % 360.0) + 360.0) % 360.0;
            }
        }
        if (!havePosition && (lastLat == 0.0 && lastLon == 0.0)) {
            return;
        }
        lastLat = lat;
        lastLon = lon;
        lastBearing = heading;

        long now = SystemClock.elapsedRealtime();
        if (store != null && now - lastRouteAt >= ROUTE_REFRESH_MS) {
            lastRouteAt = now;
            refreshRoute(store);
        }
        updateRouteProgress(lat, lon);

        Object latLng = latLngCtor.newInstance(Double.valueOf(lat), Double.valueOf(lon));
        if (locationOverlay != null) {
            tryInvoke(locationOverlay, "setPosition", new Class<?>[]{latLngClass}, new Object[]{latLng});
            tryInvoke(locationOverlay, "setBearing", new Class<?>[]{float.class}, new Object[]{Float.valueOf((float) heading)});
        }
        double zoom = zoomForSpeed(speedKph);
        double tilt = 42.0;
        Object cameraPosition = cameraPositionCtor.newInstance(latLng, Double.valueOf(zoom), Double.valueOf(tilt), Double.valueOf(heading));
        Class<?> cuClass = Class.forName(CLS_CAMERA_UPDATE, true, CarrotOffscreenMap.class.getClassLoader());
        Method toPosition = cuClass.getMethod(M_CAMERA_UPDATE_TO_POSITION, cameraPosition.getClass());
        Object update = toPosition.invoke(null, cameraPosition);
        try {
            Method animate = cuClass.getMethod(M_CAMERA_UPDATE_ANIMATE, cameraAnimLinear.getClass(), long.class);
            update = animate.invoke(update, cameraAnimLinear, Long.valueOf(CAMERA_INTERVAL_MS));
        } catch (Throwable ignored) {
            // Non-animated camera moves are still correct.
        }
        try {
            invoke(map, M_MAP_MOVE_CAMERA, new Class<?>[]{cuClass}, new Object[]{update});
        } catch (Throwable t) {
            invoke(map, M_MAP_SET_CAMERA_POSITION, new Class<?>[]{cameraPosition.getClass()}, new Object[]{cameraPosition});
        }
    }

    private static double zoomForSpeed(double speedKph) {
        // TMAP-like: tight in town, wider on the motorway.
        if (speedKph <= 30.0) {
            return 17.0;
        }
        if (speedKph >= 110.0) {
            return 15.2;
        }
        return 17.0 - (speedKph - 30.0) / 80.0 * 1.8;
    }

    // ----------------------------------------------------------------- route

    private static void refreshRoute(Object store) {
        Object map = naverMap;
        Object path = pathOverlay;
        if (map == null || path == null) {
            return;
        }
        List<?> points = Collections.emptyList();
        Object source = null;
        try {
            source = call(call(value(call(store, "K")), "e"), "h");
            points = asList(call(source, "getPathPoints"));
        } catch (Throwable ignored) {
        }
        if (points.isEmpty()) {
            if (pathCum != null) {
                pathCum = null;
                pathLat = null;
                pathLon = null;
                lastPathSource = null;
                lastPathSize = 0;
                tryInvoke(path, M_OVERLAY_SET_MAP, new Class<?>[]{map.getClass()}, new Object[]{null});
            }
            return;
        }
        if (source == lastPathSource && points.size() == lastPathSize && pathCum != null) {
            return;
        }
        int n = points.size();
        // Keep at most ~2000 vertices; Naver PathOverlay copes, but progress math stays cheap.
        int step = Math.max(1, n / 2000);
        int count = 0;
        for (int i = 0; i < n; i += step) {
            count++;
        }
        if (count < 2) {
            return;
        }
        double[] lats = new double[count];
        double[] lons = new double[count];
        double[] cum = new double[count];
        List<Object> coords = new ArrayList<Object>(count);
        int k = 0;
        try {
            for (int i = 0; i < n; i += step) {
                Object pt = points.get(i);
                double la = fieldNumber(pt, "latitude");
                double lo = fieldNumber(pt, "longitude");
                lats[k] = la;
                lons[k] = lo;
                cum[k] = k == 0 ? 0.0 : cum[k - 1] + distanceM(lats[k - 1], lons[k - 1], la, lo);
                coords.add(latLngCtor.newInstance(Double.valueOf(la), Double.valueOf(lo)));
                k++;
            }
            tryInvoke(path, "setCoords", new Class<?>[]{List.class}, new Object[]{coords});
            tryInvoke(path, "setProgress", new Class<?>[]{double.class}, new Object[]{Double.valueOf(0.0)});
            tryInvoke(path, M_OVERLAY_SET_MAP, new Class<?>[]{map.getClass()}, new Object[]{map});
            pathLat = lats;
            pathLon = lons;
            pathCum = cum;
            lastPathSource = source;
            lastPathSize = n;
            nearestIndexHint = 0;
        } catch (Throwable t) {
            Log.w(TAG, "route overlay failed: " + t);
        }
    }

    private static void updateRouteProgress(double lat, double lon) {
        double[] cum = pathCum;
        double[] lats = pathLat;
        double[] lons = pathLon;
        Object path = pathOverlay;
        if (cum == null || lats == null || lons == null || path == null || cum.length < 2) {
            return;
        }
        int best = -1;
        double bestD = Double.MAX_VALUE;
        // Search a window around the previous hint first, then widen if lost.
        int from = Math.max(0, nearestIndexHint - 20);
        int to = Math.min(lats.length, nearestIndexHint + 200);
        for (int pass = 0; pass < 2; pass++) {
            for (int i = from; i < to; i++) {
                double d = distanceM(lat, lon, lats[i], lons[i]);
                if (d < bestD) {
                    bestD = d;
                    best = i;
                }
            }
            if (best >= 0 && bestD < 120.0) {
                break;
            }
            from = 0;
            to = lats.length;
        }
        if (best < 0) {
            return;
        }
        nearestIndexHint = best;
        double total = cum[cum.length - 1];
        double progress = total > 1.0 ? Math.max(0.0, Math.min(1.0, cum[best] / total)) : 0.0;
        tryInvoke(path, "setProgress", new Class<?>[]{double.class}, new Object[]{Double.valueOf(progress)});
    }

    private static double distanceM(double lat1, double lon1, double lat2, double lon2) {
        double x = Math.toRadians(lon2 - lon1) * Math.cos(Math.toRadians((lat1 + lat2) * 0.5));
        double y = Math.toRadians(lat2 - lat1);
        return Math.sqrt(x * x + y * y) * 6371000.0;
    }

    // ---------------------------------------------------------------- frames

    /** Worker thread: ImageReader callback. */
    private static void onFrame(ImageReader r) {
        Image image = null;
        try {
            image = r.acquireLatestImage();
            if (image == null) {
                return;
            }
            long now = SystemClock.elapsedRealtime();
            lastFrameSeenAt = now;
            CarrotNaverBridge b = bridge;
            if (b == null || inFlight || now - lastFrameSentAt < MIN_FRAME_INTERVAL_MS) {
                return;
            }
            inFlight = true;
            Bitmap bitmap = toBitmap(image);
            image.close();
            image = null;
            if (bitmap == null) {
                return;
            }
            lastFrameSentAt = now;
            // sendBitmap JPEG-encodes at NaverHudSettings.quality() and recycles the bitmap.
            b.sendBitmap(bitmap);
        } catch (Throwable t) {
            Log.w(TAG, "frame failed: " + t);
        } finally {
            inFlight = false;
            if (image != null) {
                try {
                    image.close();
                } catch (Throwable ignored) {
                }
            }
        }
    }

    private static Bitmap toBitmap(Image image) {
        Image.Plane[] planes = image.getPlanes();
        if (planes == null || planes.length == 0) {
            return null;
        }
        Image.Plane plane = planes[0];
        ByteBuffer buffer = plane.getBuffer();
        int pixelStride = plane.getPixelStride();
        int rowStride = plane.getRowStride();
        int width = image.getWidth();
        int height = image.getHeight();
        if (pixelStride <= 0 || rowStride <= 0 || width <= 0 || height <= 0) {
            return null;
        }
        int rowPadding = (rowStride - pixelStride * width) / pixelStride;
        Bitmap padded = Bitmap.createBitmap(width + rowPadding, height, Bitmap.Config.ARGB_8888);
        buffer.rewind();
        padded.copyPixelsFromBuffer(buffer);
        if (rowPadding == 0) {
            return padded;
        }
        Bitmap cropped = Bitmap.createBitmap(padded, 0, 0, width, height);
        padded.recycle();
        return cropped;
    }

    // -------------------------------------------------------------- teardown

    /** Main thread. */
    private static void destroy() {
        Object ms = mapSurface;
        mapSurface = null;
        naverMap = null;
        pathOverlay = null;
        locationOverlay = null;
        mapReady = false;
        if (ms != null) {
            tryInvoke(ms, M_SURFACE_DESTROYED, new Class<?>[0], new Object[0]);
            tryInvoke(ms, M_SURFACE_ON_PAUSE, new Class<?>[0], new Object[0]);
            tryInvoke(ms, M_SURFACE_ON_STOP, new Class<?>[0], new Object[0]);
            tryInvoke(ms, M_SURFACE_ON_DESTROY, new Class<?>[0], new Object[0]);
        }
        ImageReader r = reader;
        reader = null;
        surface = null;
        if (r != null) {
            try {
                r.close();
            } catch (Throwable ignored) {
            }
        }
    }

    // ------------------------------------------------------------ reflection

    private static Context appContext() {
        try {
            Class<?> at = Class.forName("android.app.ActivityThread");
            Object app = at.getMethod("currentApplication").invoke(null);
            if (app instanceof Context) {
                return ((Context) app).getApplicationContext();
            }
        } catch (Throwable ignored) {
        }
        return null;
    }

    private static Object storeObject() {
        try {
            Field f = CarrotNaverBridge.class.getDeclaredField("store");
            f.setAccessible(true);
            return f.get(null);
        } catch (Throwable t) {
            return null;
        }
    }

    private static Object invoke(Object target, String name, Class<?>[] types, Object[] args) throws Exception {
        Method m = findMethod(target.getClass(), name, types);
        if (m == null) {
            throw new NoSuchMethodException(target.getClass().getName() + "." + name);
        }
        m.setAccessible(true);
        return m.invoke(target, args);
    }

    private static Object tryInvoke(Object target, String name, Class<?>[] types, Object[] args) {
        if (target == null) {
            return null;
        }
        try {
            return invoke(target, name, types, args);
        } catch (Throwable t) {
            Log.w(TAG, name + " unavailable: " + t);
            return null;
        }
    }

    private static Method findMethod(Class<?> cls, String name, Class<?>[] types) {
        for (Class<?> c = cls; c != null; c = c.getSuperclass()) {
            try {
                return c.getDeclaredMethod(name, types);
            } catch (NoSuchMethodException ignored) {
            }
            // Parameter class may be a superclass of the argument's runtime class.
            Method[] methods = c.getDeclaredMethods();
            for (int i = 0; i < methods.length; i++) {
                Method m = methods[i];
                if (!m.getName().equals(name)) {
                    continue;
                }
                Class<?>[] p = m.getParameterTypes();
                if (p.length != types.length) {
                    continue;
                }
                boolean ok = true;
                for (int j = 0; j < p.length; j++) {
                    if (types[j] != null && !p[j].isAssignableFrom(types[j])) {
                        ok = false;
                        break;
                    }
                }
                if (ok) {
                    return m;
                }
            }
        }
        return null;
    }

    private static Object value(Object obj) {
        return call(obj, "getValue");
    }

    private static Object call(Object obj, String name) {
        if (obj == null) {
            return null;
        }
        try {
            Method m = obj.getClass().getMethod(name, new Class<?>[0]);
            m.setAccessible(true);
            return m.invoke(obj, new Object[0]);
        } catch (Throwable t) {
            return null;
        }
    }

    private static double number(Object obj) {
        if (obj instanceof Number) {
            return ((Number) obj).doubleValue();
        }
        if (obj == null) {
            return 0.0;
        }
        try {
            Field[] fields = obj.getClass().getDeclaredFields();
            for (int i = 0; i < fields.length; i++) {
                Field f = fields[i];
                if (Modifier.isStatic(f.getModifiers())) {
                    continue;
                }
                f.setAccessible(true);
                Object v = f.get(obj);
                if (v instanceof Number) {
                    return ((Number) v).doubleValue();
                }
            }
        } catch (Throwable ignored) {
        }
        return 0.0;
    }

    private static double fieldNumber(Object obj, String name) {
        if (obj == null) {
            return 0.0;
        }
        try {
            return number(obj.getClass().getField(name).get(obj));
        } catch (Throwable t) {
            return 0.0;
        }
    }

    private static List<?> asList(Object obj) {
        if (obj instanceof List) {
            return (List<?>) obj;
        }
        if (obj != null && obj.getClass().isArray()) {
            int n = Array.getLength(obj);
            List<Object> out = new ArrayList<Object>(n);
            for (int i = 0; i < n; i++) {
                out.add(Array.get(obj, i));
            }
            return out;
        }
        return Collections.emptyList();
    }
}
