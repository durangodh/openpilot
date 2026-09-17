package ai.comma.remotehud;

import android.content.Context;
import android.hardware.usb.UsbDevice;
import android.os.Bundle;
import android.os.IBinder;
import android.os.UserHandle;

import java.lang.reflect.Method;
import java.util.concurrent.TimeUnit;

/** Silently grants the rooted S9 access to the dedicated TURZX USB panel. */
public final class UsbPermissionGranter {

    private static final int VID = 0x1CBE;
    private static final int PID = 0x0092;

    private UsbPermissionGranter() {
    }

    /**
     * Run a tiny root app_process from this APK. Calling IUsbManager as root is
     * the only way to grant USB access without creating PermissionController's
     * modal dialog. A failure is harmless; TurzxDisplay retries after Magisk is
     * ready instead of falling back to a visible requestPermission().
     */
    static boolean grantSilently(Context context) {
        Process process = null;
        try {
            String apk = context.getApplicationInfo().sourceDir;
            String command = "export CLASSPATH=" + shellQuote(apk)
                    + "; exec app_process /system/bin "
                    + UsbPermissionGranter.class.getName() + " "
                    + shellQuote(context.getPackageName()) + " "
                    + context.getApplicationInfo().uid + " "
                    + (context.getApplicationInfo().uid / 100000);
            process = Runtime.getRuntime().exec(new String[]{"su", "-c", command});
            if (!process.waitFor(4L, TimeUnit.SECONDS)) {
                process.destroy();
                return false;
            }
            return process.exitValue() == 0;
        } catch (Exception ignored) {
            return false;
        } finally {
            if (process != null) {
                process.destroy();
            }
        }
    }

    /** Entry point used by the root app_process above. */
    public static void main(String[] args) throws Exception {
        if (args.length != 3) {
            throw new IllegalArgumentException("package uid userId required");
        }
        String packageName = args[0];
        int uid = Integer.parseInt(args[1]);
        int userId = Integer.parseInt(args[2]);

        allowHiddenApis();

        Class<?> serviceManager = Class.forName("android.os.ServiceManager");
        Method getService = serviceManager.getDeclaredMethod("getService", String.class);
        getService.setAccessible(true);
        IBinder binder = (IBinder) getService.invoke(null, Context.USB_SERVICE);
        if (binder == null) {
            throw new IllegalStateException("USB service unavailable");
        }

        Class<?> stub = Class.forName("android.hardware.usb.IUsbManager$Stub");
        Method asInterface = stub.getDeclaredMethod("asInterface", IBinder.class);
        asInterface.setAccessible(true);
        Object service = asInterface.invoke(null, binder);
        Class<?> usbManager = Class.forName("android.hardware.usb.IUsbManager");

        Bundle devices = new Bundle();
        usbManager.getMethod("getDeviceList", Bundle.class).invoke(service, devices);
        UsbDevice target = null;
        for (String key : devices.keySet()) {
            Object value = devices.get(key);
            if (value instanceof UsbDevice) {
                UsbDevice candidate = (UsbDevice) value;
                if (candidate.getVendorId() == VID && candidate.getProductId() == PID) {
                    target = candidate;
                    break;
                }
            }
        }
        if (target == null) {
            throw new IllegalStateException("TURZX panel unavailable");
        }

        // The session grant makes the current connection usable immediately.
        usbManager.getMethod("grantDevicePermission", UsbDevice.class, int.class)
                .invoke(service, target, uid);

        // Android 12+ supports an explicit persistent grant. Keep the default
        // package registration too so vendor Android 13 builds retain access
        // across a USB re-enumeration and a phone reboot.
        try {
            UserHandle user = (UserHandle) UserHandle.class.getMethod("of", int.class)
                    .invoke(null, userId);
            usbManager.getMethod("setDevicePersistentPermission", UsbDevice.class, int.class,
                    UserHandle.class, boolean.class)
                    .invoke(service, target, uid, user, true);
        } catch (NoSuchMethodException ignored) {
            // Older vendor frameworks still retain the default-package choice.
        }
        usbManager.getMethod("setDevicePackage", UsbDevice.class, String.class, int.class)
                .invoke(service, target, packageName, userId);
    }

    private static void allowHiddenApis() {
        try {
            Class<?> vmRuntime = Class.forName("dalvik.system.VMRuntime");
            Method getRuntime = vmRuntime.getDeclaredMethod("getRuntime");
            Method exempt = vmRuntime.getDeclaredMethod("setHiddenApiExemptions", String[].class);
            getRuntime.setAccessible(true);
            exempt.setAccessible(true);
            exempt.invoke(getRuntime.invoke(null), (Object) new String[]{"L"});
        } catch (Exception ignored) {
            // Root app_process is already exempt on many Android 13 builds.
        }
    }

    private static String shellQuote(String value) {
        return "'" + value.replace("'", "'\\''") + "'";
    }
}
