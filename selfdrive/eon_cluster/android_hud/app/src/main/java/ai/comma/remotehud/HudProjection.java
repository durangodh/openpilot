package ai.comma.remotehud;

/** All world geometry is left-positive; reflect only at the screen boundary. */
final class HudProjection {
    private HudProjection() {}

    static float screenX(float center, float lateral, float scale, boolean flip) {
        return center + (flip ? lateral : -lateral) * scale;
    }
}
