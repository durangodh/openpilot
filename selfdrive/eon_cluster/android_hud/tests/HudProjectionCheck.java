package ai.comma.remotehud;

public final class HudProjectionCheck {
    public static void main(String[] args) {
        for (float scale : new float[]{2f, 10f, 40f}) {
            for (float y : new float[]{-7f, -3f, 0f, 3f, 7f}) {
                float normal = HudProjection.screenX(476f, y, scale, false);
                float flipped = HudProjection.screenX(476f, y, scale, true);
                if (normal + flipped != 952f
                        || (y > 0f && normal >= 476f)
                        || (y < 0f && normal <= 476f)
                        || (y == 0f && normal != 476f)) {
                    throw new AssertionError("Incorrect lateral projection: " + y);
                }
                if (HudProjection.screenX(476f, y, scale, false) != normal) {
                    throw new AssertionError("Flip toggle changed world coordinates");
                }
            }
        }
        System.out.println("HudProjectionCheck: passed (left/right/center, near/far, toggle)");
    }
}
