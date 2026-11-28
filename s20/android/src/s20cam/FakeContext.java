package s20cam;

import android.content.AttributionSource;
import android.content.Context;
import android.content.ContextWrapper;
import android.content.pm.ApplicationInfo;
import android.os.Process;

// Pretend to be the shell package (uid 2000), which holds the CAMERA permission.
final class FakeContext extends ContextWrapper {
  static final String PACKAGE_NAME = "com.android.shell";
  private static FakeContext instance;

  static synchronized FakeContext get() {
    if (instance == null) instance = new FakeContext();
    return instance;
  }

  private FakeContext() { super(Workarounds.systemContext()); }

  @Override public String getPackageName() { return PACKAGE_NAME; }
  @Override public String getOpPackageName() { return PACKAGE_NAME; }
  @Override public Context getApplicationContext() { return this; }
  @Override public int getDeviceId() { return 0; }

  @Override public ApplicationInfo getApplicationInfo() {
    ApplicationInfo base = super.getApplicationInfo();
    ApplicationInfo info = base != null ? new ApplicationInfo(base) : new ApplicationInfo();
    info.packageName = PACKAGE_NAME;
    return info;
  }

  @Override public AttributionSource getAttributionSource() {
    return new AttributionSource.Builder(Process.myUid()).setPackageName(PACKAGE_NAME).build();
  }
}
