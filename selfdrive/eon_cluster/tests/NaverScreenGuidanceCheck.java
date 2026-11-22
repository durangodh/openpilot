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
    static void arrow(int[] px, int x, int y, int direction, int color) {
        // Filled triangular head with a narrower, centred shaft; rotate original pixels.
        for(int v=0;v<23;v++) for(int u=-7;u<=7;u++) {
            boolean mark=v<8?Math.abs(u)<=v:(Math.abs(u)<=2);
            if(!mark)continue;
            int xx=direction==0?x+u:direction==1?x-u:direction==2?x+v:x-v;
            int yy=direction==0?y+v:direction==1?y-v:y+u;
            px[yy*W+xx]=color;
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
        // A speed plaque may be in the centre of the source window. Position and
        // a nearby original metre label are not enough: actual arrow pixels are required.
        for(String digits:new String[]{"30","80","20","50","188m","1234","0","1","2","3","4","5","6","7","8","9"}) {
            for(int size:new int[]{16,24,32}) {
                java.awt.image.BufferedImage im=new java.awt.image.BufferedImage(W,H,java.awt.image.BufferedImage.TYPE_INT_ARGB);
                java.awt.Graphics2D g=im.createGraphics();
                g.setColor(new java.awt.Color(0xffdddddd,true));g.fillRect(0,0,W,H);
                g.setColor(new java.awt.Color(0xff303033,true));g.fillRect(145,190,110,55);
                g.setColor(java.awt.Color.WHITE);g.setFont(new java.awt.Font("SansSerif",java.awt.Font.BOLD,size));
                g.drawString(digits,154,230);g.dispose();
                int[] numeric=im.getRGB(0,0,W,H,null,0,W);
                r=LaneBoxFinder.find(numeric,W,H,2,java.util.Collections.singletonList(new int[]{260,200,310,220}));
                check(r==null,"numeric plaque is never accepted as arrows: "+digits+" size "+size);
            }
        }
        for(int dir=0;dir<4;dir++) for(int ink:new int[]{0xffffffff,0xffaaaaaa,0xff55aaff}) {
            px=image(0xffdddddd);rect(px,140,170,230,239,0xff303033);
            arrow(px,180,200,dir,ink);
            check(LaneBoxFinder.find(px,W,H,0)!=null,"original arrow detected in direction "+dir+" ink "+ink);
        }
        // One arrow is valid too: do not solve the speed-plaque bug by dropping single lanes.
        px=image(0xffdddddd);rect(px,180,210,219,249,0xff303033);arrow(px,200,217,0,0xffffffff);
        check(LaneBoxFinder.find(px,W,H,1)!=null,"single original lane stays supported");
        px=image(0xffdddddd);rect(px,160,160,240,239,0xff303033);
        arrow(px,220,190,3,0xffffffff);rect(px,196,190,200,223,0xffffffff);
        check(LaneBoxFinder.find(px,W,H,1)!=null,"right-turn arrow with a bent stem remains supported");
        px=image(0xffdddddd);rect(px,160,160,240,239,0xff303033);
        arrow(px,190,176,0,0xffffffff);rect(px,175,198,192,202,0xffffffff);
        check(LaneBoxFinder.find(px,W,H,1)!=null,"straight arrow with side branch remains supported");
        System.out.println("Naver screen guidance checks passed: " + checks);
    }
}
