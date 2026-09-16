package ai.comma.remotehud;

public final class HudGeometrySmoothingCheck {
    private static void close(float expected, float actual) {
        if (Math.abs(expected - actual) > 0.0001f) {
            throw new AssertionError("expected=" + expected + " actual=" + actual);
        }
    }

    public static void main(String[] args) {
        close(0.72f, HudGeometrySmoothing.lateralAlpha(0.40f, 10f));
        close(0.56f, HudGeometrySmoothing.lateralAlpha(0.40f, 62.5f));
        close(0.40f, HudGeometrySmoothing.lateralAlpha(0.40f, 90f));
        close(0.80f, HudGeometrySmoothing.lateralAlpha(0.80f, 10f));
        close(0.40f, HudGeometrySmoothing.lateralAlpha(0.40f, Float.NaN));
        System.out.println("HudGeometrySmoothingCheck: passed (near/mid/far lateral response)");
    }
}
