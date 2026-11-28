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
    // hidden variants differ between releases, e.g. Android 15 openCameraForUid(id, cb, ex, uid, oomScoreOffset,
    // rotationOverride) and later ones with extra flags; take any that starts with (String, StateCallback, Executor)
    // and fill the rest: ints -> clientUid -1 (calling uid), oomScoreOffset 0, rotationOverride 0 (none); booleans false
    Method pick = null;
    for (String name : new String[] {"openCameraForUid", "openCameraDeviceUserAsync"}) {
      for (Method m : CameraManager.class.getDeclaredMethods()) {
        Class<?>[] p = m.getParameterTypes();
        if (!m.getName().equals(name) || p.length < 4 || p[0] != String.class) continue;
        if (p[1] != CameraDevice.StateCallback.class || p[2] != Executor.class) continue;
        boolean ok = true;
        for (int i = 3; i < p.length; i++) ok &= p[i] == int.class || p[i] == boolean.class;
        if (ok && (pick == null || p.length > pick.getParameterTypes().length)) pick = m;
      }
      if (pick != null) break;
    }
    if (pick == null) {
      for (Method m : CameraManager.class.getDeclaredMethods())
        if (m.getName().startsWith("openCamera")) Log.w("candidate: " + m);
      Log.w("no hidden open method found, using openCamera");
      cm.openCamera(id, ex, cb);
      return;
    }
    Class<?>[] p = pick.getParameterTypes();
    Object[] a = new Object[p.length];
    a[0] = id; a[1] = cb; a[2] = ex;
    int ints = 0;
    for (int i = 3; i < p.length; i++) a[i] = p[i] == boolean.class ? (Object) false : (Object) (ints++ == 0 ? -1 : 0);
    Log.i("opening camera " + id + " with " + pick);
    pick.setAccessible(true);
    pick.invoke(cm, a);
  }
}
