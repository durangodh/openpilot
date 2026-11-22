package ai.comma.naverhud;

import java.util.Arrays;

/** Small synthetic pixel fixtures; no Android runtime needed. */
public final class NaverScreenGuidanceCheck {
    static final int W = 400, H = 300;
    static int checks;
    static void check(boolean ok, String message) {
        checks++;
        if (!ok) throw new AssertionError(message);
    }
    static int[] image(int background) {
        int[] pixels = new int[W * H];
        Arrays.fill(pixels, background);
        return pixels;
    }
    static void rect(int[] px, int x0, int y0, int x1, int y1, int color) {
        for (int y = y0; y <= y1; y++) for (int x = x0; x <= x1; x++) px[y * W + x] = color;
    }
    static void disk(int[] px, int cx, int cy, int radius, int color) {
        for (int y = cy - radius; y <= cy + radius; y++) {
            for (int x = cx - radius; x <= cx + radius; x++) {
                if ((x-cx)*(x-cx) + (y-cy)*(y-cy) <= radius*radius) px[y * W + x] = color;
            }
        }
    }
    public static void main(String[] args) {
        int[] lane = {100, 150, 220, 190};
        int[] rightLabel = {230, 155, 286, 183};
        int[] aboveLabel = {155, 113, 205, 142};
        int[] r = ScreenGuidanceFinder.laneWithDistance(lane, rightLabel, W, H, 3);
        check(r[0] <= 100 && r[2] >= 286 && r[1] <= 150 && r[3] >= 190, "right-side original distance included");
        r = ScreenGuidanceFinder.laneWithDistance(lane, aboveLabel, W, H, 5);
        check(r[1] <= 113 && r[3] >= 190, "above-box original distance included");
        r = ScreenGuidanceFinder.laneWithDistance(lane, null, W, H, 3);
        check(r[2] >= 286, "right-side layout fallback preserves room for phone label");
        r = ScreenGuidanceFinder.laneWithDistance(lane, null, W, H, 5);
        check(r[1] <= 113, "above-box layout fallback preserves room for phone label");
        check(ScreenGuidanceFinder.laneWithDistance(null, rightLabel, W, H, 3) == null, "no replacement lane without phone box");
        r = ScreenGuidanceFinder.laneWithDistance(new int[]{350, 2, 399, 28}, null, W, H, 5);
        check(r[0] >= 0 && r[1] == 0 && r[2] == W-1 && r[3] < H, "crop clipped to frame");
        for (int color : new int[]{0xffff3030, 0xffffcc20, 0xff20d870}) {
            int[] px = image(0xffdddddd);
            rect(px, 100, 60, 190, 88, 0xff222224);
            disk(px, 114, 74, 6, color);
            disk(px, 138, 74, 6, 0xff555555);
            disk(px, 162, 74, 6, 0xff555555);
            r = ScreenGuidanceFinder.signal(px, W, H);
            check(r != null && r[0] <= 100 && r[2] >= 190 && r[1] <= 60 && r[3] >= 88,
                    "original signal housing captured for color " + color);
        }
        int[] px = image(0xffdddddd);
        rect(px, 100, 60, 130, 90, 0xff222224);
        disk(px, 115, 75, 6, 0xff20d870);
        check(ScreenGuidanceFinder.signal(px, W, H) == null, "reject isolated colored POI badge");
        px = image(0xffdddddd);
        rect(px, 50, 60, 300, 66, 0xff20d870);
        check(ScreenGuidanceFinder.signal(px, W, H) == null, "reject route line");
        px = image(0xff222224);
        disk(px, 115, 75, 6, 0xffff3030);
        check(ScreenGuidanceFinder.signal(px, W, H) == null, "reject unbounded night-map background");
        check(ScreenGuidanceFinder.signal(image(0xffdddddd), W, H) == null, "missing signal stays absent");
        // Photo-shaped regression: two-arrow strip plus right-edge school-zone 30 plaque.
        // The metadata may briefly say zero/one/eight although the same two arrows remain visible.
        px = image(0xffdddddd);
        rect(px, 140, 230, 219, 264, 0xff303033);
        for (int x : new int[]{161, 196}) {
            rect(px, x-2, 242, x+2, 258, 0xffffffff);
            for (int dy=0; dy<9; dy++) rect(px, x-dy/2, 234+dy, x+dy/2, 234+dy, 0xffffffff);
        }
        rect(px, 360, 65, 394, 124, 0xff303033);
        rect(px, 367, 94, 373, 113, 0xffffffff);
        rect(px, 381, 94, 387, 113, 0xffffffff);
        for (int hint : new int[]{0, 1, 2, 8}) {
            r = LaneBoxFinder.find(px, W, H, hint);
            check(r != null && r[0] >= 130 && r[2] <= 230 && r[1] >= 220,
                    "capture visible arrows despite transient lane-count hint " + hint);
        }
        int[] signOnly = image(0xffdddddd);
        rect(signOnly, 360, 65, 394, 124, 0xff303033);
        rect(signOnly, 367, 94, 387, 113, 0xffffffff);
        check(LaneBoxFinder.find(signOnly,W,H,1) == null, "edge speed plaque is not a lane strip");
        check(LaneBoxFinder.find(image(0xffdddddd),W,H,0) == null, "empty metadata cannot fabricate arrows");
        // Two plausible centre strips: use the original nearby metre-label bounds.
        rect(px, 80, 100, 149, 134, 0xff303033);
        rect(px, 105, 108, 111, 126, 0xffffffff);
        r = LaneBoxFinder.find(px,W,H,2,java.util.Collections.singletonList(new int[]{225,234,280,258}));
        check(r != null && r[1] >= 220, "original distance anchor selects its adjacent arrow box");
        System.out.println("Naver screen guidance checks passed: " + checks);
    }
}
