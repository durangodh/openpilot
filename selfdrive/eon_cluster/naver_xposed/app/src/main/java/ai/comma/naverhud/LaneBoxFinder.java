package ai.comma.naverhud;

/**
 * 네이버 6.12 화면 캡처에서 차로 상자(검은 둥근 사각형 + 화살표)를 찾는다. 안드로이드 API 를 쓰지
 * 않는 순수 계산이라 PC 에서도 시험할 수 있다.
 *
 * 줄마다 어두운 픽셀이 길게 이어진 구간을 찾고, 위아래로 같은 좌우 끝을 가진 구간이 쌓이면 상자로
 * 본다. 화살표가 있는 줄은 구간이 끊기지만 상자 폭 안이 절반 이상 어두우면 상자 안으로 친다.
 * 위쪽 둥근 모서리 때문에 처음 몇 줄은 좁으므로, 막 시작한 상자는 넓어지는 것을 허용한다.
 * 후보 중 차로 수에 맞는 가로세로 비율(차로 한 칸 ≈ 정사각형)이고 안에 밝은 화살표가 있는 것을 고른다.
 */
final class LaneBoxFinder {
    private LaneBoxFinder() {
    }

    static boolean dark(int p) {
        int a = p >>> 24, r = (p >> 16) & 255, g = (p >> 8) & 255, b = p & 255;
        int mx = Math.max(r, Math.max(g, b)), mn = Math.min(r, Math.min(g, b));
        return a > 200 && mx <= 85 && mx - mn <= 22;
    }

    private static boolean bright(int p) {
        int r = (p >> 16) & 255, g = (p >> 8) & 255, b = p & 255;
        return (p >>> 24) > 200 && Math.max(r, Math.max(g, b)) >= 120;
    }

    private static final class Box {
        int x0, x1, y0, yLast, full, miss;
    }

    /** {left, top, right, bottom}(포함) 또는 못 찾으면 null. */
    static int[] find(int[] px, int w, int h, int lanes) {
        return find(px, w, h, lanes, null);
    }

    /**
     * anchors: distance text boxes {l, t, r, b} in the same pixels (Naver's own "59m" label from
     * NaverScreenSemantics). A candidate right next to one (label to its right, or above it for
     * 5+ lanes) is strongly preferred.
     */
    static int[] find(int[] px, int w, int h, int lanes, java.util.List<int[]> anchors) {
        if (lanes <= 0 || w <= 0 || h <= 0) return null;
        boolean[] d = new boolean[w * h];
        for (int i = 0; i < d.length; i++) d[i] = dark(px[i]);
        int minRun = Math.max(20, lanes * 12);
        java.util.ArrayList<Box> open = new java.util.ArrayList<>();
        int[] best = null;
        double bestScore = Double.MAX_VALUE;
        int[] runs = new int[w];
        for (int y = 0; y <= h; y++) {
            int nRuns = 0;
            if (y < h) {
                int row = y * w, x = 0;
                while (x < w) {
                    if (!d[row + x]) {
                        x++;
                        continue;
                    }
                    int s = x, e = x, gap = 0;
                    for (x = x + 1; x < w; x++) {
                        if (d[row + x]) {
                            e = x;
                            gap = 0;
                        } else if (++gap > 3) {
                            break;
                        }
                    }
                    if (e - s + 1 >= minRun && nRuns + 2 <= runs.length) {
                        runs[nRuns++] = s;
                        runs[nRuns++] = e;
                    }
                }
            }
            boolean[] used = new boolean[nRuns / 2];
            for (int k = open.size() - 1; k >= 0; k--) {
                Box b = open.get(k);
                boolean young = y - b.y0 < 14;
                int slack = young ? 26 : 6;
                int match = -1;
                for (int r = 0; r < nRuns / 2; r++) {
                    int s = runs[2 * r], e = runs[2 * r + 1];
                    if (s <= b.x0 + slack && e >= b.x1 - slack && s >= b.x0 - slack && e <= b.x1 + slack) {
                        match = r;
                        break;
                    }
                }
                if (match >= 0) {
                    used[match] = true;
                    if (young) {
                        b.x0 = Math.min(b.x0, runs[2 * match]);
                        b.x1 = Math.max(b.x1, runs[2 * match + 1]);
                    }
                    b.full++;
                    b.yLast = y;
                    b.miss = 0;
                    continue;
                }
                if (y < h) {
                    int cnt = 0, row = y * w;
                    for (int x = b.x0; x <= b.x1; x++) if (d[row + x]) cnt++;
                    if (cnt >= 0.45 * (b.x1 - b.x0 + 1)) {
                        b.yLast = y;
                        b.miss = 0;
                        continue;
                    }
                }
                if (++b.miss > 1 || y >= h) {
                    open.remove(k);
                    double score = score(b, px, d, w, h, lanes) + anchorBonus(b, anchors);
                    if (score < bestScore) {
                        bestScore = score;
                        best = new int[]{b.x0, b.y0, b.x1, b.yLast};
                    }
                }
            }
            for (int r = 0; r < nRuns / 2; r++) {
                if (used[r]) continue;
                Box b = new Box();
                b.x0 = runs[2 * r];
                b.x1 = runs[2 * r + 1];
                b.y0 = b.yLast = y;
                b.full = 1;
                open.add(b);
            }
        }
        return best;
    }

    /** 낮을수록 좋다. 조건에 안 맞으면 MAX_VALUE. */
    private static double score(Box b, int[] px, boolean[] d, int w, int h, int lanes) {
        int bw = b.x1 - b.x0 + 1, bh = b.yLast - b.y0 + 1;
        if (bh < 14 || b.full < 3 || bw > 0.95 * w) return Double.MAX_VALUE;
        double aspect = bw / (double) bh;
        if (aspect < 0.55 * lanes || aspect > 1.7 * lanes) return Double.MAX_VALUE;
        int darkN = 0, brightN = 0;
        for (int y = b.y0; y <= b.yLast; y++) {
            int row = y * w;
            for (int x = b.x0; x <= b.x1; x++) {
                if (d[row + x]) darkN++;
                else if (bright(px[row + x])) brightN++;
            }
        }
        double area = bw * (double) bh;
        if (darkN < 0.5 * area || brightN < 0.015 * area) return Double.MAX_VALUE;
        // Arrows, not digits (2026-11-22: another attempt cropped a dark "30" speed plaque). Every
        // lane cell holds an arrow whose lower part is one narrow vertical stem: in the rows at 60-85 %
        // of the box height the cell's bright pixels form a single run at most 30 % of the cell wide.
        // A "0" has two sides there, a "3" or "2" a wide bottom stroke.
        double cellW = bw / (double) lanes;
        int stems = 0;
        for (int k = 0; k < lanes; k++) {
            int cx0 = b.x0 + (int) Math.round(k * cellW), cx1 = b.x0 + (int) Math.round((k + 1) * cellW) - 1;
            int rows = 0, lit = 0, narrow = 0;
            for (int y = b.y0 + (int) (0.60 * bh); y <= b.y0 + (int) (0.85 * bh); y++) {
                rows++;
                int runs = 0, first = -1, last = -1;
                boolean prev = false;
                for (int x = cx0; x <= cx1; x++) {
                    boolean on = !d[y * w + x] && bright(px[y * w + x]);
                    if (on) {
                        if (!prev) runs++;
                        if (first < 0) first = x;
                        last = x;
                    }
                    prev = on;
                }
                if (runs == 0) continue;
                lit++;
                if (runs == 1 && last - first + 1 <= 0.30 * cellW) narrow++;
            }
            if (rows > 0 && lit >= 0.5 * rows && narrow >= 0.8 * lit) stems++;
        }
        if (stems < Math.max(1, lanes - 1)) return Double.MAX_VALUE;
        double s = Math.abs(Math.log(aspect / (0.95 * lanes)));
        // Naver's own lane box sits in the lower part of the screen; the same box can also float on
        // the map near the junction (prefer the fixed one).
        if (b.y0 < 0.45 * h) s += 0.5;
        return s;
    }

    private static double anchorBonus(Box b, java.util.List<int[]> anchors) {
        return label(new int[]{b.x0, b.y0, b.x1, b.yLast}, anchors) != null ? -1.0 : 0.0;
    }

    /** The distance label belonging to box {l, t, r, b}: right next to it or just above, or null. */
    static int[] label(int[] box, java.util.List<int[]> anchors) {
        if (box == null || anchors == null) return null;
        int bh = box[3] - box[1] + 1;
        for (int[] a : anchors) {
            int cy = (a[1] + a[3]) / 2;
            boolean right = a[0] >= box[2] - 2 && a[0] - box[2] <= bh && cy >= box[1] - bh / 3 && cy <= box[3] + bh / 3;
            boolean above = a[3] <= box[1] + 2 && box[1] - a[3] <= bh && a[0] <= box[2] && a[2] >= box[0];
            if (right || above) return a;
        }
        return null;
    }

    /** 이미 찾은 상자를 다시 떠 온 그림이 아직 상자인지: 위 띠가 어둡고 전체가 반 이상 어둡다. */
    static boolean stillBox(int[] px, int w, int h) {
        if (w < 8 || h < 8) return false;
        int top = 0, topN = 0, all = 0;
        for (int y = 0; y < h; y++) {
            for (int x = w / 8; x < w - w / 8; x++) {
                boolean dk = dark(px[y * w + x]);
                if (dk) all++;
                if (y >= h / 10 && y < h / 10 + 3) {
                    topN++;
                    if (dk) top++;
                }
            }
        }
        return top >= 0.7 * topN && all >= 0.45 * (h * (w - 2 * (w / 8)));
    }
}
