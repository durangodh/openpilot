package ai.comma.remotehud;

/** Conservative image-based theme estimate shared by NAVER and TMAP.
 * This is not the navigation app's authoritative night-mode flag.
 *
 * A settled theme is held for HOLD_MS after the last confident frame, so a
 * map that pauses for a while (red light, renderer idle, app switch) does not
 * flip the drive panel back to the time-of-day fallback and forth again.
 */
final class MapThemePolicy {
    static final int UNKNOWN = -1, DAY = 0, NIGHT = 1;
    static final long HOLD_MS = 60_000L;
    /** Frames of one theme needed before switching to it. */
    static final long SETTLE_MS = 1500L;
    /**
     * In daytime the navi app goes to its night map in every tunnel. Switching the HUD with it
     * made short tunnels flip the whole panel back and forth, so in daytime the HUD only follows
     * a night map that lasts this long (2026-11-07 request: 15 s). Back to day stays SETTLE_MS.
     */
    static final long DAYTIME_NIGHT_MS = 15_000L;
    private int stable = UNKNOWN, candidate = UNKNOWN;
    private long candidateSince, lastConfident = -1;

    synchronized void reset() {
        stable = candidate = UNKNOWN;
        lastConfident = -1;
    }

    static int classify(int[] pixels) {
        int neutral = 0, bright = 0, dark = 0, black = 0;
        int min = 255, max = 0;
        for (int pixel : pixels) {
            int r = (pixel >> 16) & 255, g = (pixel >> 8) & 255, b = pixel & 255;
            int hi = Math.max(r, Math.max(g, b)), lo = Math.min(r, Math.min(g, b));
            int y = (54 * r + 183 * g + 19 * b) >> 8;
            min = Math.min(min, y);
            max = Math.max(max, y);
            if (y < 8) black++;
            // Ignore colored traffic, route lines, parks and water.
            if (hi - lo > 65 || y < 8) continue;
            neutral++;
            if (y >= 170) bright++;
            if (y <= 85) dark++;
        }
        // Reject blank/loading frames and maps dominated by imagery.
        if (pixels.length == 0 || max - min < 12 || black * 2 >= pixels.length
                || neutral * 100 < pixels.length * 45) return UNKNOWN;
        if (bright * 100 >= neutral * 70) return DAY;
        if (dark * 100 >= neutral * 70) return NIGHT;
        return UNKNOWN;
    }

    void observe(int value, long now) {
        observe(value, now, false);
    }

    /** daytime: it is day by the sun, so a night map (tunnel) must last DAYTIME_NIGHT_MS. */
    synchronized void observe(int value, long now, boolean daytime) {
        if (lastConfident >= 0 && now - lastConfident > HOLD_MS) reset();
        if (value == UNKNOWN) {
            candidate = UNKNOWN;
            return;
        }
        lastConfident = now;
        if (candidate != value) {
            candidate = value;
            candidateSince = now;
        } else if (now - candidateSince >= (value == NIGHT && daytime ? DAYTIME_NIGHT_MS : SETTLE_MS)) {
            stable = value;
        }
    }

    synchronized int current(long now) {
        return lastConfident >= 0 && now - lastConfident <= HOLD_MS ? stable : UNKNOWN;
    }
}
