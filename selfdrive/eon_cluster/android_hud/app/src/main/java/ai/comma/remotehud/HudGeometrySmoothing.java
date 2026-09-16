package ai.comma.remotehud;

/** Distance-aware smoothing for model-world lateral geometry. */
final class HudGeometrySmoothing {
    private static final float NEAR_END_M = 35f;
    private static final float FAR_START_M = 90f;
    private static final float NEAR_LATERAL_ALPHA = 0.72f;

    private HudGeometrySmoothing() {}

    static float lateralAlpha(float baseAlpha, float forwardDistance) {
        float base = Math.max(0f, Math.min(1f, baseAlpha));
        if (!Float.isFinite(forwardDistance) || forwardDistance >= FAR_START_M) {
            return base;
        }

        float near = Math.max(base, NEAR_LATERAL_ALPHA);
        if (forwardDistance <= NEAR_END_M) {
            return near;
        }

        float nearWeight = (FAR_START_M - forwardDistance) / (FAR_START_M - NEAR_END_M);
        return base + (near - base) * nearWeight;
    }
}
