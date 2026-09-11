package ai.comma.remotehud;

import java.util.Arrays;

public final class MapThemePolicyCheck {
    private static void check(boolean result, String message) {
        if (!result) throw new AssertionError(message);
    }

    private static int[] map(int background, int road) {
        int[] pixels = new int[192];
        Arrays.fill(pixels, background);
        for (int i = 0; i < pixels.length; i += 5) pixels[i] = road;
        for (int i = 1; i < pixels.length; i += 9) pixels[i] = 0xff0088ff;
        return pixels;
    }

    public static void main(String[] args) {
        check(MapThemePolicy.classify(map(0xffe5e5e5, 0xffbababa)) == 0, "day map");
        check(MapThemePolicy.classify(map(0xff18202a, 0xff39434f)) == 1, "night map");
        check(MapThemePolicy.classify(new int[192]) == -1, "black loading frame");
        int[] blank = new int[192];
        Arrays.fill(blank, 0xffffffff);
        check(MapThemePolicy.classify(blank) == -1, "white loading frame");
        check(MapThemePolicy.classify(map(0xff008800, 0xff0066cc)) == -1, "colored imagery");
        MapThemePolicy p = new MapThemePolicy();
        p.observe(0, 1000); p.observe(0, 1500); p.observe(0, 2500);
        check(p.current(2500) == 0, "stable day");
        p.observe(1, 3000); p.observe(0, 3500);
        check(p.current(3500) == 0, "brief dark frame must not switch");
        p.observe(1, 4000); p.observe(1, 4500); p.observe(1, 5500);
        check(p.current(5500) == 1, "stable night");
        p.observe(-1, 6000);
        check(p.current(6000) == 1, "brief uncertain frame preserves state");
        check(p.current(11000) == -1, "stale map falls back");
        p.observe(0, 11000);
        check(p.current(11000) == -1, "recovery cannot revive old theme");
        p.observe(0, 12500);
        check(p.current(12500) == 0, "recovery settles");
        p.reset();
        check(p.current(13000) == -1, "app switch resets theme");
        System.out.println("Map theme policy checks passed");
    }
}
