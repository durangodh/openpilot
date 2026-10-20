package ai.comma.eonbleremote;

import android.app.Service;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothGatt;
import android.bluetooth.BluetoothGattCallback;
import android.bluetooth.BluetoothGattCharacteristic;
import android.bluetooth.BluetoothGattDescriptor;
import android.bluetooth.BluetoothGattService;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothProfile;
import android.content.Intent;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.SystemClock;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicLong;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/** Reads R1 BLE HID report notifications without Android's HID-host pairing. */
public final class BleService extends Service {
  private static final UUID HID_SERVICE = UUID.fromString("00001812-0000-1000-8000-00805f9b34fb");
  private static final UUID HID_REPORT = UUID.fromString("00002a4d-0000-1000-8000-00805f9b34fb");
  private static final UUID CCCD = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");
  private static final int PORT = 40983;
  private final Handler handler = new Handler(Looper.getMainLooper());
  private final ExecutorService sender = Executors.newSingleThreadExecutor();
  private final AtomicLong counter = new AtomicLong();
  private BluetoothAdapter adapter;
  private BluetoothGatt gatt;
  private BluetoothAdapter.LeScanCallback scanCallback;
  private final List<BluetoothGattDescriptor> descriptors = new ArrayList<>();
  private String key = "";
  private String target = "";
  private String current = "";
  private long leaseUntil;
  private boolean scanning;
  private boolean discovering;
  private boolean subscribing;
  private boolean privilegedDenied;
  private final Runnable gattTimeout = () -> {
    if (gatt != null && (discovering || subscribing)) {
      status(discovering ? "BLE 서비스 검색 시간 초과" : "BLE 알림 구독 시간 초과");
      disconnect();
      retry();
    }
  };

  private void armGattTimeout() {
    handler.removeCallbacks(gattTimeout);
    handler.postDelayed(gattTimeout, 10000);
  }

  @Override public IBinder onBind(Intent intent) { return null; }

  @Override public void onCreate() {
    super.onCreate();
    BluetoothManager manager = (BluetoothManager) getSystemService(BLUETOOTH_SERVICE);
    adapter = manager == null ? null : manager.getAdapter();
  }

  @Override public int onStartCommand(Intent intent, int flags, int startId) {
    if (intent == null) { stopSelf(); return START_NOT_STICKY; }
    String newKey = intent.getStringExtra("key");
    String newTarget = intent.getStringExtra("target");
    if (newKey == null || !newKey.matches("[0-9a-f]{32}")) { stopSelf(); return START_NOT_STICKY; }
    if (!key.isEmpty() && !key.equals(newKey)) { stopSelf(); return START_NOT_STICKY; }
    key = newKey;
    newTarget = newTarget == null ? "" : newTarget.toUpperCase(Locale.US);
    if (!newTarget.isEmpty() && !newTarget.matches("(?:[0-9A-F]{2}:){5}[0-9A-F]{2}")) {
      stopSelf(); return START_NOT_STICKY;
    }
    if (!target.equals(newTarget)) {
      target = newTarget;
      disconnect();
    }
    leaseUntil = SystemClock.elapsedRealtime() + 18000;
    handler.removeCallbacks(leaseCheck);
    handler.postDelayed(leaseCheck, 19000);
    if (privilegedDenied) {
      status("Android HID 접근 차단: BLE 앱에 시스템 권한 필요");
    } else if (adapter == null || !adapter.isEnabled()) {
      status("Bluetooth 꺼짐");
    } else if (gatt == null && !scanning) {
      scan();
    }
    return START_NOT_STICKY;
  }

  private final Runnable leaseCheck = () -> {
    if (SystemClock.elapsedRealtime() >= leaseUntil) stopSelf();
  };

  private static String advertisedName(byte[] data) {
    if (data == null) return "";
    for (int i = 0; i < data.length;) {
      int length = data[i] & 255;
      if (length == 0 || i + length >= data.length) break;
      int type = data[i + 1] & 255;
      if ((type == 8 || type == 9) && length > 1) {
        return new String(data, i + 2, length - 1, StandardCharsets.UTF_8);
      }
      i += length + 1;
    }
    return "";
  }

  @SuppressWarnings("deprecation")
  private void scan() {
    if (adapter == null || !adapter.isEnabled() || scanning || gatt != null) return;
    status("R1 BLE 검색 중");
    scanCallback = (device, rssi, record) -> {
      String name = device.getName();
      if (name == null) name = advertisedName(record);
      String address = device.getAddress().toUpperCase(Locale.US);
      if (!target.isEmpty() ? !target.equals(address) :
          (name == null || !name.toLowerCase(Locale.US).contains("magicsee"))) return;
      stopScan();
      current = address;
      status("R1 BLE 연결 중");
      try {
        // Android 6 can select BR/EDR for a dual-mode R1 with the 3-argument
        // overload. The HID report we need is on the LE GATT transport.
        gatt = Build.VERSION.SDK_INT >= Build.VERSION_CODES.M
            ? device.connectGatt(this, false, callback, BluetoothDevice.TRANSPORT_LE)
            : device.connectGatt(this, false, callback);
        if (gatt == null) { status("BLE 연결 실패"); retry(); }
      }
      catch (Exception e) { status("BLE 연결 실패"); retry(); }
    };
    try {
      scanning = adapter.startLeScan(scanCallback);
      if (!scanning) { status("BLE 검색 시작 실패"); retry(); }
      else handler.postDelayed(() -> {
        if (scanning) { stopScan(); status("R1 BLE 신호 없음"); retry(); }
      }, 12000);
    } catch (Exception e) { scanning = false; status("BLE 검색 권한 또는 위치 설정 확인"); retry(); }
  }

  @SuppressWarnings("deprecation")
  private void stopScan() {
    if (scanning && adapter != null && scanCallback != null) adapter.stopLeScan(scanCallback);
    scanning = false;
    scanCallback = null;
  }

  private void retry() {
    handler.postDelayed(() -> {
      if (SystemClock.elapsedRealtime() < leaseUntil && gatt == null && !scanning) scan();
    }, 3000);
  }

  private void disconnect() {
    stopScan();
    handler.removeCallbacks(gattTimeout);
    descriptors.clear();
    discovering = false;
    subscribing = false;
    if (gatt != null) { gatt.disconnect(); gatt.close(); gatt = null; }
    current = "";
  }

  private final BluetoothGattCallback callback = new BluetoothGattCallback() {
    @Override public void onConnectionStateChange(BluetoothGatt source, int statusCode, int state) {
      // connectGatt can call back before it returns. Run on the scan thread's
      // looper so gatt has been assigned before comparing the connection.
      handler.post(() -> {
        if (source != gatt) return;
        if (statusCode == BluetoothGatt.GATT_SUCCESS && state == BluetoothProfile.STATE_CONNECTED) {
          status("R1 BLE 서비스 검색 중");
          discovering = true;
          if (!source.discoverServices()) { status("BLE 서비스 검색 실패"); disconnect(); retry(); }
          else armGattTimeout();
        } else {
          status("R1 BLE 연결 끊김 (" + statusCode + ")");
          disconnect(); retry();
        }
      });
    }

    @Override public void onServicesDiscovered(BluetoothGatt source, int statusCode) {
      handler.post(() -> {
        if (source != gatt) return;
        discovering = false;
        handler.removeCallbacks(gattTimeout);
        if (statusCode != BluetoothGatt.GATT_SUCCESS) {
          status("BLE 서비스 검색 실패 (" + statusCode + ")"); disconnect(); retry(); return;
        }
        BluetoothGattService hid = source.getService(HID_SERVICE);
        if (hid == null) { status("R1 HID BLE 서비스 없음: M+B 확인"); disconnect(); retry(); return; }
        descriptors.clear();
        for (BluetoothGattCharacteristic characteristic : hid.getCharacteristics()) {
          if (!HID_REPORT.equals(characteristic.getUuid())) continue;
          if ((characteristic.getProperties() & BluetoothGattCharacteristic.PROPERTY_NOTIFY) == 0) continue;
          BluetoothGattDescriptor descriptor = characteristic.getDescriptor(CCCD);
          if (descriptor == null) continue;
          try {
            if (!source.setCharacteristicNotification(characteristic, true)) continue;
          } catch (SecurityException e) {
            // Marshmallow restricts HID report UUIDs to BLUETOOTH_PRIVILEGED.
            // A normal APK cannot receive this permission through pm grant.
            privilegedDenied = true;
            status("Android HID 접근 차단: BLE 앱에 시스템 권한 필요");
            disconnect();
            return;
          }
          descriptors.add(descriptor);
        }
        if (descriptors.isEmpty()) { status("R1 BLE 알림 특성 없음"); disconnect(); retry(); return; }
        subscribing = true;
        armGattTimeout();
        subscribeNext(source);
      });
    }

    @Override public void onDescriptorWrite(BluetoothGatt source, BluetoothGattDescriptor descriptor, int statusCode) {
      handler.post(() -> {
        if (source != gatt) return;
        if (statusCode != BluetoothGatt.GATT_SUCCESS) {
          status("R1 BLE 알림 구독 실패 (" + statusCode + ")"); disconnect(); retry(); return;
        }
        armGattTimeout();
        subscribeNext(source);
      });
    }

    @Override public void onCharacteristicChanged(BluetoothGatt source, BluetoothGattCharacteristic characteristic) {
      if (source != gatt || !HID_REPORT.equals(characteristic.getUuid())) return;
      byte[] report = characteristic.getValue();
      if (report != null && report.length > 0) {
        // Forward every HID report. The Python bridge validates the layout
        // before turning any bytes into driving controls.
        transmit(current, toHex(report));
      }
    }
  };

  private void subscribeNext(BluetoothGatt source) {
    if (!subscribing || source != gatt) return;
    if (descriptors.isEmpty()) {
      subscribing = false;
      handler.removeCallbacks(gattTimeout);
      status("R1 BLE 입력 대기");
      return;
    }
    BluetoothGattDescriptor descriptor = descriptors.remove(0);
    descriptor.setValue(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);
    try {
      if (!source.writeDescriptor(descriptor)) {
        status("R1 BLE 알림 구독 실패"); disconnect(); retry();
      }
    } catch (SecurityException e) {
      privilegedDenied = true;
      status("Android HID 접근 차단: BLE 앱에 시스템 권한 필요");
      disconnect();
    }
  }

  private static String toHex(byte[] bytes) {
    StringBuilder out = new StringBuilder(bytes.length * 2);
    for (byte b : bytes) out.append(String.format(Locale.US, "%02x", b & 255));
    return out.toString();
  }

  private void status(String value) { transmit("STATUS", toHex(value.getBytes(StandardCharsets.UTF_8))); }

  private void transmit(String address, String data) {
    if (key.isEmpty()) return;
    final String secret = key;
    final String body = address + "|" + counter.incrementAndGet() + "|" + data;
    sender.execute(() -> {
      try {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(hexBytes(secret), "HmacSHA256"));
        String packet = body + "|" + toHex(mac.doFinal(body.getBytes(StandardCharsets.US_ASCII)));
        byte[] bytes = packet.getBytes(StandardCharsets.US_ASCII);
        try (DatagramSocket socket = new DatagramSocket()) {
          socket.send(new DatagramPacket(bytes, bytes.length, InetAddress.getByName("127.0.0.1"), PORT));
        }
      } catch (Exception ignored) { }
    });
  }

  private static byte[] hexBytes(String text) {
    byte[] result = new byte[text.length() / 2];
    for (int i = 0; i < result.length; i++) result[i] = (byte) Integer.parseInt(text.substring(i * 2, i * 2 + 2), 16);
    return result;
  }

  @Override public void onDestroy() {
    handler.removeCallbacksAndMessages(null);
    disconnect();
    sender.shutdownNow();
    super.onDestroy();
  }
}
