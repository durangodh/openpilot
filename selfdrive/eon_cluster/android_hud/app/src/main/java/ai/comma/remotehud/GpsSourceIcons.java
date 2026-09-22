package ai.comma.remotehud;

import android.content.res.Resources;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.ColorMatrixColorFilter;
import android.graphics.Paint;
import android.graphics.Rect;
import android.graphics.RectF;
import android.os.SystemClock;

/** User-supplied originals, tinted at draw time; source files stay unchanged. */
final class GpsSourceIcons {
    private static final int GREEN = Color.rgb(84, 214, 120);
    private static final int AMBER = Color.rgb(255, 190, 80);
    private static final int GREY = Color.rgb(105, 115, 125);
    private final Bitmap car, phone;
    private final Rect carBounds, phoneBounds;
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);
    private final RectF rect = new RectF();
    private final ColorMatrixColorFilter green = tint(GREEN), grey = tint(GREY), amber = tint(AMBER);
    private final ColorMatrixColorFilter red = tint(Color.rgb(255, 82, 82));
    private final ColorMatrixColorFilter dimGreen = tint(Color.rgb(20, 64, 34));

    private ColorMatrixColorFilter tone(int tone) {
        switch (tone) {
            case GpsSourcePolicy.GREEN: return green;
            case GpsSourcePolicy.AMBER: return amber;
            case GpsSourcePolicy.RED: return red;
            case GpsSourcePolicy.DIM_GREEN: return dimGreen;
            default: return grey;
        }
    }

    GpsSourceIcons(Resources resources) {
        car = BitmapFactory.decodeResource(resources, R.drawable.gps_vehicle_source);
        phone = BitmapFactory.decodeResource(resources, R.drawable.gps_phone_source);
        carBounds = bounds(car);
        phoneBounds = bounds(phone);
    }
    private static Rect bounds(Bitmap bitmap) {
        int left = bitmap.getWidth(), top = bitmap.getHeight(), right = 0, bottom = 0;
        for (int y = 0; y < bitmap.getHeight(); y++) {
            for (int x = 0; x < bitmap.getWidth(); x++) {
                int pixel = bitmap.getPixel(x, y);
                if (Color.alpha(pixel) > 0 && Color.red(pixel) < 220) {
                    left = Math.min(left, x); top = Math.min(top, y);
                    right = Math.max(right, x + 1); bottom = Math.max(bottom, y + 1);
                }
            }
        }
        return right > left && bottom > top ? new Rect(left, top, right, bottom)
                : new Rect(0, 0, bitmap.getWidth(), bitmap.getHeight());
    }
    private static ColorMatrixColorFilter tint(int color) {
        // White becomes transparent, original grey becomes opaque colored ink.
        return new ColorMatrixColorFilter(new float[]{
                0,0,0,0,Color.red(color), 0,0,0,0,Color.green(color),
                0,0,0,0,Color.blue(color), -1.65f,0,0,1.65f,0});
    }
    private void icon(Canvas canvas, Bitmap bitmap, Rect source, float centerX,
                      float top, ColorMatrixColorFilter filter) {
        float h = 34f, w = h * source.width() / source.height();
        rect.set(centerX - w / 2, top + 7f, centerX + w / 2, top + 7f + h);
        paint.setColorFilter(filter);
        canvas.drawBitmap(bitmap, source, rect, paint);
        paint.setColorFilter(null);
    }
    float width(GpsSourceMonitor.Reading reading) {
        paint.setTextSize(21f);
        return GpsSourcePolicy.badgeWidth(reading.kind)
                + (reading.accuracy.isEmpty() ? 0f : paint.measureText(reading.accuracy) + 16f);
    }
    void draw(Canvas canvas, float right, float top, GpsSourceMonitor.Reading reading) {
        int kind = reading.kind;
        boolean predicted = reading.predicted;
        String status = GpsSourcePolicy.symbol(kind);
        float width = width(reading);
        float left = right - width;
        paint.setColor(Color.argb(210, 28, 34, 40));
        rect.set(left, top, right, top + 48f);
        canvas.drawRoundRect(rect, 10f, 10f, paint);
        long now = SystemClock.elapsedRealtime();
        icon(canvas, car, carBounds, left + 29f, top,
                tone(GpsSourcePolicy.iconTone(kind, predicted, true, now)));
        icon(canvas, phone, phoneBounds, left + 81f, top,
                tone(GpsSourcePolicy.iconTone(kind, predicted, false, now)));
        paint.setColor(kind == GpsSourcePolicy.WAITING || kind == -2 ? GREY : AMBER);
        paint.setTextSize(23f);
        paint.setTextAlign(Paint.Align.CENTER);
        canvas.drawText(status, left + GpsSourcePolicy.badgeWidth(kind) - 24f, top + 33f, paint);
        if (!reading.accuracy.isEmpty()) {
            paint.setColor(Color.WHITE);
            paint.setTextSize(21f);
            paint.setTextAlign(Paint.Align.LEFT);
            canvas.drawText(reading.accuracy, left + GpsSourcePolicy.badgeWidth(kind), top + 32f, paint);
        }
    }
    void close() { car.recycle(); phone.recycle(); }
}
