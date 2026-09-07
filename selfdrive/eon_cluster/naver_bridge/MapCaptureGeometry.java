package com.naver.map.carrot;

/** Keep one scale on both axes, then crop to the HUD's landscape viewport. */
final class MapCaptureGeometry {
    static final int WIDTH = 960;
    static final int HEIGHT = 576;

    static int[] destination(int width, int height, int percent) {
        double scale = Math.min(WIDTH / (double) width, HEIGHT / (double) height)
                * Math.max(50, Math.min(100, percent)) / 100d;
        int w = Math.max(1, (int) Math.round(width * scale));
        int h = Math.max(1, (int) Math.round(height * scale));
        int x = (WIDTH - w) / 2, y = (HEIGHT - h) / 2;
        return new int[]{x, y, x + w, y + h};
    }

    static int[] captureSize(int width, int height) {
        // Bound the readback allocation to 16 MiB, without upscaling source pixels.
        double scale = Math.min(1d, 2048d / Math.max(width, height));
        return new int[]{Math.max(1, (int) Math.round(width * scale)),
                Math.max(1, (int) Math.round(height * scale))};
    }

    static int[] crop(int width, int height) {
        double scale = Math.min(width / (double) WIDTH, height / (double) HEIGHT);
        int cropWidth = Math.max(1, (int) Math.round(WIDTH * scale));
        int cropHeight = Math.max(1, (int) Math.round(HEIGHT * scale));
        int left = (width - cropWidth) / 2;
        // Bias toward the lower map area used for the current-position marker.
        int top = Math.max(0, Math.min(height - cropHeight,
                (int) Math.round((height - cropHeight) * 0.64d)));
        return new int[]{left, top, left + cropWidth, top + cropHeight};
    }
}
