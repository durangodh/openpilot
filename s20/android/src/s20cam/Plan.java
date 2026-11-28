package s20cam;

import android.graphics.ImageFormat;
import android.graphics.Rect;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.util.Size;

/**
 * Make an S20 camera look like a comma camera: frames of outW x outH whose focal length is `focal` px with the
 * optical center in the middle, so openpilot's existing camera config and compiled warps apply unchanged.
 * A sensor crop (digital zoom) narrows the field of view when the lens is wider than wanted; when it is narrower,
 * the whole sensor is scaled down and the frame gets a black border.
 */
final class Plan {
  final String id;
  final int capW, capH, outW, outH, offX, offY;
  final Rect crop;
  final float focal;  // actual focal length of the output frame

  private Plan(String id, int capW, int capH, Rect crop, int outW, int outH, int offX, int offY, float focal) {
    this.id = id; this.capW = capW; this.capH = capH; this.crop = crop;
    this.outW = outW; this.outH = outH; this.offX = offX; this.offY = offY; this.focal = focal;
  }

  static Plan make(String id, CameraCharacteristics c, int outW, int outH, float wantFocal) {
    Rect act = c.get(CameraCharacteristics.SENSOR_INFO_ACTIVE_ARRAY_SIZE);
    float[] k = c.get(CameraCharacteristics.LENS_INTRINSIC_CALIBRATION);  // fx, fy, cx, cy, s in active-array pixels
    float fx = k != null && k[0] > 0 ? k[0] : focalFromLens(c, act);
    float cx = k != null && k[2] > 0 ? k[2] : act.width() / 2f, cy = k != null && k[3] > 0 ? k[3] : act.height() / 2f;
    StreamConfigurationMap map = c.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
    Size[] sizes = map.getOutputSizes(ImageFormat.YUV_420_888);

    // sensor pixels per output pixel that gives the wanted focal length; capture as much of the frame as fits
    float scale = fx / wantFocal;
    Size best = null; float bestCover = -1;
    for (Size s : sizes) {
      if (s.getWidth() > outW || s.getHeight() > outH || s.getWidth() % 2 != 0 || s.getHeight() % 2 != 0) continue;
      float cropW = s.getWidth() * scale, cropH = s.getHeight() * scale;
      float fit = Math.min(1f, Math.min(act.width() / cropW, act.height() / cropH));  // <1: sensor too small, must shrink
      float cover = s.getWidth() * s.getHeight() * fit * fit - Math.abs(fit - 1f) * 1e5f;  // prefer exact focal, then area
      if (cover > bestCover) { bestCover = cover; best = s; }
    }
    if (best == null) throw new IllegalStateException("camera " + id + ": no YUV size fits " + outW + "x" + outH);
    float cropW = best.getWidth() * scale, cropH = best.getHeight() * scale;
    float fit = Math.min(1f, Math.min(act.width() / cropW, act.height() / cropH));
    cropW *= fit; cropH *= fit;
    // center the crop on the optical center, kept inside the sensor
    int x0 = Math.round(Math.max(0, Math.min(act.width() - cropW, cx - cropW / 2)));
    int y0 = Math.round(Math.max(0, Math.min(act.height() - cropH, cy - cropH / 2)));
    Rect crop = new Rect(act.left + x0, act.top + y0, act.left + x0 + Math.round(cropW), act.top + y0 + Math.round(cropH));
    float outPerSensor = best.getWidth() / cropW;
    float focal = fx * outPerSensor;
    // place the image so the optical center lands in the middle of the frame
    float ocx = (cx - x0) * outPerSensor, ocy = (cy - y0) * outPerSensor;
    int offX = Math.max(0, Math.min(outW - best.getWidth(), Math.round(outW / 2f - ocx)));
    int offY = Math.max(0, Math.min(outH - best.getHeight(), Math.round(outH / 2f - ocy)));
    return new Plan(id, best.getWidth(), best.getHeight(), crop, outW, outH, offX, offY, focal);
  }

  private static float focalFromLens(CameraCharacteristics c, Rect act) {
    float f = c.get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS)[0];
    return f / c.get(CameraCharacteristics.SENSOR_INFO_PHYSICAL_SIZE).getWidth() * act.width();
  }

  @Override public String toString() {
    return String.format("camera %s: capture %dx%d of sensor %s -> %dx%d frame at (%d,%d), focal %.1f px",
        id, capW, capH, crop.toShortString(), outW, outH, offX, offY, focal);
  }
}
