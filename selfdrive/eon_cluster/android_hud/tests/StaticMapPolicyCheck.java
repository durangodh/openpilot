package ai.comma.remotehud;

public final class StaticMapPolicyCheck {
    private static void check(boolean value, String message) {
        if (!value) throw new AssertionError(message);
    }

    public static void main(String[] args) {
        long now = 10000L;
        check(StaticMapPolicy.shouldRefresh(false, Double.NaN, Double.NaN,
                0L, 37.0, 127.0, 0.0, now), "first frame while stopped");
        check(!StaticMapPolicy.shouldRefresh(true, 37.0, 127.0,
                0L, 37.0001, 127.0, 30.0, now), "under 50 metres");
        check(!StaticMapPolicy.shouldRefresh(true, 37.0, 127.0,
                9000L, 37.001, 127.0, 30.0, now), "minimum interval");
        check(!StaticMapPolicy.shouldRefresh(true, 37.0, 127.0,
                0L, 37.001, 127.0, 0.0, now), "no refresh while stopped");
        check(StaticMapPolicy.shouldRefresh(true, 37.0, 127.0,
                0L, 37.001, 127.0, 30.0, now), "moving beyond 50 metres");
        check(!StaticMapPolicy.validPose(0.0, 0.0), "zero pose rejected");
        System.out.println("Static Map refresh policy checks passed");
    }
}
