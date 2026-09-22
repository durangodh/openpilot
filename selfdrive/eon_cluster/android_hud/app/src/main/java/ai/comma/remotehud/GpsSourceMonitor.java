package ai.comma.remotehud;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;

/** Passive only: does not turn on GPS, inject positions, or change navigation. */
final class GpsSourceMonitor implements LocationListener {
    private final Context context;
    private final LocationManager manager;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private volatile Location lastGps;
    private volatile boolean registered;
    private volatile boolean allowed;
    private volatile boolean enabled;
    private volatile boolean failed;
    private final Runnable refresh = new Runnable() {
        @Override public void run() {
            allowed = hasPermissions(context);
            enabled = manager != null && (Build.VERSION.SDK_INT >= 28
                    ? manager.isLocationEnabled() : manager.isProviderEnabled(LocationManager.GPS_PROVIDER));
            if (!allowed || !enabled) {
                unregister();
                lastGps = null;
            } else if (!registered) {
                try {
                    manager.requestLocationUpdates(LocationManager.PASSIVE_PROVIDER,
                            0L, 0f, GpsSourceMonitor.this, Looper.getMainLooper());
                    registered = true;
                    failed = false;
                } catch (RuntimeException e) {
                    failed = true;
                }
            }
            handler.postDelayed(this, 2000L);
        }
    };

    GpsSourceMonitor(Context context) {
        this.context = context;
        manager = (LocationManager) context.getSystemService(Context.LOCATION_SERVICE);
    }
    static boolean hasPermissions(Context context) {
        return context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                == PackageManager.PERMISSION_GRANTED
                && (Build.VERSION.SDK_INT < 29 || context.checkSelfPermission(
                    Manifest.permission.ACCESS_BACKGROUND_LOCATION) == PackageManager.PERMISSION_GRANTED);
    }
    void start() { handler.post(refresh); }
    void stop() { handler.removeCallbacks(refresh); unregister(); lastGps = null; }
    private void unregister() {
        if (registered) {
            try { manager.removeUpdates(this); } catch (RuntimeException ignored) { }
            registered = false;
        }
    }
    @Override public void onLocationChanged(Location location) {
        if (!LocationManager.GPS_PROVIDER.equals(location.getProvider())) return;
        Location previous = lastGps;
        if (previous == null || location.getElapsedRealtimeNanos() > previous.getElapsedRealtimeNanos()) {
            lastGps = new Location(location);
        }
    }
    int sourceKind() {
        if (!allowed) return -1;
        if (!enabled) return -2;
        if (failed) return -3;
        Location location = lastGps;
        if (location == null) return GpsSourcePolicy.WAITING;
        Bundle extras = location.getExtras();
        String source = extras == null ? null : extras.getString("source");
        int satellites = extras == null ? 0 : extras.getInt("satellites", 0);
        return GpsSourcePolicy.current(GpsSourcePolicy.classify(location.getProvider(),
                source, satellites, location.isFromMockProvider()),
                location.getElapsedRealtimeNanos() / 1000000L, SystemClock.elapsedRealtime());
    }
    boolean isPredicted() {
        Location location = lastGps;
        return location != null && location.getExtras() != null
                && location.getExtras().getBoolean("predicted", false);
    }
    String label() {
        int kind = sourceKind();
        if (kind == -1) return "GPS 위치 권한 필요";
        if (kind == -2) return "GPS 위치 꺼짐";
        if (kind == -3) return "GPS 확인 오류";
        String label = GpsSourcePolicy.label(kind);
        if (kind == GpsSourcePolicy.VEHICLE && isPredicted()) {
            label += " · 보간";
        }
        return label;
    }
    @Override public void onProviderEnabled(String provider) { }
    @Override public void onProviderDisabled(String provider) { lastGps = null; }
    @Override public void onStatusChanged(String provider, int status, Bundle extras) { }
}
