package ai.comma.naverhud;

import android.graphics.Rect;
import android.view.View;
import android.view.ViewGroup;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityNodeProvider;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;

/** Read the app's own Compose accessibility bounds without enabling a service,
 * clicking anything, or changing the accessibility settings. Optional: pixel
 * region discovery remains available when a node has no semantic label. */
final class NaverScreenSemantics {
    static final class Entry {
        final String text;
        final int[] bounds, parent;
        Entry(String text, int[] bounds, int[] parent) {
            this.text = text; this.bounds = bounds; this.parent = parent;
        }
    }

    static List<Entry> read(View decor) {
        List<Entry> out = new ArrayList<>();
        int[] origin = new int[2];
        decor.getLocationOnScreen(origin);
        walkViews(decor, origin, out, 0);
        return out;
    }

    private static void walkViews(View v, int[] origin, List<Entry> out, int depth) {
        if (depth > 30 || !v.isShown() || out.size() > 512) return;
        AccessibilityNodeProvider provider = v.getAccessibilityNodeProvider();
        if (provider != null) {
            walkNodes(provider, -1, origin, null, out, new HashSet<>(), 0);
        } else {
            try {
                AccessibilityNodeInfo n = v.createAccessibilityNodeInfo();
                if (n != null) {
                    add(n, origin, null, out);
                    n.recycle();
                }
            } catch (Throwable ignored) { }
        }
        if (v instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) v;
            for (int i = 0; i < group.getChildCount(); i++) walkViews(group.getChildAt(i), origin, out, depth + 1);
        }
    }

    private static void walkNodes(AccessibilityNodeProvider provider, int id, int[] origin, int[] parent,
                                  List<Entry> out, HashSet<Integer> seen, int depth) {
        if (depth > 30 || seen.size() >= 512 || !seen.add(id)) return;
        AccessibilityNodeInfo node = null;
        try {
            node = provider.createAccessibilityNodeInfo(id);
            if (node == null) return;
            int[] bounds = add(node, origin, parent, out);
            for (int i = 0; i < node.getChildCount(); i++) {
                // getChild() needs an accessibility-service connection. For the app's own
                // provider, read the encoded child ID and ask the provider directly.
                long child = ((Number) de.robv.android.xposed.XposedHelpers.callMethod(node, "getChildId", i)).longValue();
                int virtualId = (int) (child >> 32);
                walkNodes(provider, virtualId, origin, bounds, out, seen, depth + 1);
            }
        } catch (Throwable ignored) {
            // No accessible virtual node on this Compose version: use pixel discovery.
        } finally {
            if (node != null) node.recycle();
        }
    }

    private static int[] add(AccessibilityNodeInfo node, int[] origin, int[] parent, List<Entry> out) {
        Rect r = new Rect();
        node.getBoundsInScreen(r);
        int[] bounds = {r.left - origin[0], r.top - origin[1], r.right - origin[0] - 1, r.bottom - origin[1] - 1};
        String text = (String.valueOf(node.getText()) + " " + String.valueOf(node.getContentDescription())).toLowerCase(Locale.ROOT);
        if (!r.isEmpty()) out.add(new Entry(text, bounds, parent));
        return bounds;
    }

    static int[] distance(List<Entry> entries, int[] lane, int w, int h) {
        if (lane == null) return null;
        int bh = lane[3] - lane[1] + 1;
        int[] best = null;
        double bestD = Double.MAX_VALUE;
        for (Entry e : entries) {
            if (!e.text.matches("(?s).*\\d(?:[\\d., ]*)\\s*(?:km|m|킬로미터|미터)(?:\\b|\\s|$).*")) continue;
            int[] r = e.bounds;
            if (!compact(r, w, h) || r[2] - r[0] > bh * 4 || r[3] - r[1] > bh * 1.6) continue;
            int dx = Math.max(0, Math.max(lane[0] - r[2], r[0] - lane[2]));
            int dy = Math.max(0, Math.max(lane[1] - r[3], r[1] - lane[3]));
            double d = Math.hypot(dx, dy);
            if (d < bh * 1.6 && d < bestD) { bestD = d; best = r; }
        }
        return best;
    }

    static int[] signal(List<Entry> entries, int w, int h) {
        int[] best = null;
        int area = Integer.MAX_VALUE;
        for (Entry e : entries) {
            if (!e.text.matches("(?s).*(?:신호등|교통신호|신호 정보|신호정보|traffic light|traffic signal|빨간불|초록불).*")) continue;
            int[] r = e.bounds;
            if (e.parent != null && compact(e.parent, w, h)
                    && e.parent[2] - e.parent[0] < w * .65 && e.parent[3] - e.parent[1] < h * .2) r = e.parent;
            if (!compact(r, w, h) || r[2] - r[0] > w * .65 || r[3] - r[1] > h * .2) continue;
            int a = (r[2] - r[0] + 1) * (r[3] - r[1] + 1);
            if (a < area) { area = a; best = r; }
        }
        return best;
    }

    static int[] counter(List<Entry> entries, int[] lamp, int w, int h) {
        int height = lamp[3] - lamp[1] + 1;
        int[] best = null;
        double bestD = Double.MAX_VALUE;
        for (Entry e : entries) {
            if (!e.text.matches("(?s).*\\d+\\s*(?:초|seconds?|sec)(?:\\s|$).*")) continue;
            int[] r = e.bounds;
            if (!compact(r, w, h) || r[2] - r[0] > height * 3 || r[3] - r[1] > height * 2) continue;
            int dx = Math.max(0, Math.max(lamp[0] - r[2], r[0] - lamp[2]));
            int dy = Math.max(0, Math.max(lamp[1] - r[3], r[1] - lamp[3]));
            double d = Math.hypot(dx, dy);
            if (d < height * 2 && d < bestD) { bestD = d; best = r; }
        }
        return best;
    }

    private static boolean compact(int[] r, int w, int h) {
        return r[0] >= 0 && r[1] >= 0 && r[2] < w && r[3] < h && r[2] > r[0] && r[3] > r[1];
    }
}
