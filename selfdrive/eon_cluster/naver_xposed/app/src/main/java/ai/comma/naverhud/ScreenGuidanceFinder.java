package ai.comma.naverhud;

/** Pixel coordinates are inclusive. No Android dependencies. */
final class ScreenGuidanceFinder {
    private ScreenGuidanceFinder() { }

    // Include the phone's distance label, preserving its original position and background.
    // Semantic bounds take precedence; older layouts place it to the right (<=4 lanes)
    // or above (>=5 lanes). No distance text is synthesized.
    static int[] laneWithDistance(int[] lane, int[] label, int w, int h, int lanes) {
        if (lane == null) return null;
        int height = lane[3] - lane[1] + 1;
        int[] r = lane.clone();
        if (label != null) {
            r[0] = Math.min(r[0], label[0]);
            r[1] = Math.min(r[1], label[1]);
            r[2] = Math.max(r[2], label[2]);
            r[3] = Math.max(r[3], label[3]);
        } else if (lanes <= 4) {
            r[2] += (int) Math.ceil(height * 2.4);
        } else {
            r[1] -= (int) Math.ceil(height * 0.9);
        }
        return pad(r, 3, w, h);
    }

    static int[] pad(int[] r, int p, int w, int h) {
        return new int[]{Math.max(0, r[0] - p), Math.max(0, r[1] - p),
                Math.min(w - 1, r[2] + p), Math.min(h - 1, r[3] + p)};
    }

    private static int color(int p) {
        int r = (p >> 16) & 255, g = (p >> 8) & 255, b = p & 255;
        if ((p >>> 24) < 200) return 0;
        if (r > 145 && g < r * .65 && b < r * .65) return 1;
        if (r > 150 && g > 105 && b < Math.min(r, g) * .65) return 2;
        if (g > 115 && r < g * .8 && b < g * .9) return 3;
        return 0;
    }

    /** Find a colored lamp enclosed by a compact dark housing. Reject route lines,
     * large night-map backgrounds and colored map markers lacking a signal housing.
     * Semantic signal bounds, when available, are used instead by the caller. */
    static int[] signal(int[] px, int w, int h) {
        boolean[] seen = new boolean[px.length];
        int[] queue = new int[px.length];
        int[] best = null;
        int bestArea = Integer.MAX_VALUE;
        for (int start = 0; start < px.length; start++) {
            int phase = color(px[start]);
            if (seen[start] || phase == 0) continue;
            int read = 0, count = 1;
            queue[0] = start;
            seen[start] = true;
            int x0 = start % w, x1 = x0, y0 = start / w, y1 = y0;
            while (read < count) {
                int i = queue[read++], x = i % w, y = i / w;
                x0 = Math.min(x0, x); x1 = Math.max(x1, x);
                y0 = Math.min(y0, y); y1 = Math.max(y1, y);
                for (int d = 0; d < 4; d++) {
                    int nx = x + (d == 0 ? -1 : d == 1 ? 1 : 0);
                    int ny = y + (d == 2 ? -1 : d == 3 ? 1 : 0);
                    if (nx < 0 || nx >= w || ny < 0 || ny >= h) continue;
                    int ni = ny * w + nx;
                    if (!seen[ni] && color(px[ni]) == phase) {
                        seen[ni] = true; queue[count++] = ni;
                    }
                }
            }
            int bw = x1 - x0 + 1, bh = y1 - y0 + 1;
            if (bw < 5 || bh < 5 || bh > h / 9 || bw > w / 9
                    || bw < bh * .55 || bw > bh * 1.8 || count < bw * bh * .25) continue;
            int[] housing = housing(px, w, h, x0, y0, x1, y1, queue);
            if (housing == null) continue;
            int hw = housing[2] - housing[0] + 1, hh = housing[3] - housing[1] + 1;
            // A row of lamps, rather than a single colored POI on a square badge.
            if (hw < bw * 2.5 || hw > bw * 12 || hh < bh || hh > bh * 3.5
                    || hw < hh * 1.6 || hw > hh * 9) continue;
            int area = hw * hh;
            if (area < bestArea) { bestArea = area; best = housing; }
        }
        return best == null ? null : pad(best, 3, w, h);
    }

    private static int[] housing(int[] px, int w, int h, int x0, int y0, int x1, int y1, int[] queue) {
        int seed = -1;
        for (int y = Math.max(0, y0 - 3); y <= y1 + 3 && y < h && seed < 0; y++) {
            for (int x = Math.max(0, x0 - 3); x <= x1 + 3 && x < w; x++) {
                if ((x < x0 || x > x1 || y < y0 || y > y1) && LaneBoxFinder.dark(px[y * w + x])) {
                    seed = y * w + x; break;
                }
            }
        }
        if (seed < 0) return null;
        int limit = Math.min(px.length, Math.max(500, w * h / 18));
        java.util.HashSet<Integer> seen = new java.util.HashSet<>();
        int read = 0, count = 1;
        queue[0] = seed; seen.add(seed);
        int left = seed % w, right = left, top = seed / w, bottom = top;
        while (read < count) {
            int i = queue[read++], x = i % w, y = i / w;
            left = Math.min(left, x); right = Math.max(right, x);
            top = Math.min(top, y); bottom = Math.max(bottom, y);
            for (int d = 0; d < 4; d++) {
                int nx = x + (d == 0 ? -1 : d == 1 ? 1 : 0);
                int ny = y + (d == 2 ? -1 : d == 3 ? 1 : 0);
                if (nx < 0 || nx >= w || ny < 0 || ny >= h) continue;
                int ni = ny * w + nx;
                if (LaneBoxFinder.dark(px[ni]) && seen.add(ni)) {
                    if (count >= limit) return null;
                    queue[count++] = ni;
                }
            }
        }
        if (left > x0 || right < x1 || top > y0 || bottom < y1) return null;
        return new int[]{left, top, right, bottom};
    }
}
