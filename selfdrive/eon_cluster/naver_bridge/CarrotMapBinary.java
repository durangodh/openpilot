package com.naver.map.carrot;

import android.graphics.Bitmap;

import java.io.ByteArrayOutputStream;
import java.lang.reflect.Field;
import java.lang.reflect.Method;

/** Sends map_main as a raw JPEG WebSocket frame, matching the TMAP transport. */
final class CarrotMapBinary {
    private static final String TAG = "CarrotMapBinary";
    private static volatile Field wsField;
    private static volatile Method binarySend;
    private static volatile boolean firstSent;
    private static volatile long lastFailureLog;

    private CarrotMapBinary() {
    }

    static boolean send(CarrotNaverBridge bridge, Object value) {
        Bitmap bitmap = value instanceof Bitmap ? (Bitmap) value : null;
        try {
            if (bridge == null || bitmap == null || bitmap.isRecycled()) {
                return false;
            }
            ByteArrayOutputStream output = new ByteArrayOutputStream(128 * 1024);
            // TMAP requests JPEG quality 65 for its map_main stream. Use the same
            // payload profile so EON and S9 receive an equivalent map frame.
            if (!bitmap.compress(Bitmap.CompressFormat.JPEG, 65, output)) {
                throw new IllegalStateException("JPEG compression returned false");
            }
            byte[] jpeg = output.toByteArray();
            Object ws = websocket(bridge);
            if (ws == null) {
                throw new IllegalStateException("WebSocket unavailable");
            }
            Method send = binarySend;
            if (send == null || send.getDeclaringClass() != ws.getClass()) {
                send = ws.getClass().getDeclaredMethod("send", byte[].class);
                send.setAccessible(true);
                binarySend = send;
            }
            boolean ok = Boolean.TRUE.equals(send.invoke(ws, new Object[]{jpeg}));
            if (!ok) {
                throw new IllegalStateException("binary WebSocket send failed");
            }
            if (!firstSent) {
                firstSent = true;
                CarrotHudLog.log(TAG, "first binary map_main " + jpeg.length + " bytes");
            }
            return true;
        } catch (Throwable t) {
            long now = android.os.SystemClock.elapsedRealtime();
            if (lastFailureLog == 0 || now - lastFailureLog >= 5000) {
                lastFailureLog = now;
                CarrotHudLog.log(TAG, "binary map_main failed: " + t);
            }
            return false;
        } finally {
            if (bitmap != null && !bitmap.isRecycled()) {
                bitmap.recycle();
            }
        }
    }

    private static Object websocket(CarrotNaverBridge bridge) throws Exception {
        Field field = wsField;
        if (field == null) {
            field = CarrotNaverBridge.class.getDeclaredField("ws");
            field.setAccessible(true);
            wsField = field;
        }
        return field.get(bridge);
    }
}
