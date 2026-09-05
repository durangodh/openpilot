package ai.comma.remotehud;

/** Display-only lead refresh and sprite spacing. Never changes telemetry. */
final class LeadDisplayPolicy {
    private LeadDisplayPolicy() {}

    static boolean refreshNow(boolean newPacket, boolean styleChanged,
                              boolean leadChanged, long now, long nextRender) {
        return (newPacket || styleChanged || leadChanged)
                && (styleChanged || leadChanged || now >= nextRender);
    }

    static float separatedBottom(float leadX, float bottom, float width, float aspect,
                                 float egoX, float egoBottom, float egoWidth, float gap) {
        float leadHalfWidth = width * 0.60f; // includes source ground marker
        if (leadX + leadHalfWidth <= egoX - egoWidth * 0.5f
                || leadX - leadHalfWidth >= egoX + egoWidth * 0.5f) {
            return bottom;
        }
        float egoTop = egoBottom - egoWidth * aspect;
        float markerBelow = Math.max(1.5f, width * aspect * 0.15f);
        return Math.min(bottom, egoTop - gap - markerBelow);
    }
}
