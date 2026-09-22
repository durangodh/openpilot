package ai.comma.remotehud;

import android.content.ContextWrapper;
import android.location.Location;
import android.os.Bundle;
import android.os.Looper;
import android.os.SystemClock;

/** In-memory callback test only: never registers or injects a system location. */
public final class GpsSourceDeviceCheck {
    private static void check(boolean value) { if (!value) throw new AssertionError(); }
    public static void main(String[] args) throws Exception {
        Looper.prepareMainLooper();
        GpsSourceMonitor monitor = new GpsSourceMonitor(new ContextWrapper(null) {
            @Override public Object getSystemService(String name) { return null; }
        });
        check(monitor.label().contains("권한"));
        for (String field : new String[]{"allowed", "enabled"}) {
            java.lang.reflect.Field f = GpsSourceMonitor.class.getDeclaredField(field);
            f.setAccessible(true);
            f.setBoolean(monitor, true);
        }
        check(monitor.label().equals("GPS 수신 대기"));
        Location location = new Location("gps");
        location.setElapsedRealtimeNanos(SystemClock.elapsedRealtimeNanos() - 100000000L);
        Bundle extras = new Bundle();
        extras.putString("source", "vehicle");
        extras.putBoolean("predicted", true);
        location.setExtras(extras);
        monitor.onLocationChanged(location);
        check(monitor.label().equals("GPS 차량 · 보간"));
        monitor.onLocationChanged(new Location("network"));
        check(monitor.label().equals("GPS 차량 · 보간"));
        extras = new Bundle();
        extras.putInt("satellites", 7);
        location.setExtras(extras);
        location.setElapsedRealtimeNanos(SystemClock.elapsedRealtimeNanos());
        monitor.onLocationChanged(location);
        check(monitor.label().equals("GPS S9"));
        monitor.stop();
        check(monitor.label().equals("GPS 수신 대기"));
        System.out.println("GPS Android callback: 6 checks passed; no system location injected");
    }
}
