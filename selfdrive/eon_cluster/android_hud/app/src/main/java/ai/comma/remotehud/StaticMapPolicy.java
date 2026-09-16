package ai.comma.remotehud;

/** Pure refresh policy kept separate so host-side tests need no Android runtime. */
final class StaticMapPolicy {
    static final double REFRESH_DISTANCE_METERS = 50.0;
    static final long MIN_REFRESH_INTERVAL_MS = 5000L;
    static final double MOVING_SPEED_KPH = 1.0;

    private StaticMapPolicy() {
    }

    static boolean validPose(double lat, double lon) {
        return Double.isFinite(lat) && Double.isFinite(lon)
                && lat >= -85.0 && lat <= 85.0 && lon >= -180.0 && lon <= 180.0
                && (Math.abs(lat) >= 0.5 || Math.abs(lon) >= 0.5);
    }

    static double distanceMeters(double lat1, double lon1, double lat2, double lon2) {
        double north = (lat2 - lat1) * 111320.0;
        double east = (lon2 - lon1) * 111320.0
                * Math.max(0.1, Math.cos(Math.toRadians((lat1 + lat2) * 0.5)));
        return Math.hypot(north, east);
    }

    static boolean shouldRefresh(boolean hasFrame, double lastLat, double lastLon,
                                 long lastFetchElapsed, double lat, double lon,
                                 double speedKph, long nowElapsed) {
        if (!validPose(lat, lon)) return false;
        if (!hasFrame || !validPose(lastLat, lastLon)) return true;
        if (speedKph < MOVING_SPEED_KPH) return false;
        if (nowElapsed - lastFetchElapsed < MIN_REFRESH_INTERVAL_MS) return false;
        return distanceMeters(lastLat, lastLon, lat, lon) >= REFRESH_DISTANCE_METERS;
    }
}
