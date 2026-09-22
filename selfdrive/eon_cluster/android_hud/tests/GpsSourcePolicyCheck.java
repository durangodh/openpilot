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
        System.out.println("GPS source: 29 checks passed");
    }
}
