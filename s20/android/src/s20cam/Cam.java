package s20cam;

import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;

import java.lang.reflect.Method;
import java.util.concurrent.Executor;

// Android 15/16 CameraManager asks the app context for a rotation override (landscape-to-portrait compat) and fails
// for app_process. Call the hidden overloads that take the override directly (ROTATION_OVERRIDE_NONE = 0 / false).
final class Cam {
  private Cam() {}

  static CameraCharacteristics chars(CameraManager cm, String id) throws Exception {
    for (Method m : CameraManager.class.getDeclaredMethods()) {
      if (!m.getName().equals("getCameraCharacteristics")) continue;
      Class<?>[] p = m.getParameterTypes();
      if (p.length == 2 && p[0] == String.class && (p[1] == int.class || p[1] == boolean.class)) {
        m.setAccessible(true);
        return (CameraCharacteristics) m.invoke(cm, id, p[1] == int.class ? (Object) 0 : (Object) false);
      }
    }
    return cm.getCameraCharacteristics(id);
  }

  static void open(CameraManager cm, String id, Executor ex, CameraDevice.StateCallback cb) throws Exception {
    for (Method m : CameraManager.class.getDeclaredMethods()) {
      if (!m.getName().equals("openCameraForUid")) continue;
      Class<?>[] p = m.getParameterTypes();
      // (String id, StateCallback, Executor, int clientUid, int oomScoreOffset, int|boolean rotationOverride)
      if (p.length == 6 && p[1] == CameraDevice.StateCallback.class && p[2] == Executor.class) {
        m.setAccessible(true);
        m.invoke(cm, id, cb, ex, -1, 0, p[5] == int.class ? (Object) 0 : (Object) false);
        return;
      }
    }
    Log.w("openCameraForUid overload not found, using openCamera");
    cm.openCamera(id, ex, cb);
  }
}
