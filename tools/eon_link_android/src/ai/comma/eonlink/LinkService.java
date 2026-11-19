package ai.comma.eonlink;

import android.app.*;
import android.content.*;
import android.content.pm.ServiceInfo;
import android.net.wifi.WifiManager;
import android.os.*;
import io.zoompilot.jetlink.server.Native;
import org.json.*;
import java.io.*;
import java.net.*;
import java.util.*;
import java.util.concurrent.*;

/** Explicitly started foreground service; never starts vehicle control. */
public final class LinkService extends Service {
  public static final String STOP = "stop", DOWNLOAD = "download", PREPARE = "prepare", IMPORT = "import";
  public static volatile String status = "중지됨", snapshot = "", logs = "";
  private final ExecutorService work = Executors.newSingleThreadExecutor();
  private volatile boolean alive, ready;
  private Relay relay;
  private PowerManager.WakeLock cpu;
  private WifiManager.WifiLock wifi;
  private Thread poller;
  private final PowerManager.OnThermalStatusChangedListener thermalListener = this::thermal;

  @Override public void onCreate() {
    super.onCreate(); alive = true;
    NotificationManager manager = getSystemService(NotificationManager.class);
    manager.createNotificationChannel(new NotificationChannel("eonlink", "이온링크 연결", NotificationManager.IMPORTANCE_LOW));
    PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
    PendingIntent stop = PendingIntent.getService(this, 1, new Intent(this, LinkService.class).setAction(STOP), PendingIntent.FLAG_IMMUTABLE);
    Notification n = new Notification.Builder(this, "eonlink").setSmallIcon(ai.comma.eonlink.R.drawable.link_icon)
      .setContentTitle("이온링크").setContentText("N16 연산 제공 · 모델 선택은 이온웹")
      .setContentIntent(open).setOngoing(true).addAction(new Notification.Action.Builder(null, "중지", stop).build()).build();
    startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
    cpu = getSystemService(PowerManager.class).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "eonlink:serving");
    cpu.setReferenceCounted(false); cpu.acquire();
    wifi = ((WifiManager)getApplicationContext().getSystemService(WIFI_SERVICE))
      .createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "eonlink:wifi");
    wifi.setReferenceCounted(false); wifi.acquire();
    getSystemService(PowerManager.class).addThermalStatusListener(thermalListener);
    work.execute(this::startEngine);
  }
  private void startEngine() {
    status = "엔진 시작 중";
    try {
      JSONObject config = new JSONObject();
      config.put("cache", new File(getFilesDir(), "engine/cache").getAbsolutePath());
      config.put("backend", "ort"); config.put("device", "htp-whole");
      config.put("keep_alive", true); config.put("keep_cpu_warm", false);
      config.put("npu_shared_memory", false); config.put("experiments", new JSONArray());
      config.put("experiment_sweep", false); config.put("port", Relay.ENGINE);
      config.put("listen", true); config.put("usb", false); config.put("preload", true);
      config.put("chip", Build.SOC_MODEL); config.put("firmware", Build.FINGERPRINT);
      config.put("native_library_dir", getApplicationInfo().nativeLibraryDir);
      String error = Native.start(config.toString());
      if (error != null) throw new IOException(error);
      if (!alive) { Native.stop(); return; }
      relay = new Relay(text -> status = text, Relay.ENGINE);
      relay.start(Relay.PORT); relay.startDiscovery(); ready = true;
      status = "연결 대기 · TCP 5600 · 자동 검색 5601";
      poller = new Thread(() -> {
        long version = -1, nextLog = 0;
        while (alive) {
          try {
            String text = Native.snapshot(version, 1000);
            if (text != null && !text.isEmpty()) {
              snapshot = text; JSONObject snap = new JSONObject(text); version = snap.optLong("version", version);
              JSONArray imports = snap.optJSONArray("imports");
              if (imports != null) for (int i = 0; i < imports.length(); i++) {
                JSONObject job = imports.optJSONObject(i); if (job == null) continue;
                String state = job.optString("state"); File imported = new File(job.optString("path"));
                if ((state.equals("done") || state.equals("failed")) && getFilesDir().equals(imported.getParentFile())
                    && imported.getName().startsWith("n16-import-")) imported.delete();
              }
            }
            JSONObject report = new JSONObject(Native.logs(nextLog));
            nextLog = report.optLong("next", nextLog);
            JSONArray lines = report.optJSONArray("lines");
            if (lines != null && lines.length() > 0) {
              StringBuilder b = new StringBuilder(logs);
              for (int i = 0; i < lines.length(); i++) b.append(lines.getString(i)).append('\n');
              logs = b.substring(Math.max(0, b.length() - 16000));
            }
          } catch (Exception e) { status = "상태 읽기 오류: " + e.getMessage(); }
        }
      }, "eon-status"); poller.start();
    } catch (Throwable e) { status = "시작 실패: " + e.getMessage(); stopSelf(); }
  }
  private void thermal(int level) {
    if (ready) Native.reportThermal(level >= PowerManager.THERMAL_STATUS_CRITICAL ? "critical" :
      level >= PowerManager.THERMAL_STATUS_SEVERE ? "serious" : level >= PowerManager.THERMAL_STATUS_MODERATE ? "fair" : "nominal");
  }
  @Override public int onStartCommand(Intent intent, int flags, int startId) {
    String action = intent == null ? null : intent.getAction();
    if (STOP.equals(action)) { stopSelf(); return START_NOT_STICKY; }
    if (DOWNLOAD.equals(action) || PREPARE.equals(action) || IMPORT.equals(action)) {
      final String path = intent.getStringExtra("path");
      work.execute(() -> {
        if (!ready || !alive) return;
        if (relay != null && relay.hasClient()) { status = "이온 시험을 중지한 뒤 모델을 변경하세요"; return; }
        try {
          JSONObject command = new JSONObject().put("cmd", action);
          if (IMPORT.equals(action)) command.put("path", path);
          else command.put("sha256", Relay.SHA);
          JSONObject reply = new JSONObject(Native.command(command.toString()));
          if (IMPORT.equals(action) && !reply.optBoolean("ok") && path != null) new File(path).delete();
          status = reply.optBoolean("ok") ? "요청됨 · 아래 진행 상태를 확인하세요" : "요청 실패: " + reply.optString("error");
        } catch (Exception e) { status = "요청 실패: " + e.getMessage(); }
      });
    }
    return START_NOT_STICKY;
  }
  @Override public void onDestroy() {
    alive = false; ready = false;
    getSystemService(PowerManager.class).removeThermalStatusListener(thermalListener);
    if (relay != null) relay.close();
    if (wifi != null && wifi.isHeld()) wifi.release();
    if (cpu != null && cpu.isHeld()) cpu.release();
    work.execute(() -> { try { Native.stop(); } catch (Throwable ignored) {} });
    work.shutdown(); status = status.startsWith("시작 실패") ? status : "중지됨";
    super.onDestroy();
  }
  @Override public IBinder onBind(Intent intent) { return null; }
  public static String addresses() {
    ArrayList<String> found = new ArrayList<>();
    try {
      Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
      while (interfaces.hasMoreElements()) {
        NetworkInterface n = interfaces.nextElement();
        if (!n.isUp() || n.isLoopback()) continue;
        for (InterfaceAddress a : n.getInterfaceAddresses()) if (a.getAddress() instanceof Inet4Address && a.getAddress().isSiteLocalAddress())
          found.add(a.getAddress().getHostAddress() + ":" + Relay.PORT);
      }
    } catch (SocketException ignored) {}
    return found.isEmpty() ? "같은 핫스팟의 Wi-Fi에 연결하세요" : android.text.TextUtils.join("\n", found);
  }
}
