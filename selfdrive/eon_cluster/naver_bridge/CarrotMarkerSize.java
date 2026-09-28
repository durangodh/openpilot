package com.naver.map.carrot;

import android.content.Context;
import com.naver.maps.map.overlay.LocationOverlay;
import com.naver.maps.map.overlay.OverlayImage;
import java.util.Collections;
import java.util.Map;
import java.util.WeakHashMap;

/** Keeps a navigation icon's original pixel size proportional to its load-time density.
 * Called on the existing map/UI update path; no timers, restarts or startup waits.
 */
public final class CarrotMarkerSize {
    private static final Map<OverlayImage, Float> sources =
            Collections.synchronizedMap(new WeakHashMap<OverlayImage, Float>());
    private static final Map<LocationOverlay, State> states = new WeakHashMap<>();

    private static final class State {
        final OverlayImage icon;
        final float density;
        final int width, height;
        State(OverlayImage icon, float density, int width, int height) {
            this.icon = icon;
            this.density = density;
            this.width = width;
            this.height = height;
        }
    }

    private static boolean valid(float density) {
        return density > 0f && !Float.isNaN(density) && !Float.isInfinite(density);
    }

    public static void remember(Object result, float sourceDensity) {
        if (result instanceof OverlayImage && valid(sourceDensity)) {
            sources.put((OverlayImage) result, sourceDensity);
        }
    }

    static int pixels(int sourcePixels, float sourceDensity, float currentDensity) {
        if (sourcePixels <= 0 || !valid(sourceDensity) || !valid(currentDensity)) return 0;
        return Math.max(1, Math.round(sourcePixels * (currentDensity / sourceDensity)));
    }

    public static void apply(LocationOverlay overlay, Context context) {
        if (overlay == null || context == null) return;
        try {
            OverlayImage icon = overlay.getIcon();
            if (icon == null) return;
            float density = context.getResources().getDisplayMetrics().density;
            if (!valid(density)) return;
            State previous = states.get(overlay);
            if (previous != null && previous.icon == icon && previous.density == density
                    && overlay.getIconWidth() == previous.width
                    && overlay.getIconHeight() == previous.height) return;

            Float sourceDensity = sources.get(icon);
            boolean resource = icon.getClass().getName().endsWith("$ResourceDescriptor");
            if (sourceDensity == null && !resource) {
                // Do not reuse a size we set for a different, untracked icon.
                if (previous != null) {
                    overlay.setIconWidth(0);
                    overlay.setIconHeight(0);
                    states.remove(overlay);
                }
                return;
            }

            // j/i are this APK's width/height accessors. Resource descriptors
            // resolve dimensions using the current Context; bitmap descriptors
            // return raw pixel dimensions of the downloaded image.
            int width = icon.j(context), height = icon.i(context);
            if (sourceDensity != null) {
                width = pixels(width, sourceDensity, density);
                height = pixels(height, sourceDensity, density);
            }
            if (width <= 0 || height <= 0) return;
            if (overlay.getIconWidth() != width) overlay.setIconWidth(width);
            if (overlay.getIconHeight() != height) overlay.setIconHeight(height);
            states.put(overlay, new State(icon, density, width, height));
        } catch (RuntimeException ignored) {
            // The original map renderer remains usable if a map is released.
        }
    }

    private CarrotMarkerSize() { }
}
