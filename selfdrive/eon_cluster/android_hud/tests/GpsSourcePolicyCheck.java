package ai.comma.remotehud;

public final class GpsSourcePolicyCheck {
    private static void check(boolean result) { if (!result) throw new AssertionError(); }
    public static void main(String[] args) {
        check(GpsSourcePolicy.classify("gps", "vehicle", 0, false) == GpsSourcePolicy.VEHICLE);
        check(GpsSourcePolicy.classify("gps", null, 8, false) == GpsSourcePolicy.PHONE);
        check(GpsSourcePolicy.classify("gps", null, 0, false) == GpsSourcePolicy.UNKNOWN);
        check(GpsSourcePolicy.classify("gps", "other", 8, false) == GpsSourcePolicy.UNKNOWN);
        check(GpsSourcePolicy.classify("network", "vehicle", 0, false) == GpsSourcePolicy.WAITING);
        check(GpsSourcePolicy.classify("fused", null, 8, false) == GpsSourcePolicy.WAITING);
        check(GpsSourcePolicy.classify("gps", "vehicle", 8, true) == GpsSourcePolicy.MOCK);
        check(GpsSourcePolicy.current(1, 1000, 3999) == 1);
        check(GpsSourcePolicy.current(1, 1000, 4000) == 0);
        check(GpsSourcePolicy.current(2, 1000, 999) == 0);
        check(GpsSourcePolicy.current(2, 0, 1) == 0);
        for (boolean car : new boolean[]{true, false}) {
            check(GpsSourcePolicy.iconTone(-1, false, car, 0) == GpsSourcePolicy.RED);
            check(GpsSourcePolicy.iconTone(-3, false, car, 0) == GpsSourcePolicy.RED);
            check(GpsSourcePolicy.iconTone(3, false, car, 0) == GpsSourcePolicy.AMBER);
            check(GpsSourcePolicy.iconTone(0, true, car, 500) == GpsSourcePolicy.GREY);
        }
        check(GpsSourcePolicy.iconTone(1, true, true, 499) == GpsSourcePolicy.GREEN);
        check(GpsSourcePolicy.iconTone(1, true, true, 500) == GpsSourcePolicy.DIM_GREEN);
        check(GpsSourcePolicy.iconTone(1, true, true, 1000) == GpsSourcePolicy.GREEN);
        check(GpsSourcePolicy.iconTone(1, false, true, 500) == GpsSourcePolicy.GREEN);
        check(GpsSourcePolicy.iconTone(1, true, false, 500) == GpsSourcePolicy.GREY);
        check(GpsSourcePolicy.iconTone(2, true, false, 500) == GpsSourcePolicy.GREEN);
        check(GpsSourcePolicy.symbol(-1).isEmpty());
        check(GpsSourcePolicy.symbol(-3).isEmpty());
        check(GpsSourcePolicy.symbol(1).isEmpty());
        check(GpsSourcePolicy.symbol(3).isEmpty());
        for (int source : new int[]{-1, -3, 1, 2, 3}) check(GpsSourcePolicy.badgeWidth(source) == 106);
        for (int source : new int[]{0, -2, 4}) check(GpsSourcePolicy.badgeWidth(source) == 154);
        check(GpsSourcePolicy.accuracyLabel(1, true, 4.1f).equals("±5m"));
        check(GpsSourcePolicy.accuracyLabel(2, true, 5f).equals("±5m"));
        check(GpsSourcePolicy.accuracyLabel(3, true, 0.2f).equals("±1m"));
        check(GpsSourcePolicy.accuracyLabel(1, true, 10000f).equals("±>9999m"));
        for (int source : new int[]{0, -1, -2, -3, 4}) check(GpsSourcePolicy.accuracyLabel(source, true, 5f).isEmpty());
        for (float value : new float[]{0f, -1f, Float.NaN, Float.POSITIVE_INFINITY})
            check(GpsSourcePolicy.accuracyLabel(1, true, value).isEmpty());
        check(GpsSourcePolicy.accuracyLabel(1, false, 5f).isEmpty());
        System.out.println("GPS source: 51 checks passed");
    }
}
