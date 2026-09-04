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
    private static final int MAX_RESULTS = 40;
    // MobileNet occasionally misses one or two of the 2 Hz camera frames.
    // Keep a matched display track for about 1.5 seconds so weak JPEGs or a
    // brief partial occlusion do not make the HUD vehicle blink.
    private static final int MAX_MISSED_FRAMES = 3;
    private static final float TRACK_ALPHA = 0.42f;
    // 과다 검출 억제: 새 트랙은 연속 2프레임(1 s @2 Hz) 확인 후에만 표시하고,
    // 같은 프레임 안의 중복 박스(세단/트럭 이중 검출)는 하나로 합친다.
    private static final int CONFIRM_HITS = 2;
    private static final double MERGE_DISTANCE_M = 3.0d;
    private static final double MERGE_LATERAL_M = 1.4d;
    private static final double MAX_LATERAL_M = 7.5d;   // 좌우 2차선까지만

    private final ObjectDetector detector;
    private final ArrayList<VehicleTrack> tracks = new ArrayList<>();

    private static final class VehicleTrack {
        double distance;
        double lateral;
        float score;
        String type;
        int missed;
        int hits;
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
                        // setting can select 25..45% without rebuilding.
                        .setScoreThreshold(0.25f)
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
            float threshold = Math.max(0.25f, Math.min(0.70f,
                    scene.optInt("hudVisionThreshold", 50) * 0.01f));
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
                // 4px 짜리 점 검출은 대부분 오검출. 입력 해상도의 3% 이상만 받는다.
                if (box == null || box.width() < image.getWidth() * 0.03f
                        || box.height() < image.getHeight() * 0.03f) {
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
                        || distance < 2d || distance > 120d
                        || Math.abs(lateral) > MAX_LATERAL_M) {
                    continue;
                }
                // Retain the COCO class for display-only type-specific HUD
                // silhouettes.  This value never leaves the S9 or reaches
                // RadarD/controls.
                VehicleTrack candidate = new VehicleTrack(distance, lateral,
                        vehicle.getScore(), normalizeVehicleType(vehicle.getLabel()));
                if (!mergeDuplicate(observations, candidate)) {
                    observations.add(candidate);
                }
                if (observations.size() >= MAX_RESULTS) {
                    break;
                }
            }
            return trackedOutput(observations);
        } finally {
            image.recycle();
        }
    }

    /** 같은 프레임 안에서 거의 같은 자리의 박스는 점수 높은 쪽 하나로 합친다. */
    private static boolean mergeDuplicate(ArrayList<VehicleTrack> observations,
                                          VehicleTrack candidate) {
        for (VehicleTrack other : observations) {
            if (Math.abs(other.distance - candidate.distance) <= MERGE_DISTANCE_M
                    && Math.abs(other.lateral - candidate.lateral) <= MERGE_LATERAL_M) {
                if (candidate.score > other.score) {
                    other.distance = candidate.distance;
                    other.lateral = candidate.lateral;
                    other.score = candidate.score;
                    other.type = candidate.type;
                }
                return true;
            }
        }
        return false;
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
                observation.hits = 1;
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
                best.hits++;
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
                // Keep a held track clearly visible while fading it gently
                // gently for up to roughly 1.5 s at 2 Hz. This bridges a truck
                // edge, glare, or one poor JPEG without leaving a long-lived
                // ghost after the physical vehicle has gone.
                // 0.30 바닥은 표시 컷(0.25)보다 높아 유령이 1.5 s 남았다. 바닥을
                // 낮춰 두 번째 미검출부터는 화면에서 빠지게 한다.
                track.score = Math.max(0.12f, track.score * 0.72f);
            }
            if (track.missed > MAX_MISSED_FRAMES) {
                tracks.remove(i);
                continue;
            }
            if (track.hits < CONFIRM_HITS) {
                // 아직 확인되지 않은 트랙은 내부에만 유지하고 표시하지 않는다.
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
