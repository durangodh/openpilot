package s20cam;

import android.graphics.ImageFormat;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CaptureRequest;
import android.hardware.camera2.params.OutputConfiguration;
import android.hardware.camera2.params.SessionConfiguration;
import android.media.Image;
import android.media.ImageReader;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Range;

import java.util.Collections;
import java.util.List;
import java.util.concurrent.Executor;

final class Streamer {
  static final class Spec {
    final Plan plan; final String file; final boolean flip, required;
    Spec(Plan plan, String file, boolean flip, boolean required) {
      this.plan = plan; this.file = file; this.flip = flip; this.required = required;
    }
  }

  private final CameraManager cm;
  private final List<Spec> specs;
  private final int fps;
  private final boolean ctrlOff;  // CONTROL_MODE_OFF: some HALs only honour SENSOR_FRAME_DURATION with all 3A off
  private final Handler handler;
  private final Executor executor;

  Streamer(CameraManager cm, List<Spec> specs, int fps, boolean ctrlOff) {
    this.ctrlOff = ctrlOff;
    this.cm = cm;
    this.specs = specs;
    this.fps = fps;
    HandlerThread t = new HandlerThread("s20cam");
    t.start();
    handler = new Handler(t.getLooper());
    executor = handler::post;
  }

  void start() throws Exception {
    for (Spec s : specs) {
      try { open(s); } catch (Exception e) { problem(s, "open", e); }
    }
  }

  /** One camera: ImageReader -> ring, capture session, and (when needed) our own exposure loop at a fixed frame time. */
  private final class Cam {
    final Spec s; final Plan pl; final CameraCharacteristics ch; final RingWriter ring; final ImageReader reader;
    CameraCaptureSession session; CaptureRequest.Builder rb;
    Exposure ae;  // null: the camera's AE runs at an exact [fps, fps] range
    long t0 = System.nanoTime(), n0 = 0;

    Cam(Spec s) throws Exception {
      this.s = s; this.pl = s.plan; this.ch = Cam.this.chars();
      ring = new RingWriter(s.file, pl.outW, pl.outH, pl.capW, pl.capH, pl.offX, pl.offY, pl.focal, s.flip);
      reader = ImageReader.newInstance(pl.capW, pl.capH, ImageFormat.YUV_420_888, 4);
      reader.setOnImageAvailableListener(r -> {
        Image img = r.acquireLatestImage();
        if (img == null) return;
        int luma;
        try { luma = ring.write(img); } finally { img.close(); }
        if (ae != null && ae.update(luma)) repeat();
        long now = System.nanoTime();
        if (now - t0 > 5_000_000_000L) {
          Log.i("camera " + pl.id + ": " + String.format("%.1f fps, luma %d", (ring.frames() - n0) * 1e9 / (now - t0), luma)
              + (ae != null ? ", " + ae : ""));
          t0 = now; n0 = ring.frames();
        }
      }, handler);
    }

    private CameraCharacteristics chars() throws Exception { return s20cam.Cam.chars(cm, s.plan.id); }

    void repeat() {
      try {
        if (ae != null) ae.apply(rb);
        session.setRepeatingRequest(rb.build(), null, handler);
      } catch (Exception e) { problem(s, "repeating request", e); }
    }
  }

  private void open(Spec s) throws Exception {
    Cam c = new Cam(s);
    s20cam.Cam.open(cm, c.pl.id, executor, new CameraDevice.StateCallback() {
      @Override public void onOpened(CameraDevice dev) {
        try {
          c.rb = dev.createCaptureRequest(CameraDevice.TEMPLATE_RECORD);
          c.rb.addTarget(c.reader.getSurface());
          setup(c);
          c.rb.set(CaptureRequest.SCALER_CROP_REGION, c.pl.crop);
          SessionConfiguration sc = new SessionConfiguration(SessionConfiguration.SESSION_REGULAR,
              Collections.singletonList(new OutputConfiguration(c.reader.getSurface())), executor,
              new CameraCaptureSession.StateCallback() {
                @Override public void onConfigured(CameraCaptureSession session) {
                  c.session = session;
                  c.repeat();
                  Log.i("streaming " + c.pl + (s.flip ? ", rotated 180" : "") + " -> " + s.file);
                }
                @Override public void onConfigureFailed(CameraCaptureSession session) { problem(s, "configure", null); }
              });
          dev.createCaptureSession(sc);
        } catch (Exception e) { problem(s, "session", e); }
      }
      @Override public void onDisconnected(CameraDevice dev) { problem(s, "disconnected", null); dev.close(); }
      @Override public void onError(CameraDevice dev, int error) {
        problem(s, "error " + error + (error == ERROR_MAX_CAMERAS_IN_USE ? " (max cameras in use)" : ""), null);
        dev.close();
      }
    });
  }

  private void setup(Cam c) {
    CaptureRequest.Builder rb = c.rb;
    CameraCharacteristics ch = c.ch;
    // fixed geometry: no OIS, no stabilization, no distortion correction, focus at infinity when the lens allows it
    rb.set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO);
    rb.set(CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE, CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE_OFF);
    rb.set(CaptureRequest.LENS_OPTICAL_STABILIZATION_MODE, CaptureRequest.LENS_OPTICAL_STABILIZATION_MODE_OFF);
    int[] dc = ch.get(CameraCharacteristics.DISTORTION_CORRECTION_AVAILABLE_MODES);
    if (dc != null && dc.length > 0) rb.set(CaptureRequest.DISTORTION_CORRECTION_MODE, CaptureRequest.DISTORTION_CORRECTION_MODE_OFF);
    Float minFocus = ch.get(CameraCharacteristics.LENS_INFO_MINIMUM_FOCUS_DISTANCE);
    if (minFocus != null && minFocus > 0) {
      rb.set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_OFF);
      rb.set(CaptureRequest.LENS_FOCUS_DISTANCE, 0f);  // infinity
    }
    Range<Integer>[] ranges = ch.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES);
    if (ranges != null) for (Range<Integer> r : ranges) {
      if (r.getLower() == fps && r.getUpper() == fps) {
        rb.set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON);
        rb.set(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, r);
        Log.i("camera " + c.pl.id + ": camera AE at " + r);
        return;
      }
    }
    if (Exposure.supported(ch)) {
      // no exact range (S20 FE: 15, 24, 30 fixed): run the sensor at exactly 1/fps and do exposure ourselves
      c.ae = new Exposure(ch, 1_000_000_000L / fps);
      rb.set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_OFF);
      if (ctrlOff) rb.set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_OFF);
      Log.i("camera " + c.pl.id + ": manual exposure at " + fps + " fps" + (ctrlOff ? ", 3A off" : ""));
      return;
    }
    // last resort: the camera's AE with the closest range that contains fps
    Range<Integer> best = null;
    if (ranges != null) for (Range<Integer> r : ranges)
      if (r.contains(fps) && (best == null || r.getUpper() < best.getUpper())) best = r;
    rb.set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON);
    if (best != null) rb.set(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, best);
    Log.w("camera " + c.pl.id + ": no exact " + fps + " fps and no manual sensor, camera AE at " + best);
  }

  private static void problem(Spec s, String what, Exception e) {
    Log.w("camera " + s.plan.id + ": FAIL " + what + (e != null ? ": " + e : ""));
    if (e != null) e.printStackTrace();
    if (s.required) System.exit(1);
    Log.w("camera " + s.plan.id + " is optional, continuing without it");
  }
}
