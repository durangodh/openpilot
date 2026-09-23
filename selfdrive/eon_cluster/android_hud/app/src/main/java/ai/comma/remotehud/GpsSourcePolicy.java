package ai.comma.remotehud;

import java.util.Locale;

/** Source of delivered GPS fixes, not the app's selected/fused navigation position. */
final class GpsSourcePolicy {
    static final int WAITING = 0, VEHICLE = 1, PHONE = 2, UNKNOWN = 3, MOCK = 4;
    static final int GREY = 0, GREEN = 1, AMBER = 2, RED = 3, DIM_GREEN = 4;
    static int iconTone(int source, boolean predicted, boolean car, long nowMs) {
        if (source == -1 || source == -3) return RED;
        if (source == UNKNOWN) return AMBER;
        if (car && source == VEHICLE) {
            // One cycle per second; dim green keeps the silhouette recognizable.
            return predicted && (nowMs / 500L) % 2L != 0 ? DIM_GREEN : GREEN;
        }
        return !car && source == PHONE ? GREEN : GREY;
    }
    static String symbol(int source) {
        return source == MOCK ? "M" : source == -2 ? "×" : source == WAITING ? "—" : "";
    }
    static int badgeWidth(int source) {
        return symbol(source).isEmpty() ? 106 : 154;
    }
    static String accuracyLabel(int source, boolean hasAccuracy, float meters) {
        if ((source != VEHICLE && source != PHONE && source != UNKNOWN)
                || !hasAccuracy || !Float.isFinite(meters) || meters <= 0f) return "";
        if (meters > 9999f) return ">9999m";
        return String.format(Locale.US, "%.1fm", meters);
    }
    static int classify(String provider, String source, int satellites, boolean mock) {
        if (!"gps".equals(provider)) return WAITING;
        if (mock) return MOCK;
        if ("vehicle".equals(source)) return VEHICLE;
        // Do not equate missing vehicle metadata with a handset fix.
        if ((source == null || source.isEmpty()) && satellites > 0) return PHONE;
        return UNKNOWN;
    }
    static int current(int source, long fixMs, long nowMs) {
        return fixMs <= 0 || nowMs < fixMs || nowMs - fixMs >= 3000 ? WAITING : source;
    }
    static String label(int source) {
        switch (source) {
            case VEHICLE: return "GPS 차량";
            case PHONE: return "GPS S9";
            case UNKNOWN: return "GPS 출처 미확인";
            case MOCK: return "GPS 모의 위치";
            default: return "GPS 수신 대기";
        }
    }
}
