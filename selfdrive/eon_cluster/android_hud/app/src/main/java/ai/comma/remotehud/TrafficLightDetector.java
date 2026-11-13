package ai.comma.remotehud;

import android.content.Context;
import android.content.res.AssetFileDescriptor;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.os.SystemClock;
import android.util.Log;

import org.tensorflow.lite.Interpreter;

import java.io.FileInputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.MappedByteBuffer;
import java.nio.channels.FileChannel;
import java.util.HashMap;
import java.util.Map;

/**
 * Display-only traffic light test (2026-11-14): finds traffic lights in the EON road photos the
 * HUD already receives (4 fps) and reads their colour. Nothing here reaches the car's controls;
 * the HUD only draws the result in the road preview so its accuracy can be judged on the road.
 *
 * Detection: COCO SSD MobileNet v1 (quantized, 300x300, class 9 = traffic light), downloaded
 * by the build into assets/detect.tflite. Colour: bright, saturated pixels inside the box,
 * counted by hue (Korean lights: red, amber, blue-green). A colour is shown once two frames in
 * a row agree, and kept for one missed frame.
 */
final class TrafficLightDetector {
    static final int NONE = 0;
    static final int RED = 1;
    static final int YELLOW = 2;
    static final int GREEN = 3;

    private static final String TAG = "HudTrafficLight";
    private static final String MODEL = "detect.tflite";
    private static final int SIZE = 300;
    private static final int MAX_DETECTIONS = 10;
    private static final int TRAFFIC_LIGHT_CLASS = 9;
    private static final float MIN_SCORE = 0.45f;
    /** Lights hang above the road: ignore boxes centred in the lower part of the photo. */
    private static final float MAX_CENTER_Y = 0.62f;
    private static final long HOLD_MS = 600;

    static final class Result {
        final int color;
        /** Box {left, top, right, bottom}, normalized to the photo. */
        final float[] box;
        final float score;

        Result(int color, float[] box, float score) {
            this.color = color;
            this.box = box;
            this.score = score;
        }
    }

    private final Context context;
    private Interpreter interpreter;
    private boolean failed;
    private final ByteBuffer input = ByteBuffer.allocateDirect(SIZE * SIZE * 3).order(ByteOrder.nativeOrder());
    private final int[] scaledPixels = new int[SIZE * SIZE];
    private final float[][][] boxes = new float[1][MAX_DETECTIONS][4];
    private final float[][] classes = new float[1][MAX_DETECTIONS];
    private final float[][] scores = new float[1][MAX_DETECTIONS];
    private final float[] count = new float[1];

    private int lastRaw = NONE;
    private Result stable;
    private long stableAt;

    TrafficLightDetector(Context context) {
        this.context = context.getApplicationContext();
    }

    /** Called on the road preview thread for every new photo. Never throws. */
    Result detect(Bitmap photo) {
        if (photo == null || photo.isRecycled() || !ready()) return null;
        try {
            Result raw = run(photo);
            long now = SystemClock.elapsedRealtime();
            int color = raw == null ? NONE : raw.color;
            if (color != NONE && color == lastRaw) {
                stable = raw;
                stableAt = now;
            }
            lastRaw = color;
            if (stable != null && now - stableAt > HOLD_MS) stable = null;
            return stable;
        } catch (Throwable t) {
            Log.w(TAG, "detect failed", t);
            return null;
        }
    }

    private boolean ready() {
        if (interpreter != null) return true;
        if (failed) return false;
        try (AssetFileDescriptor fd = context.getAssets().openFd(MODEL);
             FileInputStream stream = new FileInputStream(fd.getFileDescriptor())) {
            MappedByteBuffer model = stream.getChannel().map(FileChannel.MapMode.READ_ONLY,
                    fd.getStartOffset(), fd.getDeclaredLength());
            Interpreter.Options options = new Interpreter.Options();
            options.setNumThreads(2);
            interpreter = new Interpreter(model, options);
            return true;
        } catch (Throwable t) {
            failed = true;
            Log.w(TAG, "traffic light model unavailable", t);
            return false;
        }
    }

    private Result run(Bitmap photo) {
        Bitmap scaled = Bitmap.createScaledBitmap(photo, SIZE, SIZE, true);
        scaled.getPixels(scaledPixels, 0, SIZE, 0, 0, SIZE, SIZE);
        if (scaled != photo) scaled.recycle();
        input.rewind();
        for (int pixel : scaledPixels) {
            input.put((byte) ((pixel >> 16) & 0xFF));
            input.put((byte) ((pixel >> 8) & 0xFF));
            input.put((byte) (pixel & 0xFF));
        }
        input.rewind();
        Map<Integer, Object> outputs = new HashMap<>();
        outputs.put(0, boxes);
        outputs.put(1, classes);
        outputs.put(2, scores);
        outputs.put(3, count);
        interpreter.runForMultipleInputsOutputs(new Object[]{input}, outputs);

        Result best = null;
        float bestRank = 0f;
        int n = Math.min(MAX_DETECTIONS, Math.max(0, Math.round(count[0])));
        for (int i = 0; i < n; i++) {
            if (Math.round(classes[0][i]) != TRAFFIC_LIGHT_CLASS || scores[0][i] < MIN_SCORE) continue;
            float top = clamp(boxes[0][i][0]), left = clamp(boxes[0][i][1]);
            float bottom = clamp(boxes[0][i][2]), right = clamp(boxes[0][i][3]);
            if (bottom <= top || right <= left || (top + bottom) / 2f > MAX_CENTER_Y) continue;
            int color = color(photo, left, top, right, bottom);
            if (color == NONE) continue;
            // Prefer confident lights near the middle of the road ahead.
            float centre = Math.abs((left + right) / 2f - 0.5f);
            float rank = scores[0][i] * (1.2f - centre);
            if (rank > bestRank) {
                bestRank = rank;
                best = new Result(color, new float[]{left, top, right, bottom}, scores[0][i]);
            }
        }
        return best;
    }

    /** The lit colour inside the box, from bright saturated pixels counted by hue. */
    static int color(Bitmap photo, float left, float top, float right, float bottom) {
        int w = photo.getWidth(), h = photo.getHeight();
        int x0 = (int) (left * w), x1 = (int) Math.ceil(right * w);
        int y0 = (int) (top * h), y1 = (int) Math.ceil(bottom * h);
        x0 = Math.max(0, x0); y0 = Math.max(0, y0);
        x1 = Math.min(w, x1); y1 = Math.min(h, y1);
        if (x1 - x0 < 2 || y1 - y0 < 2) return NONE;
        int[] pixels = new int[(x1 - x0) * (y1 - y0)];
        photo.getPixels(pixels, 0, x1 - x0, x0, y0, x1 - x0, y1 - y0);
        int red = 0, yellow = 0, green = 0;
        float[] hsv = new float[3];
        for (int pixel : pixels) {
            Color.colorToHSV(pixel, hsv);
            // Lit lamps only: dimmer saturated pixels (autumn leaves, signs) read as amber
            // in the 2026-11 road photos.
            if (hsv[2] < 0.75f || hsv[1] < 0.45f) continue;
            float hue = hsv[0];
            if (hue < 20f || hue > 335f) red++;
            else if (hue < 65f) yellow++;
            else if (hue >= 110f && hue <= 205f) green++;
        }
        int lit = Math.max(red, Math.max(yellow, green));
        if (lit < Math.max(3, pixels.length / 80)) return NONE;
        if (lit == red) return RED;
        if (lit == yellow) return YELLOW;
        return GREEN;
    }

    private static float clamp(float v) {
        return Math.max(0f, Math.min(1f, v));
    }
}
