package s20cam;

import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CaptureRequest;
import android.util.Range;

/**
 * Our own auto exposure for a fixed frame time (the camera's AE cannot hold 20 fps on the S20 FE).
 * Keeps mean luma near TARGET: shortest exposure first (less motion blur), then gain, then longer exposure.
 */
final class Exposure {
  static final int TARGET = 110;
  private final long frameNs, expMin, expMax, expPreferMax;
  private final int isoMin, isoMax;
  private long exp;
  private int iso, frames;

  static boolean supported(CameraCharacteristics ch) {
    int[] caps = ch.get(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES);
    if (caps == null) return false;
    for (int c : caps) if (c == CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_MANUAL_SENSOR) return true;
    return false;
  }

  Exposure(CameraCharacteristics ch, long frameNs) {
    this.frameNs = frameNs;
    Range<Long> er = ch.get(CameraCharacteristics.SENSOR_INFO_EXPOSURE_TIME_RANGE);
    Range<Integer> ir = ch.get(CameraCharacteristics.SENSOR_INFO_SENSITIVITY_RANGE);
    expMin = er.getLower();
    expMax = Math.min(er.getUpper(), frameNs * 7 / 10);
    expPreferMax = Math.min(expMax, 10_000_000L);  // up to 10 ms before raising gain
    isoMin = ir.getLower();
    isoMax = ir.getUpper();
    exp = Math.min(expPreferMax, 5_000_000L);
    iso = Math.max(isoMin, 100);
  }

  void apply(CaptureRequest.Builder rb) {
    rb.set(CaptureRequest.SENSOR_FRAME_DURATION, frameNs);
    rb.set(CaptureRequest.SENSOR_EXPOSURE_TIME, exp);
    rb.set(CaptureRequest.SENSOR_SENSITIVITY, iso);
  }

  /** Feed one frame's mean luma; true when the request should be resent with new settings. */
  boolean update(int luma) {
    if (++frames % 4 != 0) return false;  // settings take a few frames to show up
    double ratio = Math.max(0.5, Math.min(2.0, TARGET / (double) Math.max(luma, 1)));
    if (Math.abs(ratio - 1) < 0.08) return false;
    double total = exp * (double) iso * ratio;  // "exposure value" in ns*ISO
    long e = (long) Math.max(expMin, Math.min(expPreferMax, total / isoMin));
    int i = (int) Math.max(isoMin, Math.min(isoMax, total / e));
    if (i >= isoMax) e = (long) Math.max(expMin, Math.min(expMax, total / isoMax));
    if (e == exp && i == iso) return false;
    exp = e; iso = i;
    return true;
  }

  @Override public String toString() { return String.format("exposure %.2f ms ISO %d", exp / 1e6, iso); }
}
