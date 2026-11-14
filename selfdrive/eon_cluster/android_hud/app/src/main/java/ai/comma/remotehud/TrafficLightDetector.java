package ai.comma.remotehud;

import android.content.Context;
import android.graphics.Bitmap;
import android.os.SystemClock;
import android.util.Log;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.FloatBuffer;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;

import ai.onnxruntime.OnnxTensor;
import ai.onnxruntime.OrtEnvironment;
import ai.onnxruntime.OrtSession;

/**
 * Traffic light colour from the EON road camera (2026-11-14), after carrot-wip's experimental
 * signal work (tools/signal_analysis/neural_signal.py): the pinned Autoware traffic light
 * networks, Apache-2.0 —
 *   detector   YOLOX-s tlr_car_ped_yolox_s_batch_1.onnx (416x416, BGR 0-255, top-left letterbox
 *              padded 114, strides 8/16/32, vehicle-light score obj*cls1 > 0.3, NMS 0.65)
 *   classifier MobileNetV2 traffic_light_classifier_mobilenetv2_batch_1.onnx (224x224 RGB,
 *              top-left letterbox padded 0, ImageNet mean/std, 11 lamp combinations).
 * Both are downloaded once from their pinned Hugging Face revisions and checked by SHA-256.
 *
 * Input is the upper road region the EON serves at native resolution (/api/road.jpg?signal=1).
 * The selected light is the confident one nearest the middle of the road; that is a
 * hypothesis, not proof of which light controls ego's lane. A colour counts once two photos
 * in a row agree (~0.7 s at 3 fps) and is kept for one missed photo.
 */
final class TrafficLightDetector {
    static final int NONE = 0;
    static final int RED = 1;
    static final int YELLOW = 2;
    static final int GREEN = 3;

    private static final String TAG = "HudTrafficLight";
    private static final String DETECTOR_URL = "https://huggingface.co/AutowareFoundation/traffic_light_fine_detector/"
            + "resolve/3f0616ffda0c19fc84fc455f988421c0df970e5d/tlr_car_ped_yolox_s_batch_1.onnx";
    private static final String DETECTOR_SHA = "1ad633066a1195006f4709f8fa07800dd65a74a814b3efb4c99bcc5a1a7962f6";
    private static final String CLASSIFIER_URL = "https://huggingface.co/AutowareFoundation/traffic_light_classifier/"
            + "resolve/527c4905afd8f1bfcc03253529a105f7fa5a6d36/traffic_light_classifier_mobilenetv2_batch_1.onnx";
    private static final String CLASSIFIER_SHA = "455b71b3b20d3a96aa0e49f32714ba50421f668a2f9b9907c30b1346ac8a3703";
    static final String[] LABELS = {"green", "left,red", "left,red,straight", "red", "red,right", "red,straight",
            "unknown", "yellow", "red,up_left", "red,right,straight", "red,up_right"};

    private static final int DET = 416;
    private static final int CLS = 224;
    private static final float DET_SCORE = 0.3f;
    private static final float NMS_IOU = 0.65f;
    private static final int MAX_BOXES = 4;
    private static final float[] MEAN = {123.675f, 116.28f, 103.53f};
    private static final float[] STD = {58.395f, 57.12f, 57.375f};
    private static final long RETRY_MS = 10 * 60 * 1000L;
    /** Stopped: the lights do not move, so the detector reruns only this often (classifier every photo). */
    private static final long STOPPED_DETECT_MS = 2000L;
    private static final float ROI_CAMERA_CENTRE = (0.5f - 0.20f) / (0.85f - 0.20f);

    static final class Result {
        final int color;
        final String label;
        final float score;
        /** Box {left, top, right, bottom} in camera pixels. */
        final float[] box;
        /** EON capture time of the photo (epoch ms). */
        final long eonTime;

        Result(int color, String label, float score, float[] box, long eonTime) {
            this.color = color;
            this.label = label;
            this.score = score;
            this.box = box;
            this.eonTime = eonTime;
        }
    }

    private final Context context;
    private OrtEnvironment env;
    private OrtSession detector;
    private OrtSession classifier;
    private long nextLoadAttempt;
    private final float[] grid;          // (x, y, stride) per YOLOX output row
    private final FloatBuffer detInput = FloatBuffer.allocate(3 * DET * DET);
    private final FloatBuffer clsInput = FloatBuffer.allocate(3 * CLS * CLS);
    private int[] pixels = new int[0];

    private List<float[]> lastBoxes = new ArrayList<>();   // detector boxes of the last photo
    private int lastW, lastH;
    private long lastDetectAt;
    volatile String backend = "";
    private int lastRaw = NONE;
    private Result stable;
    private long stableAt;
    volatile String status = "준비 전";

    TrafficLightDetector(Context context) {
        this.context = context.getApplicationContext();
        List<float[]> rows = new ArrayList<>();
        for (int s : new int[]{8, 16, 32}) {
            for (int y = 0; y < DET / s; y++) {
                for (int x = 0; x < DET / s; x++) rows.add(new float[]{x, y, s});
            }
        }
        grid = new float[rows.size() * 3];
        for (int i = 0; i < rows.size(); i++) System.arraycopy(rows.get(i), 0, grid, i * 3, 3);
    }

    /**
     * One upper-road photo -> the confirmed light, or null. crop is the photo's camera box
     * {u0, v0, u1, v1}. Runs on the signal thread; never throws.
     */
    Result detect(Bitmap photo, int[] crop, long eonTime, boolean stopped) {
        if (photo == null || photo.isRecycled() || crop == null || !ready()) return null;
        try {
            Result raw = run(photo, crop, eonTime, stopped);
            long now = SystemClock.elapsedRealtime();
            int color = raw == null ? NONE : raw.color;
            if (color != NONE && color == lastRaw) {
                stable = raw;
                stableAt = now;
            }
            lastRaw = color;
            if (stable != null && now - stableAt > 900L) stable = null;
            return stable;
        } catch (Throwable t) {
            Log.w(TAG, "detect failed", t);
            return null;
        }
    }

    private boolean ready() {
        if (detector != null && classifier != null) return true;
        long now = SystemClock.elapsedRealtime();
        if (now < nextLoadAttempt) return false;
        nextLoadAttempt = now + RETRY_MS;
        try {
            File dir = new File(context.getFilesDir(), "traffic_light");
            File det = model(dir, "detector.onnx", DETECTOR_URL, DETECTOR_SHA);
            File cls = model(dir, "classifier.onnx", CLASSIFIER_URL, CLASSIFIER_SHA);
            env = OrtEnvironment.getEnvironment();
            // The phone's neural accelerator (NNAPI, FP16) instead of the CPU: the CPU at 3 fps
            // took the S20 to ~90 °C (2026-11-14). CPU, one thread, only if NNAPI is refused.
            try {
                detector = env.createSession(det.getAbsolutePath(), nnapiOptions());
                classifier = env.createSession(cls.getAbsolutePath(), nnapiOptions());
                backend = "NNAPI";
            } catch (Throwable nnapi) {
                Log.w(TAG, "NNAPI unavailable, CPU", nnapi);
                OrtSession.SessionOptions cpu = new OrtSession.SessionOptions();
                cpu.setIntraOpNumThreads(1);
                detector = env.createSession(det.getAbsolutePath(), cpu);
                classifier = env.createSession(cls.getAbsolutePath(), cpu);
                backend = "CPU";
            }
            status = "동작 중 " + backend;
            return true;
        } catch (Throwable t) {
            detector = classifier = null;
            status = "모델 준비 실패: " + t.getMessage();
            Log.w(TAG, "traffic light models unavailable", t);
            return false;
        }
    }

    private static OrtSession.SessionOptions nnapiOptions() throws Exception {
        OrtSession.SessionOptions options = new OrtSession.SessionOptions();
        options.setIntraOpNumThreads(1);
        options.addNnapi(java.util.EnumSet.of(ai.onnxruntime.providers.NNAPIFlags.USE_FP16));
        return options;
    }

    /** The pinned model file, downloaded once and verified by SHA-256. */
    private File model(File dir, String name, String url, String sha) throws Exception {
        File file = new File(dir, name);
        if (file.isFile() && sha.equals(sha256(file))) return file;
        if (!dir.isDirectory() && !dir.mkdirs()) throw new IllegalStateException("no model folder");
        status = "모델 내려받는 중";
        File tmp = new File(dir, name + ".part");
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(30000);
        conn.setInstanceFollowRedirects(true);
        try (InputStream in = conn.getInputStream(); FileOutputStream out = new FileOutputStream(tmp)) {
            byte[] buffer = new byte[64 * 1024];
            int n;
            while ((n = in.read(buffer)) != -1) out.write(buffer, 0, n);
        } finally {
            conn.disconnect();
        }
        if (!sha.equals(sha256(tmp))) {
            tmp.delete();
            throw new IllegalStateException(name + " checksum mismatch");
        }
        if (!tmp.renameTo(file)) throw new IllegalStateException(name + " rename failed");
        return file;
    }

    private static String sha256(File file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream in = new FileInputStream(file)) {
            byte[] buffer = new byte[64 * 1024];
            int n;
            while ((n = in.read(buffer)) != -1) digest.update(buffer, 0, n);
        }
        StringBuilder hex = new StringBuilder();
        for (byte b : digest.digest()) hex.append(String.format(Locale.US, "%02x", b));
        return hex.toString();
    }

    private Result run(Bitmap photo, int[] crop, long eonTime, boolean stopped) throws Exception {
        int w = photo.getWidth(), h = photo.getHeight();
        if (pixels.length < w * h) pixels = new int[w * h];
        photo.getPixels(pixels, 0, w, 0, 0, w, h);
        long now = SystemClock.elapsedRealtime();
        List<float[]> kept;
        if (stopped && w == lastW && h == lastH && now - lastDetectAt < STOPPED_DETECT_MS) {
            kept = lastBoxes;   // standing still: same lights, only re-read their colour
        } else {
            kept = detectBoxes(photo, w, h);
            lastBoxes = kept;
            lastW = w;
            lastH = h;
            lastDetectAt = now;
        }
        return choose(kept, w, h, crop, eonTime);
    }

    private List<float[]> detectBoxes(Bitmap photo, int w, int h) throws Exception {
        // Detector input: top-left letterbox, BGR 0-255, pad 114, NCHW.
        float scale = Math.min(DET / (float) w, DET / (float) h);
        int nw = Math.max(1, (int) (w * scale)), nh = Math.max(1, (int) (h * scale));
        Bitmap resized = Bitmap.createScaledBitmap(photo, nw, nh, true);
        int[] small = new int[nw * nh];
        resized.getPixels(small, 0, nw, 0, 0, nw, nh);
        if (resized != photo) resized.recycle();
        float[] in = detInput.array();
        java.util.Arrays.fill(in, 114f);
        int plane = DET * DET;
        for (int y = 0; y < nh; y++) {
            for (int x = 0; x < nw; x++) {
                int p = small[y * nw + x];
                int o = y * DET + x;
                in[o] = p & 0xFF;                       // B
                in[plane + o] = (p >> 8) & 0xFF;        // G
                in[2 * plane + o] = (p >> 16) & 0xFF;   // R
            }
        }
        float[][] rows;
        detInput.rewind();
        try (OnnxTensor tensor = OnnxTensor.createTensor(env, detInput, new long[]{1, 3, DET, DET});
             OrtSession.Result out = detector.run(Collections.singletonMap("images", tensor))) {
            rows = ((float[][][]) out.get(0).getValue())[0];
        }

        List<float[]> boxes = new ArrayList<>();   // {x0, y0, x1, y1, score} in photo pixels
        int count = Math.min(rows.length, grid.length / 3);
        for (int i = 0; i < count; i++) {
            float[] r = rows[i];
            float score = r[4] * r[6];
            if (!(score > DET_SCORE)) continue;
            float gx = grid[i * 3], gy = grid[i * 3 + 1], s = grid[i * 3 + 2];
            float cx = (r[0] + gx) * s, cy = (r[1] + gy) * s;
            float bw = (float) Math.exp(r[2]) * s, bh = (float) Math.exp(r[3]) * s;
            float x0 = clamp((cx - bw / 2f) / scale, w), y0 = clamp((cy - bh / 2f) / scale, h);
            float x1 = clamp((cx + bw / 2f) / scale, w), y1 = clamp((cy + bh / 2f) / scale, h);
            if (x1 > x0 && y1 > y0) boxes.add(new float[]{x0, y0, x1, y1, score});
        }
        Collections.sort(boxes, (a, b) -> Float.compare(b[4], a[4]));
        List<float[]> kept = new ArrayList<>();
        for (float[] box : boxes) {
            boolean separate = true;
            for (float[] old : kept) {
                if (overlap(box, old) >= NMS_IOU) {
                    separate = false;
                    break;
                }
            }
            if (separate) kept.add(box);
            if (kept.size() >= MAX_BOXES) break;
        }
        return kept;
    }

    private Result choose(List<float[]> kept, int w, int h, int[] crop, long eonTime) throws Exception {

        // Classify each light; pick the confident one nearest the middle of the road.
        float camW = crop[2] - crop[0], camH = crop[3] - crop[1];
        Result best = null;
        float bestRank = 0f;
        for (float[] box : kept) {
            float[] probs = classify(w, h, box);
            if (probs == null) continue;
            int index = 0;
            for (int i = 1; i < probs.length; i++) if (probs[i] > probs[index]) index = i;
            String label = index < LABELS.length ? LABELS[index] : "unknown";
            int color = colorOf(label);
            if (color == NONE) continue;
            // The region spans camera x 20-85 %, so the camera centre sits at 0.4615 of it.
            float u = (box[0] + box[2]) / 2f / w;
            float rank = box[4] * (1.2f - Math.abs(u - ROI_CAMERA_CENTRE));
            if (rank > bestRank) {
                bestRank = rank;
                float sx = camW / w, sy = camH / h;
                best = new Result(color, label, box[4], new float[]{crop[0] + box[0] * sx, crop[1] + box[1] * sy,
                        crop[0] + box[2] * sx, crop[1] + box[3] * sy}, eonTime);
            }
        }
        return best;
    }

    /** Classifier probabilities for one box of the photo in pixels[]. */
    private float[] classify(int w, int h, float[] box) throws Exception {
        int x0 = Math.max(0, (int) box[0]), y0 = Math.max(0, (int) box[1]);
        int x1 = Math.min(w, (int) box[2]), y1 = Math.min(h, (int) box[3]);
        int cw = x1 - x0, ch = y1 - y0;
        if (cw < 1 || ch < 1) return null;
        float scale = Math.min(CLS / (float) cw, CLS / (float) ch);
        int nw = Math.max(1, Math.round(cw * scale)), nh = Math.max(1, Math.round(ch * scale));
        float[] in = clsInput.array();
        int plane = CLS * CLS;
        for (int c = 0; c < 3; c++) {
            java.util.Arrays.fill(in, c * plane, (c + 1) * plane, (0f - MEAN[c]) / STD[c]);
        }
        // Bilinear resize of the crop straight from pixels[] (RGB, normalized).
        for (int y = 0; y < nh; y++) {
            float sy = Math.min(ch - 1f, Math.max(0f, (y + 0.5f) / scale - 0.5f));
            int yA = (int) sy, yB = Math.min(ch - 1, yA + 1);
            float fy = sy - yA;
            for (int x = 0; x < nw; x++) {
                float sx = Math.min(cw - 1f, Math.max(0f, (x + 0.5f) / scale - 0.5f));
                int xA = (int) sx, xB = Math.min(cw - 1, xA + 1);
                float fx = sx - xA;
                int p00 = pixels[(y0 + yA) * w + x0 + xA], p01 = pixels[(y0 + yA) * w + x0 + xB];
                int p10 = pixels[(y0 + yB) * w + x0 + xA], p11 = pixels[(y0 + yB) * w + x0 + xB];
                int o = y * CLS + x;
                for (int c = 0; c < 3; c++) {
                    int shift = 16 - 8 * c;   // R, G, B
                    float v = ((p00 >> shift) & 0xFF) * (1 - fx) * (1 - fy) + ((p01 >> shift) & 0xFF) * fx * (1 - fy)
                            + ((p10 >> shift) & 0xFF) * (1 - fx) * fy + ((p11 >> shift) & 0xFF) * fx * fy;
                    in[c * plane + o] = (v - MEAN[c]) / STD[c];
                }
            }
        }
        clsInput.rewind();
        try (OnnxTensor tensor = OnnxTensor.createTensor(env, clsInput, new long[]{1, 3, CLS, CLS});
             OrtSession.Result out = classifier.run(Collections.singletonMap("input", tensor))) {
            return ((float[][]) out.get(0).getValue())[0];
        }
    }

    static int colorOf(String label) {
        if (label.contains("red")) return RED;
        if (label.equals("yellow")) return YELLOW;
        if (label.equals("green")) return GREEN;
        return NONE;
    }

    static String colorName(int color) {
        return color == RED ? "red" : color == YELLOW ? "yellow" : color == GREEN ? "green" : "none";
    }

    private static float overlap(float[] a, float[] b) {
        float inter = Math.max(0f, Math.min(a[2], b[2]) - Math.max(a[0], b[0]))
                * Math.max(0f, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
        return inter / Math.max(1f, (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter);
    }

    private static float clamp(float v, float max) {
        return Math.max(0f, Math.min(max, v));
    }
}
