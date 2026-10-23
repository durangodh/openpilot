package ai.comma.remotehud;

/**
 * Keeps the arrival clock stable between actual navigation updates.
 *
 * Navigation bridges resend their latest route snapshot every second. Some
 * apps (notably NAVER while stationary) keep the remaining duration unchanged
 * until guidance advances. Recalculating {@code now + remaining} for every
 * duplicate packet makes the displayed arrival time drift later once a minute.
 */
final class NavigationEtaClock {
    private long arrivalWallMs;
    private int lastRemainSeconds = -1;
    private int lastNavApp = -1;

    long update(boolean active, int remainSeconds, int navApp, long nowWallMs) {
        if (!active || remainSeconds <= 0) {
            reset();
            return 0L;
        }

        if (arrivalWallMs == 0L
                || remainSeconds != lastRemainSeconds
                || navApp != lastNavApp) {
            arrivalWallMs = nowWallMs + remainSeconds * 1000L;
            lastRemainSeconds = remainSeconds;
            lastNavApp = navApp;
        }
        return arrivalWallMs;
    }

    private void reset() {
        arrivalWallMs = 0L;
        lastRemainSeconds = -1;
        lastNavApp = -1;
    }
}
