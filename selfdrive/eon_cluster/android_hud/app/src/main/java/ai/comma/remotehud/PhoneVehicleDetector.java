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

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** CPU TFLite detector whose output is consumed only by the HUD renderer. */
final class PhoneVehicleDetector implements AutoCloseable {
    private static final float CAMERA_TO_BUMPER_M = 1.52f;
    private static final int MAX_RESULTS = 24;
    // MobileNet occasionally misses one or two of the 2 Hz camera frames.
    // Keep a matched display track for one second so a single weak JPEG or
    // partial occlusion does not make the HUD vehicle blink.
    private static final int MAX_MISSED_FRAMES = 2;
    private static final float TRACK_ALPHA = 0.42f;

    private final ObjectDetector detector;
    private final ArrayList<VehicleTrack> tracks = new ArrayList<>();

    private static final class VehicleTrack {
        double distance;
        double lateral;
        float score;
        String type;
        int missed;
        boolean matched;
        double distanceStep;
        double lateralStep;

        VehicleTrack(double distance, double lateral, float score, String type) {
            this.distance = distance;
            this.lateral = lateral;
            this.score = score;
            this.type = type;
        }
    }

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
        ArrayList<VehicleTrack> observations = new ArrayList<>();
        JSONObject ground = scene.optJSONObject("cameraGround");
        JSONArray matrix = ground == null ? null : ground.optJSONArray("m");
        if (jpeg == null || jpeg.length == 0 || matrix == null || matrix.length() != 9) {
            return trackedOutput(observations);
        }

        Bitmap image = BitmapFactory.decodeByteArray(jpeg, 0, jpeg.length);
        if (image == null) {
            return trackedOutput(observations);
        }
        try {
            int sourceWidth = ground.optInt("w", 0);
            int sourceHeight = ground.optInt("h", 0);
            if (sourceWidth <= 0 || sourceHeight <= 0) {
                return trackedOutput(observations);
            }
            float threshold = Math.max(0.30f, Math.min(0.90f,
                    scene.optInt("hudVisionThreshold", 55) * 0.01f));
            double[] m = new double[9];
            for (int i = 0; i < m.length; i++) {
                m[i] = matrix.optDouble(i, Double.NaN);
                if (!Double.isFinite(m[i])) {
                    return trackedOutput(observations);
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
                // Retain the COCO class for display-only type-specific HUD
                // silhouettes.  This value never leaves the S9 or reaches
                // RadarD/controls.
                observations.add(new VehicleTrack(distance, lateral, vehicle.getScore(),
                        normalizeVehicleType(vehicle.getLabel())));
                if (observations.size() >= MAX_RESULTS) {
                    break;
                }
            }
            return trackedOutput(observations);
        } finally {
            image.recycle();
        }
    }

    private JSONArray trackedOutput(ArrayList<VehicleTrack> observations) throws Exception {
        for (VehicleTrack track : tracks) {
            track.matched = false;
        }
        for (VehicleTrack observation : observations) {
            VehicleTrack best = null;
            double bestCost = Double.POSITIVE_INFINITY;
            for (VehicleTrack track : tracks) {
                if (track.matched) {
                    continue;
                }
                double predictedDistance = track.distance + track.distanceStep;
                double predictedLateral = track.lateral + track.lateralStep;
                double distanceError = Math.abs(predictedDistance - observation.distance);
                double lateralError = Math.abs(predictedLateral - observation.lateral);
                double distanceGate = Math.max(4d, Math.min(12d, observation.distance * 0.18d));
                if (distanceError <= distanceGate && lateralError <= 2.2d) {
                    double cost = distanceError / distanceGate + lateralError / 2.2d;
                    if (cost < bestCost) {
                        bestCost = cost;
                        best = track;
                    }
                }
            }
            if (best == null) {
                observation.matched = true;
                tracks.add(observation);
            } else {
                double oldDistance = best.distance;
                double oldLateral = best.lateral;
                double predictedDistance = oldDistance + best.distanceStep;
                double predictedLateral = oldLateral + best.lateralStep;
                best.distance = predictedDistance
                        + (observation.distance - predictedDistance) * TRACK_ALPHA;
                best.lateral = predictedLateral
                        + (observation.lateral - predictedLateral) * TRACK_ALPHA;
                best.distanceStep = clampStep(best.distanceStep * 0.55d
                        + (observation.distance - oldDistance) * 0.45d, 8d);
                best.lateralStep = clampStep(best.lateralStep * 0.55d
                        + (observation.lateral - oldLateral) * 0.45d, 1.5d);
                best.score = Math.max(observation.score, best.score * 0.92f);
                best.type = observation.type;
                best.missed = 0;
                best.matched = true;
            }
        }

        JSONArray output = new JSONArray();
        for (int i = tracks.size() - 1; i >= 0; i--) {
            VehicleTrack track = tracks.get(i);
            if (!track.matched) {
                track.missed++;
                track.distance += track.distanceStep;
                track.lateral += track.lateralStep;
                track.distanceStep *= 0.70d;
                track.lateralStep *= 0.70d;
                // The renderer's validity floor is 0.30. Keep a held track at
                // that floor and let the bounded miss count expire it instead
                // of making a low-confidence vehicle blink immediately.
                track.score = Math.max(0.30f, track.score * 0.90f);
            }
            if (track.missed > MAX_MISSED_FRAMES) {
                tracks.remove(i);
                continue;
            }
            JSONObject object = new JSONObject();
            object.put("d", Math.round(track.distance * 10d) / 10d);
            object.put("y", Math.round(track.lateral * 100d) / 100d);
            object.put("p", Math.round(track.score * 100d) / 100d);
            object.put("src", "P");
            object.put("type", track.type);
            output.put(object);
        }
        return output;
    }

    private static double clampStep(double value, double limit) {
        return Math.max(-limit, Math.min(limit, value));
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
        tracks.clear();
        detector.close();
    }
}
