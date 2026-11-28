package s20cam;

import android.graphics.ImageFormat;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.os.Looper;
import android.util.Range;
import android.util.Size;
import android.util.SizeF;

import java.lang.reflect.Constructor;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Set;

/**
 * Camera bridge for the S20 FE port (stage 6). Runs on the Android side with app_process as the shell user:
 *
 *   list                                       print every camera: facing, focal length, sensor, intrinsics, YUV sizes
 *   auto [road=0] [wide=2|none] [dir=...] [fps=20] [flip=1] [dry]
 *                                              stream the road and wide cameras into ring files as comma-like frames
 */
public final class Main {
  private Main() {}

  public static void main(String[] args) throws Exception {
    Workarounds.apply();
    CameraManager cm = cameraManager();
    if (args.length == 0 || args[0].equals("list")) {
      list(cm);
      System.exit(0);
    } else if (args[0].equals("auto")) {
      // auto [road=0] [wide=2|none] [dir=/data/s20pilot/share] [fps=20] [flip=0|1] [out=1928x1208] [roadf=2648] [widef=567]
      // defaults make the frames match openpilot's comma 3 camera config (AR0231/OX03C10: 2648 px road, 567 px wide)
      java.util.Map<String, String> o = new java.util.HashMap<>();
      for (int i = 1; i < args.length; i++) { String[] kv = args[i].split("=", 2); o.put(kv[0], kv.length > 1 ? kv[1] : ""); }
      String[] out = o.getOrDefault("out", "1928x1208").split("x");
      int ow = Integer.parseInt(out[0]), oh = Integer.parseInt(out[1]);
      String dir = o.getOrDefault("dir", "/data/s20pilot/share");
      boolean flip = o.getOrDefault("flip", "0").equals("1");
      List<Streamer.Spec> specs = new ArrayList<>();
      String road = o.getOrDefault("road", "0"), wide = o.getOrDefault("wide", "2");
      specs.add(new Streamer.Spec(Plan.make(road, Cam.chars(cm, road), ow, oh, Float.parseFloat(o.getOrDefault("roadf", "2648"))), dir + "/road.ring", flip));
      if (!wide.equals("none"))
        specs.add(new Streamer.Spec(Plan.make(wide, Cam.chars(cm, wide), ow, oh, Float.parseFloat(o.getOrDefault("widef", "567"))), dir + "/wide.ring", flip));
      for (Streamer.Spec sp : specs) Log.i("plan " + sp.plan);
      if (o.containsKey("dry")) System.exit(0);
      new Streamer(cm, specs, Integer.parseInt(o.getOrDefault("fps", "20"))).start();
      Looper.loop();
    } else {
      throw new IllegalArgumentException("unknown command " + args[0]);
    }
  }

  static CameraManager cameraManager() throws Exception {
    Constructor<CameraManager> ctor = CameraManager.class.getDeclaredConstructor(android.content.Context.class);
    ctor.setAccessible(true);
    return ctor.newInstance(FakeContext.get());
  }

  private static void list(CameraManager cm) throws Exception {
    String[] ids = cm.getCameraIdList();
    Log.i("camera ids: " + Arrays.toString(ids));
    try {
      Set<Set<String>> conc = cm.getConcurrentCameraIds();
      Log.i("concurrent camera sets: " + conc);
    } catch (Throwable t) { Log.i("concurrent camera sets: n/a (" + t + ")"); }
    List<String> all = new ArrayList<>(Arrays.asList(ids));
    // hidden physical cameras (e.g. the ultra-wide) are often reachable by id without being listed
    for (int i = 0; i < 8; i++) if (!all.contains(Integer.toString(i))) all.add(Integer.toString(i));
    for (int k = 0; k < all.size(); k++) {
      String id = all.get(k);
      CameraCharacteristics c;
      try { c = Cam.chars(cm, id); } catch (Throwable t) { Log.i("=== camera " + id + ": not available (" + (t.getCause() != null ? t.getCause() : t) + ")"); continue; }
      Integer facing = c.get(CameraCharacteristics.LENS_FACING);
      String f = facing == null ? "?" : facing == CameraCharacteristics.LENS_FACING_BACK ? "back"
               : facing == CameraCharacteristics.LENS_FACING_FRONT ? "front" : "external";
      Log.i("=== camera " + id + " (" + f + ")");
      Log.i("  focal lengths mm: " + Arrays.toString(c.get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS)));
      SizeF phys = c.get(CameraCharacteristics.SENSOR_INFO_PHYSICAL_SIZE);
      Log.i("  sensor physical mm: " + phys);
      Log.i("  pixel array: " + c.get(CameraCharacteristics.SENSOR_INFO_PIXEL_ARRAY_SIZE)
          + "  active: " + c.get(CameraCharacteristics.SENSOR_INFO_ACTIVE_ARRAY_SIZE));
      Log.i("  intrinsics [fx,fy,cx,cy,s]: " + Arrays.toString(c.get(CameraCharacteristics.LENS_INTRINSIC_CALIBRATION)));
      Log.i("  distortion: " + Arrays.toString(c.get(CameraCharacteristics.LENS_DISTORTION)));
      Log.i("  pose translation: " + Arrays.toString(c.get(CameraCharacteristics.LENS_POSE_TRANSLATION))
          + "  rotation: " + Arrays.toString(c.get(CameraCharacteristics.LENS_POSE_ROTATION)));
      Log.i("  hw level: " + c.get(CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL)
          + "  timestamp source (0=unknown/monotonic 1=realtime/boottime): " + c.get(CameraCharacteristics.SENSOR_INFO_TIMESTAMP_SOURCE));
      Log.i("  AF modes: " + Arrays.toString(c.get(CameraCharacteristics.CONTROL_AF_AVAILABLE_MODES))
          + "  min focus dist: " + c.get(CameraCharacteristics.LENS_INFO_MINIMUM_FOCUS_DISTANCE));
      Log.i("  OIS modes: " + Arrays.toString(c.get(CameraCharacteristics.LENS_INFO_AVAILABLE_OPTICAL_STABILIZATION))
          + "  distortion correction modes: " + Arrays.toString(c.get(CameraCharacteristics.DISTORTION_CORRECTION_AVAILABLE_MODES)));
      Range<Integer>[] fpsr = c.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES);
      Log.i("  fps ranges: " + Arrays.toString(fpsr));
      try {
        Set<String> physIds = c.getPhysicalCameraIds();
        if (!physIds.isEmpty()) {
          Log.i("  logical camera, physical ids: " + physIds);
          for (String p : physIds) if (!all.contains(p)) all.add(p);
        }
      } catch (Throwable t) { /* old API */ }
      StreamConfigurationMap map = c.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
      if (map != null) {
        StringBuilder sb = new StringBuilder();
        Size[] sizes = map.getOutputSizes(ImageFormat.YUV_420_888);
        if (sizes != null) for (Size s : sizes) {
          long ns = map.getOutputMinFrameDuration(ImageFormat.YUV_420_888, s);
          sb.append(s).append(ns > 0 ? String.format("@%.0f ", 1e9 / ns) : " ");
        }
        Log.i("  YUV_420_888 sizes@maxfps: " + sb);
      }
    }
  }
}
