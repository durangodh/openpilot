package ai.comma.remotehud;

/**
 * Visual-only OSM road matcher.
 *
 * <p>OSM geometry arrives in the vehicle frame (forward x, left y), but raw
 * navigation GPS and heading can move the environment sideways or rotate it
 * away from the camera road. Find one nearby road compatible with the
 * camera-estimated road centre and return a rigid yaw/lateral correction.
 * Longitudinal translation is intentionally not estimated: a continuous road
 * centreline contains no landmark that can identify forward GPS error.</p>
 */
final class OsmRoadMatcher {
    private static final float HALF_PI = (float) (Math.PI * 0.5);
    private static final float PI = (float) Math.PI;
    private static final float MAX_HEADING_ERROR = (float) Math.toRadians(32d);
    private static final float MAX_LATERAL_SHIFT = 15f;
    private static final float MAX_MATCH_SCORE = 22f;
    private static final float MIN_SEGMENT_LENGTH = 5f;

    static final class Match {
        final int roadIndex;
        final float yawCorrection;
        final float lateralShift;
        final float distance;
        final float headingError;
        final float score;

        Match(int roadIndex, float yawCorrection, float lateralShift,
              float distance, float headingError, float score) {
            this.roadIndex = roadIndex;
            this.yawCorrection = yawCorrection;
            this.lateralShift = lateralShift;
            this.distance = distance;
            this.headingError = headingError;
            this.score = score;
        }
    }

    private OsmRoadMatcher() {}

    /**
     * Match the current position to a nearby, similarly-directed OSM road.
     * targetRoadY is the camera-estimated centre of the whole road relative
     * to ego. Without that camera anchor a nearest OSM line cannot safely
     * distinguish the driven carriageway from a parallel or opposite road.
     */
    static Match find(float[][] roadX, float[][] roadY, float[] roadW,
                      int[] roadMatch, long[] roadIds, long preferredRoadId,
                      float targetRoadY, float targetRoadWidth) {
        if (roadX == null || roadY == null || !Float.isFinite(targetRoadY)) {
            return null;
        }
        boolean haveTargetWidth = Float.isFinite(targetRoadWidth);
        float bestScore = Float.MAX_VALUE;
        Match best = null;
        int roads = Math.min(roadX.length, roadY.length);
        for (int r = 0; r < roads; r++) {
            // Minor pedestrian/cycle geometry remains available to World3D,
            // but must never anchor vehicle-road map alignment.
            if (roadMatch != null && r < roadMatch.length && roadMatch[r] == 0) {
                continue;
            }
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
                float lateralShift = targetRoadY - matchedY;
                if (Math.abs(lateralShift) > MAX_LATERAL_SHIFT) {
                    continue;
                }

                // Width is only an estimate in OSM, so heading/lateral fit
                // dominate. Keeping the previous OSM way gives hysteresis on
                // parallel roads while still allowing a clearly better road.
                float widthError = haveTargetWidth
                        ? Math.abs(width - targetRoadWidth) * 2f : 0f;
                float continuityBonus = roadIds != null && r < roadIds.length
                        && roadIds[r] == preferredRoadId ? 4f : 0f;
                float score = Math.abs(lateralShift) + headingError * 15f
                        + widthError + Math.abs(nearX) * 0.08f
                        - Math.min(10f, width) * 0.08f - continuityBonus;
                if (score < bestScore) {
                    bestScore = score;
                    best = new Match(r, correction, lateralShift,
                            distance, headingError, score);
                }
            }
        }
        return best != null && best.score <= MAX_MATCH_SCORE ? best : null;
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
