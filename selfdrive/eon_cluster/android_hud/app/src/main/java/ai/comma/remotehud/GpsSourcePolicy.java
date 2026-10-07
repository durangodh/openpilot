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
    /**
     * 위치 전달 지연(초) 표시. 2026-10-07: 차량 GPS(nMirror)의 정확도 3.0m 와 달리 실제로는
     * 약 10m 늦게 잡혔다. 정확도에는 전달 지연이 들어 있지 않으므로, 측정 시각(getTime, UTC)과
     * 받은 시각의 차이 중앙값을 따로 보여 준다. 0~5초 밖이면 폰 시계가 맞지 않거나 nMirror 가
     * 측정 시각을 넘기지 않는 것이라 표시하지 않는다.
     */
    static String latencyLabel(long medianMs) {
        if (medianMs < 0L || medianMs > 5000L) return "";
        return String.format(java.util.Locale.US, "%.1fs", medianMs / 1000.0);
    }

    /** 최근 지연 표본의 중앙값(ms). 표본이 없으면 -1. */
    static long median(long[] samples, int count) {
        if (count <= 0) return -1L;
        long[] copy = java.util.Arrays.copyOf(samples, count);
        java.util.Arrays.sort(copy);
        return copy[count / 2];
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
