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
    private final ObjectDetector detector;
    private final CameraVehicleTracker tracker = new CameraVehicleTracker();

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

    JSONArray detect(byte[] jpeg, JSONObject scene, long frameTime) throws Exception {
        ArrayList<CameraVehicleTracker.Box> observations = new ArrayList<>();
        JSONObject ground = scene.optJSONObject("cameraGround");
        JSONArray matrix = ground == null ? null : ground.optJSONArray("m");
        if (jpeg == null || jpeg.length == 0 || matrix == null || matrix.length() != 9) {
            return trackedOutput(observations, frameTime);
        }

        Bitmap image = BitmapFactory.decodeByteArray(jpeg, 0, jpeg.length);
        if (image == null) {
            return trackedOutput(observations, frameTime);
        }
        try {
            int sourceWidth = ground.optInt("w", 0);
            int sourceHeight = ground.optInt("h", 0);
            if (sourceWidth <= 0 || sourceHeight <= 0) {
                return trackedOutput(observations, frameTime);
            }
            float threshold = Math.max(0.25f, Math.min(0.70f,
                    scene.optInt("hudVisionThreshold", 50) * 0.01f));
            double[] m = new double[9];
            for (int i = 0; i < m.length; i++) {
                m[i] = matrix.optDouble(i, Double.NaN);
                if (!Double.isFinite(m[i])) {
                    return trackedOutput(observations, frameTime);
                }
            }

            TensorImage input = TensorImage.fromBitmap(image);
            List<Detection> detections = detector.detect(input);
            for (Detection detection : detections) {
                Category vehicle = bestVehicleCategory(detection.getCategories());
                if (vehicle == null) {
                    continue;
                }
                String vehicleType = normalizeVehicleType(vehicle.getLabel());
                float classThreshold = "person".equals(vehicleType)
                        ? Math.max(0.25f, threshold - 0.12f) : threshold;
                if (vehicle.getScore() < classThreshold) continue;
                RectF box = detection.getBoundingBox();
                // 보행자는 차보다 폭이 좁으므로 별도 최소 폭을 적용한다.
                float minWidthRatio = "person".equals(vehicleType) ? 0.015f : 0.03f;
                if (box == null || box.width() < image.getWidth() * minWidthRatio
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
                // Keep the native camera axis here. ModelWorldGL applies the
                // installed display's hudPathFlip after tracker prediction.
                double lateral = (m[3] * pixelX + m[4] * pixelY + m[5]) / scale;
                if (!Double.isFinite(distance) || !Double.isFinite(lateral)
                        || distance < 2d || distance > 120d
                        || Math.abs(lateral) > 12d) {
                    continue;
                }
                // Retain the COCO class for display-only type-specific HUD
                // silhouettes.  This value never leaves the S9 or reaches
                // RadarD/controls.
                // Estimate visible width from both bottom corners on the road
                // plane; retain image boxes for identity and duplicate matching.
                double lx = Math.max(0d, box.left) * sourceWidth / image.getWidth();
                double rx = Math.min(image.getWidth()-1d, box.right) * sourceWidth / image.getWidth();
                double ls = m[6]*lx + m[7]*pixelY + m[8];
                double rs = m[6]*rx + m[7]*pixelY + m[8];
                double estimatedWidth = 1.88d;
                if (Math.abs(ls)>1e-7 && Math.abs(rs)>1e-7) {
                    estimatedWidth = Math.abs((m[3]*rx+m[4]*pixelY+m[5])/rs
                            -(m[3]*lx+m[4]*pixelY+m[5])/ls);
                }
                if (!Double.isFinite(estimatedWidth)) estimatedWidth=1.88d;
                estimatedWidth=CameraVehicleTracker.clamp(estimatedWidth,0.6d,3.5d);
                double aspect=box.height()*sourceHeight*image.getWidth()
                        / (box.width()*sourceWidth*image.getHeight());
                double estimatedHeight=CameraVehicleTracker.clamp(estimatedWidth*aspect,0.6d,4d);
                observations.add(new CameraVehicleTracker.Box(distance,lateral,
                        estimatedWidth,estimatedHeight,
                        box.left/image.getWidth(),box.top/image.getHeight(),
                        box.right/image.getWidth(),box.bottom/image.getHeight(),
                        vehicle.getScore(),vehicleType));
                if (observations.size() >= MAX_RESULTS) {
                    break;
                }
            }
            return trackedOutput(observations, frameTime);
        } finally {
            image.recycle();
        }
    }

    private JSONArray trackedOutput(ArrayList<CameraVehicleTracker.Box> observations,
                                    long frameTime) throws Exception {
        JSONArray output=new JSONArray();
        for (CameraVehicleTracker.Track t:tracker.update(observations,frameTime)) {
            long age=Math.max(0L,frameTime-t.time);
            double fade=1d-0.72d*Math.min(1d,age/450d);
            JSONObject object=new JSONObject();
            object.put("id",t.id);
            object.put("d",t.box.d);
            object.put("y",t.box.y);
            object.put("p",t.box.score*fade);
            object.put("src","P");
            object.put("type",t.box.type);
            object.put("width",t.box.width);
            object.put("height",t.box.height);
            object.put("vd",t.vd);
            object.put("vy",t.vy);
            object.put("seen",t.time);
            output.put(object);
        }
        return output;
    }

    void reset() { tracker.clear(); }

    private static Category bestVehicleCategory(List<Category> categories) {
        Category best = null;
        for (Category category : categories) {
            String label = normalizeVehicleType(category.getLabel());
            boolean vehicle = label.equals("car") || label.equals("truck")
                    || label.equals("bus") || label.equals("motorcycle")
                    || label.equals("bicycle") || label.equals("person");
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
        tracker.clear();
        detector.close();
    }
}
