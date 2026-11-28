package s20cam;

import android.annotation.SuppressLint;
import android.app.Application;
import android.content.Context;
import android.content.ContextWrapper;
import android.content.pm.ApplicationInfo;
import android.os.Looper;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;

// app_process has no Android Context; build a system one the way scrcpy does, so CameraManager works from a shell.
@SuppressLint("PrivateApi,DiscouragedPrivateApi")
final class Workarounds {
  private static Class<?> atClass;
  private static Object activityThread;

  private Workarounds() {}

  static void apply() {
    if (Looper.getMainLooper() == null) Looper.prepareMainLooper();
    try {
      atClass = Class.forName("android.app.ActivityThread");
      Constructor<?> ctor = atClass.getDeclaredConstructor();
      ctor.setAccessible(true);
      activityThread = ctor.newInstance();
      Field cur = atClass.getDeclaredField("sCurrentActivityThread");
      cur.setAccessible(true);
      cur.set(null, activityThread);
      Field sys = atClass.getDeclaredField("mSystemThread");
      sys.setAccessible(true);
      sys.setBoolean(activityThread, true);
    } catch (Throwable t) {
      throw new RuntimeException("ActivityThread setup failed", t);
    }
    try { fillAppInfo(); } catch (Throwable t) { Log.w("fillAppInfo: " + t); }
    try { fillAppContext(); } catch (Throwable t) { Log.w("fillAppContext: " + t); }
  }

  private static void fillAppInfo() throws Exception {
    Class<?> appBindDataClass = Class.forName("android.app.ActivityThread$AppBindData");
    Constructor<?> ctor = appBindDataClass.getDeclaredConstructor();
    ctor.setAccessible(true);
    Object appBindData = ctor.newInstance();
    ApplicationInfo info = new ApplicationInfo();
    info.packageName = FakeContext.PACKAGE_NAME;
    Field appInfo = appBindDataClass.getDeclaredField("appInfo");
    appInfo.setAccessible(true);
    appInfo.set(appBindData, info);
    Field bound = atClass.getDeclaredField("mBoundApplication");
    bound.setAccessible(true);
    bound.set(activityThread, appBindData);
  }

  private static void fillAppContext() throws Exception {
    Application app = new Application();
    Method attach = ContextWrapper.class.getDeclaredMethod("attachBaseContext", Context.class);
    attach.setAccessible(true);
    attach.invoke(app, FakeContext.get());
    Field initial = atClass.getDeclaredField("mInitialApplication");
    initial.setAccessible(true);
    initial.set(activityThread, app);
  }

  static Context systemContext() {
    try {
      Method m = atClass.getDeclaredMethod("getSystemContext");
      return (Context) m.invoke(activityThread);
    } catch (Throwable t) {
      throw new RuntimeException("getSystemContext failed", t);
    }
  }
}
