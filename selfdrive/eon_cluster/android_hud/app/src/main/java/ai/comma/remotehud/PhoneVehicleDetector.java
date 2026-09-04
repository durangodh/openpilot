package ai.comma.remotehud;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.RectF;

import org.json.JSONArray;
import org.json.JSONObject;
import org.tensorflow.lite.support.image.TensorImage;
import org.tensorflow.lite.support.label.Category;
import org.tensorflow.lite.task.vision.detector.Detection;
import org.tensorflow.lite.task.vision.detector.ObjectDetector;

import java.util.List;
import java.util.Locale;

/** CPU TFLite detector whose output is consumed only by the HUD renderer. */
final class PhoneVehicleDetector implements AutoCloseable {
    private static final float CAMERA_TO_BUMPER_M = 1.52f;
    private static final int MAX_RESULTS = 24;

    private final ObjectDetector detector;

    PhoneVehicleDetector(Context context) throws Exception {
        ObjectDetector.ObjectDetectorOptions options =
                ObjectDetector.ObjectDetectorOptions.builder()
                        // Keep the runtime threshold low enough that the EON
                        // setting can still select 30..90% without rebuilding.
                        .setScoreThreshold(0.30f)
                        .setMaxResults(MAX_RESULTS)
                        .build();
        detector = ObjectDetector.createFromFileAndOptions(
                context, "mobilenetv1.tflite", options);
    }

    JSONArray detect(byte[] jpeg, JSONObject scene) throws Exception {
        JSONArray output = new JSONArray();
        JSONObject ground = scene.optJSONObject("cameraGround");
        JSONArray matrix = ground == null ? null : ground.optJSONArray("m");
        if (jpeg == null || jpeg.length == 0 || matrix == null || matrix.length() != 9) {
            return output;
        }

        Bitmap image = BitmapFactory.decodeByteArray(jpeg, 0, jpeg.length);
        if (image == null) {
            return output;
        }
        try {
            int sourceWidth = ground.optInt("w", 0);
            int sourceHeight = ground.optInt("h", 0);
            if (sourceWidth <= 0 || sourceHeight <= 0) {
                return output;
            }
            float threshold = Math.max(0.30f, Math.min(0.90f,
                    scene.optInt("hudVisionThreshold", 55) * 0.01f));
            double[] m = new double[9];
            for (int i = 0; i < m.length; i++) {
                m[i] = matrix.optDouble(i, Double.NaN);
                if (!Double.isFinite(m[i])) {
                    return output;
                }
            }

            TensorImage input = TensorImage.fromBitmap(image);
            List<Detection> detections = detector.detect(input);
            for (Detection detection : detections) {
                Category vehicle = bestVehicleCategory(detection.getCategories());
                if (vehicle == null || vehicle.getScore() < threshold) {
                    continue;
                }
                RectF box = detection.getBoundingBox();
                if (box == null || box.width() < 4f || box.height() < 4f) {
                    continue;
                }
                double pixelX = Math.max(0d, Math.min(image.getWidth() - 1d,
                        box.centerX())) * sourceWidth / image.getWidth();
                double pixelY = Math.max(0d, Math.min(image.getHeight() - 1d,
                        box.bottom)) * sourceHeight / image.getHeight();
                double scale = m[6] * pixelX + m[7] * pixelY + m[8];
                if (!Double.isFinite(scale) || Math.abs(scale) < 1e-7) {
                    continue;
                }
                double distance = (m[0] * pixelX + m[1] * pixelY + m[2]) / scale
                        - CAMERA_TO_BUMPER_M;
                // Keep the camera projection's native left-positive lateral axis.
                // hudPathFlip only corrects model geometry on the installed display;
                // applying it here mirrors phone detections to the opposite lane.
                double lateral = (m[3] * pixelX + m[4] * pixelY + m[5]) / scale;
                if (!Double.isFinite(distance) || !Double.isFinite(lateral)
                        || distance < 2d || distance > 120d || Math.abs(lateral) > 15d) {
                    continue;
                }
                JSONObject object = new JSONObject();
                object.put("d", Math.round(distance * 10d) / 10d);
                object.put("y", Math.round(lateral * 100d) / 100d);
                object.put("p", Math.round(vehicle.getScore() * 100d) / 100d);
                object.put("src", "P");
                // Retain the COCO class for display-only type-specific HUD
                // silhouettes.  This value never leaves the S9 or reaches
                // RadarD/controls.
                object.put("type", normalizeVehicleType(vehicle.getLabel()));
                output.put(object);
                if (output.length() >= MAX_RESULTS) {
                    break;
                }
            }
            return output;
        } finally {
            image.recycle();
        }
    }

    private static Category bestVehicleCategory(List<Category> categories) {
        Category best = null;
        for (Category category : categories) {
            String label = normalizeVehicleType(category.getLabel());
            boolean vehicle = label.equals("car") || label.equals("truck")
                    || label.equals("bus") || label.equals("motorcycle")
                    || label.equals("bicycle");
            if (vehicle && (best == null || category.getScore() > best.getScore())) {
                best = category;
            }
        }
        return best;
    }

    private static String normalizeVehicleType(String label) {
        String normalized = label == null ? "" : label.toLowerCase(Locale.US);
        return normalized.equals("motorbike") ? "motorcycle" : normalized;
    }

    @Override
    public void close() {
        detector.close();
    }
}
