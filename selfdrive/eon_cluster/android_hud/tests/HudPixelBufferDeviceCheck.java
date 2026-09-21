package ai.comma.remotehud;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.Rect;
import android.graphics.RectF;

/** Run via app_process on Android: exercises the real Android Canvas, no UI. */
public final class HudPixelBufferDeviceCheck {
    private static void check(boolean ok, String message) {
        if (!ok) throw new AssertionError(message);
    }
    public static void main(String[] args) {
        Bitmap source = Bitmap.createBitmap(1920, 462, Bitmap.Config.RGB_565);
        source.setDensity(560);
        source.eraseColor(Color.WHITE);
        Bitmap legacy = Bitmap.createBitmap(1920, 462, Bitmap.Config.RGB_565);
        legacy.setDensity(160);
        legacy.eraseColor(Color.BLACK);
        new Canvas(legacy).drawBitmap(source, 0f, 0f, new Paint());
        check(legacy.getPixel(100, 50) == Color.WHITE, "legacy origin should be drawn");
        check(legacy.getPixel(1000, 200) == Color.BLACK, "must reproduce legacy DPI shrink");
        System.out.println("Legacy 560 -> 160 DPI shrink reproduced on Android Canvas");
        legacy.recycle();

        int tests = 0;
        for (int[] dpi : new int[][] {{560,160}, {160,560}, {160,160}, {0,0}}) {
            for (int rotation : new int[] {-90,90}) {
                for (boolean mirror : new boolean[] {false,true}) {
                    Bitmap out = HudPixelBuffer.create(462, 1920);
                    check(out.getDensity() == Bitmap.DENSITY_NONE, "new transport buffer must be density-free");
                    // Explicit destination must remain correct even if a caller
                    // accidentally changes buffer density after construction.
                    source.setDensity(dpi[0]);
                    out.setDensity(dpi[1]);
                    out.eraseColor(Color.BLACK);
                    Canvas canvas = new Canvas(out);
                    Matrix matrix = new Matrix();
                    matrix.setScale(mirror ? -1f : 1f, 1f);
                    matrix.postRotate(rotation);
                    RectF bounds = new RectF(0, 0, 1920, 462);
                    matrix.mapRect(bounds);
                    matrix.postTranslate(-bounds.left, -bounds.top);
                    canvas.setMatrix(matrix);
                    HudPixelBuffer.copy(canvas, source, new Rect(0, 0, 1920, 462), new Paint());
                    for (int y : new int[] {0, 100, 1000, 1919}) {
                        for (int x : new int[] {0, 200, 461}) {
                            check(out.getPixel(x, y) == Color.WHITE,
                                    "unfilled pixel " + x + "," + y + " dpi=" + dpi[0] + "/" + dpi[1]);
                        }
                    }
                    out.recycle();
                    tests++;
                }
            }
        }
        source.recycle();
        System.out.println("PASS: " + tests + " density/rotation/mirror cases fill the complete HUD");
    }
}
