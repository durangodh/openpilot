package ai.comma.remotehud;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Rect;

/** Transport frames are physical pixels, never Android density-scaled UI. */
final class HudPixelBuffer {
    static Bitmap create(int width, int height) {
        Bitmap bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.RGB_565);
        bitmap.setDensity(Bitmap.DENSITY_NONE);
        return bitmap;
    }

    static void copy(Canvas canvas, Bitmap source, Rect destination, Paint paint) {
        // The coordinate-only overload silently multiplies by target/source DPI.
        // An explicit destination rectangle is independent of BOTH densities.
        canvas.drawBitmap(source, null, destination, paint);
    }

    private HudPixelBuffer() { }
}
