package ai.comma.remotehud;

public final class NavigationEtaClockCheck {
    private static void equal(long expected, long actual, String message) {
        if (expected != actual) {
            throw new AssertionError(message + ": expected=" + expected + " actual=" + actual);
        }
    }

    public static void main(String[] args) {
        NavigationEtaClock clock = new NavigationEtaClock();
        long start = 1_000_000L;

        long originalArrival = clock.update(true, 19 * 60, 2, start);
        equal(start + 19 * 60_000L, originalArrival, "initial NAVER arrival");

        // Four minutes of identical heartbeat packets must not turn 13:37 into 13:41.
        equal(originalArrival, clock.update(true, 19 * 60, 2, start + 4 * 60_000L),
                "duplicate duration drifted");

        // A real duration update or route recalculation supplies a new estimate.
        equal(start + 4 * 60_000L + 16 * 60_000L,
                clock.update(true, 16 * 60, 2, start + 4 * 60_000L),
                "changed duration did not refresh arrival");

        // Switching navigation sources is a separate route context.
        equal(start + 5 * 60_000L + 16 * 60_000L,
                clock.update(true, 16 * 60, 3, start + 5 * 60_000L),
                "navigation source switch did not refresh arrival");

        equal(0L, clock.update(false, 16 * 60, 3, start + 6 * 60_000L),
                "inactive guidance did not clear arrival");
        equal(start + 7 * 60_000L + 16 * 60_000L,
                clock.update(true, 16 * 60, 3, start + 7 * 60_000L),
                "new guidance did not establish arrival");

        System.out.println("Navigation ETA clock: 6 checks passed");
    }
}
