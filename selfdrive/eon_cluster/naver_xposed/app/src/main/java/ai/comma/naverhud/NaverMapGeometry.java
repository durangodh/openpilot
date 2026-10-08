package ai.comma.naverhud;

/** Pixel geometry for placing a phone-map snapshot in the tall 12.3-inch HUD map panel. */
final class NaverMapGeometry {
    static final int PANEL_WIDTH = 760;
    static final int PANEL_HEIGHT = 720;
    static final int ETA_HEIGHT = 58;
    // The 1034x720 Naver navigation surface is almost exactly this aspect ratio.
    // Keeping it prevents the 12.3-inch square-ish panel from turning a centre crop
    // into an apparent 1.6x zoom.  The unused top is covered by the turn banner.
    static final int MAP_HEIGHT = 530;
    static final int MAP_TOP = PANEL_HEIGHT - ETA_HEIGHT - MAP_HEIGHT;
    static final int MAP_BOTTOM = MAP_TOP + MAP_HEIGHT;

    private NaverMapGeometry() {
    }

    static boolean useInset(boolean guiding, String mapSource) {
        return guiding && "guidance".equals(mapSource);
    }

    /** Source rectangle with PANEL_WIDTH:MAP_HEIGHT aspect, centred on the navigation camera. */
    static int[] sourceCrop(int width, int height) {
        if (width <= 0 || height <= 0) return new int[]{0, 0, 0, 0};
        int cropWidth = width;
        int cropHeight = Math.round(width * MAP_HEIGHT / (float) PANEL_WIDTH);
        if (cropHeight > height) {
            cropHeight = height;
            cropWidth = Math.min(width, Math.round(height * PANEL_WIDTH / (float) MAP_HEIGHT));
        }
        int centerX = width / 2;
        int centerY = Math.round(height * (height > width ? 0.62f : 0.5f));
        int left = Math.max(0, Math.min(width - cropWidth, centerX - cropWidth / 2));
        int top = Math.max(0, Math.min(height - cropHeight, centerY - cropHeight / 2));
        return new int[]{left, top, left + cropWidth, top + cropHeight};
    }
}
