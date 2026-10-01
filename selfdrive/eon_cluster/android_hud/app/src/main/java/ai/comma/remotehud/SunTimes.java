package ai.comma.remotehud;

/**
 * Day/night by solar elevation, used when the map image cannot tell the HUD
 * theme. The HUD is used in Korea, so a fixed central-Korea point is within a
 * few minutes of local sunrise/sunset. (Same as the Kakao module's copy.)
 */
final class SunTimes {
    static final double LAT = 36.5, LON = 127.8;
    // Standard sunrise/sunset definition (refraction + solar radius).
    static final double HORIZON_DEG = -0.833;

    private SunTimes() { }

    static boolean isNight(long utcMillis) {
        return elevationDeg(utcMillis, LAT, LON) < HORIZON_DEG;
    }

    /** Low-precision solar elevation (about 0.1 degree), NOAA/USNO formulas. */
    static double elevationDeg(long utcMillis, double latDeg, double lonDeg) {
        double d = utcMillis / 86400000.0 + 2440587.5 - 2451545.0;
        double g = Math.toRadians(norm(357.529 + 0.98560028 * d));
        double q = norm(280.459 + 0.98564736 * d);
        double l = Math.toRadians(norm(q + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)));
        double e = Math.toRadians(23.439 - 0.00000036 * d);
        double ra = Math.toDegrees(Math.atan2(Math.cos(e) * Math.sin(l), Math.cos(l)));
        double dec = Math.asin(Math.sin(e) * Math.sin(l));
        double gmst = norm((18.697374558 + 24.06570982441908 * d) * 15.0);
        double ha = Math.toRadians(norm(gmst + lonDeg - ra));
        double lat = Math.toRadians(latDeg);
        return Math.toDegrees(Math.asin(Math.sin(lat) * Math.sin(dec)
                + Math.cos(lat) * Math.cos(dec) * Math.cos(ha)));
    }

    private static double norm(double deg) {
        double r = deg % 360.0;
        return r < 0 ? r + 360.0 : r;
    }
}
