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
    final String id; final int w, h; final String file;
    Spec(String id, int w, int h, String file) { this.id = id; this.w = w; this.h = h; this.file = file; }
    static Spec parse(String s) {  // <id>:<w>x<h>:<file>
      String[] a = s.split(":", 3);
      String[] wh = a[1].split("x");
      return new Spec(a[0], Integer.parseInt(wh[0]), Integer.parseInt(wh[1]), a[2]);
    }
  }

  private final CameraManager cm;
  private final List<Spec> specs;
  private final int fps;
  private final Handler handler;
  private final Executor executor;

  Streamer(CameraManager cm, List<Spec> specs, int fps) {
    this.cm = cm;
    this.specs = specs;
    this.fps = fps;
    HandlerThread t = new HandlerThread("s20cam");
    t.start();
    handler = new Handler(t.getLooper());
    executor = handler::post;
  }

  void start() throws Exception {
    for (Spec s : specs) open(s);
  }

  private void open(Spec s) throws Exception {
    RingWriter ring = new RingWriter(s.file, s.w, s.h);
    ImageReader reader = ImageReader.newInstance(s.w, s.h, ImageFormat.YUV_420_888, 4);
    final long[] last = {System.nanoTime(), 0};
    reader.setOnImageAvailableListener(r -> {
      Image img = r.acquireLatestImage();
      if (img == null) return;
      try { ring.write(img); } finally { img.close(); }
      long now = System.nanoTime();
      if (now - last[0] > 5_000_000_000L) {
        Log.i("camera " + s.id + ": " + String.format("%.1f", (ring.frames() - last[1]) * 1e9 / (now - last[0])) + " fps");
        last[0] = now; last[1] = ring.frames();
      }
    }, handler);
    CameraCharacteristics ch = cm.getCameraCharacteristics(s.id);
    cm.openCamera(s.id, executor, new CameraDevice.StateCallback() {
      @Override public void onOpened(CameraDevice dev) {
        try {
          CaptureRequest.Builder rb = dev.createCaptureRequest(CameraDevice.TEMPLATE_RECORD);
          rb.addTarget(reader.getSurface());
          setup(rb, ch);
          SessionConfiguration sc = new SessionConfiguration(SessionConfiguration.SESSION_REGULAR,
              Collections.singletonList(new OutputConfiguration(reader.getSurface())), executor,
              new CameraCaptureSession.StateCallback() {
                @Override public void onConfigured(CameraCaptureSession session) {
                  try {
                    session.setRepeatingRequest(rb.build(), null, handler);
                    Log.i("camera " + s.id + " streaming " + s.w + "x" + s.h + " -> " + s.file);
                  } catch (Exception e) { fail("repeating request " + s.id, e); }
                }
                @Override public void onConfigureFailed(CameraCaptureSession session) { fail("configure " + s.id, null); }
              });
          dev.createCaptureSession(sc);
        } catch (Exception e) { fail("session " + s.id, e); }
      }
      @Override public void onDisconnected(CameraDevice dev) { fail("disconnected " + s.id, null); }
      @Override public void onError(CameraDevice dev, int error) { fail("camera " + s.id + " error " + error, null); }
    });
  }

  private void setup(CaptureRequest.Builder rb, CameraCharacteristics ch) {
    // fixed geometry: no OIS, no stabilization, no distortion correction, focus at infinity when the lens allows it
    rb.set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO);
    rb.set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON);
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
    Range<Integer> best = null;
    if (ranges != null) for (Range<Integer> r : ranges) {
      if (r.getUpper() == fps && (best == null || r.getLower() > best.getLower())) best = r;
    }
    if (best != null) rb.set(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, best);
    else Log.w("no AE fps range ending at " + fps);
  }

  private static void fail(String what, Exception e) {
    Log.w("FAIL " + what + (e != null ? ": " + e : ""));
    if (e != null) e.printStackTrace();
    System.exit(1);
  }
}
