package ai.comma.remotehud;

/**
 * Visual-only OSM road matcher.
 *
 * <p>OSM geometry arrives in the vehicle frame (forward x, left y), but raw
 * navigation GPS and heading can move the entire environment several metres
 * sideways or rotate it away from the camera road.  Find the nearby OSM road
 * segment most compatible with the vehicle heading and return one rigid
 * correction for every OSM feature.  Nothing from this class is fed back to
 * planning or control.</p>
 */
final class OsmRoadMatcher {
    private static final float HALF_PI = (float) (Math.PI * 0.5);
    private static final float PI = (float) Math.PI;
    private static final float MAX_HEADING_ERROR = (float) Math.toRadians(32d);
    private static final float MAX_LATERAL_SHIFT = 15f;
    private static final float MIN_SEGMENT_LENGTH = 5f;

    static final class Match {
        final float yawCorrection;
        final float lateralShift;
        final float distance;
        final float headingError;

        Match(float yawCorrection, float lateralShift, float distance,
              float headingError) {
            this.yawCorrection = yawCorrection;
            this.lateralShift = lateralShift;
            this.distance = distance;
            this.headingError = headingError;
        }
    }

    private OsmRoadMatcher() {}

    /**
     * Match the current position to a nearby, similarly-directed OSM road.
     * targetRoadY is the camera-estimated centre of the whole road relative
     * to the ego vehicle.  For a two-lane road while driving in lane 1 it is
     * about -1.75 m, not zero, so map matching does not drag the road centre
     * underneath the car.
     */
    static Match find(float[][] roadX, float[][] roadY, float[] roadW,
                      float targetRoadY, float targetRoadWidth) {
        if (roadX == null || roadY == null) {
            return null;
        }
        boolean haveTarget = Float.isFinite(targetRoadY);
        boolean haveTargetWidth = Float.isFinite(targetRoadWidth);
        float bestScore = Float.MAX_VALUE;
        Match best = null;
        int roads = Math.min(roadX.length, roadY.length);
        for (int r = 0; r < roads; r++) {
            float[] xs = roadX[r];
            float[] ys = roadY[r];
            if (xs == null || ys == null) {
                continue;
            }
            float width = roadW != null && r < roadW.length ? roadW[r] : 5f;
            int points = Math.min(xs.length, ys.length);
            for (int i = 0; i < points - 1; i++) {
                float dx = xs[i + 1] - xs[i];
                float dy = ys[i + 1] - ys[i];
                float length2 = dx * dx + dy * dy;
                if (length2 < MIN_SEGMENT_LENGTH * MIN_SEGMENT_LENGTH) {
                    continue;
                }
                float angle = directionlessAngle((float) Math.atan2(dy, dx));
                float headingError = Math.abs(angle);
                if (headingError > MAX_HEADING_ERROR) {
                    continue;
                }

                float t = -(xs[i] * dx + ys[i] * dy) / length2;
                t = Math.max(0f, Math.min(1f, t));
                float nearX = xs[i] + dx * t;
                float nearY = ys[i] + dy * t;
                float distance = (float) Math.hypot(nearX, nearY);
                float maximumDistance = Math.min(20f, Math.max(11f, width + 8f));
                if (distance > maximumDistance) {
                    continue;
                }

                float correction = -angle;
                float cos = (float) Math.cos(correction);
                float sin = (float) Math.sin(correction);
                float matchedY = nearX * sin + nearY * cos;
                float lateralShift = haveTarget ? targetRoadY - matchedY : 0f;
                if (Math.abs(lateralShift) > MAX_LATERAL_SHIFT) {
                    continue;
                }

                // Prefer the line requiring the smallest map correction. A
                // heading penalty rejects crossing roads at intersections;
                // the small width bonus favours the main road over a parallel
                // service road when both are otherwise equally plausible.
                float lateralError = haveTarget ? Math.abs(lateralShift) : distance;
                float widthError = haveTargetWidth
                        ? Math.abs(width - targetRoadWidth) * 4f : 0f;
                float score = lateralError + headingError * 15f + widthError
                        + Math.abs(nearX) * 0.08f - Math.min(10f, width) * 0.08f;
                if (score < bestScore) {
                    bestScore = score;
                    best = new Match(correction, lateralShift, distance, headingError);
                }
            }
        }
        return best;
    }

    private static float directionlessAngle(float angle) {
        while (angle > HALF_PI) {
            angle -= PI;
        }
        while (angle < -HALF_PI) {
            angle += PI;
        }
        return angle;
    }

    static void transformPolylines(float[][] xs, float[][] ys,
                                   float yawCorrection, float lateralShift) {
        if (xs == null || ys == null) {
            return;
        }
        int count = Math.min(xs.length, ys.length);
        for (int i = 0; i < count; i++) {
            transformPoints(xs[i], ys[i], yawCorrection, lateralShift);
        }
    }

    static void transformPoints(float[] xs, float[] ys,
                                float yawCorrection, float lateralShift) {
        if (xs == null || ys == null) {
            return;
        }
        float cos = (float) Math.cos(yawCorrection);
        float sin = (float) Math.sin(yawCorrection);
        int count = Math.min(xs.length, ys.length);
        for (int i = 0; i < count; i++) {
            float x = xs[i];
            float y = ys[i];
            xs[i] = x * cos - y * sin;
            ys[i] = x * sin + y * cos + lateralShift;
        }
    }
}
