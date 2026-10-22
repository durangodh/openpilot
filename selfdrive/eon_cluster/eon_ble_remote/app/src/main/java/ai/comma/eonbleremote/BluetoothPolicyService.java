package ai.comma.eonbleremote;

import android.app.Service;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.util.Log;

/** EON Android 6 pairing policy; separate from the privileged HID receiver. */
public final class BluetoothPolicyService extends Service {
  private final Handler handler = new Handler(Looper.getMainLooper());
  private BluetoothAdapter adapter;
  private String target = "";
  // 임대 없음: Python 은 처음과 대상 R1 이 바뀔 때만 시작한다(주기적 `am` 실행이 EON 과부하 원인).

  private final BroadcastReceiver changes = new BroadcastReceiver() {
    @Override public void onReceive(Context context, Intent intent) {
      if (BluetoothAdapter.ACTION_STATE_CHANGED.equals(intent.getAction())) {
        int state = intent.getIntExtra(BluetoothAdapter.EXTRA_STATE, BluetoothAdapter.ERROR);
        if (state == BluetoothAdapter.STATE_ON) enforce();
        else if (state == BluetoothAdapter.STATE_OFF) handler.postDelayed(() -> ensureEnabled(), 1000);
        return;
      }
      BluetoothDevice device = intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE);
      if (device == null || !R1OnlyPolicy.rejects(target, device.getAddress())) return;
      if (BluetoothDevice.ACTION_PAIRING_REQUEST.equals(intent.getAction()) && isOrderedBroadcast()) {
        abortBroadcast();
      }
      reject(device);
    }
  };

  @Override public void onCreate() {
    super.onCreate();
    adapter = BluetoothAdapter.getDefaultAdapter();
    IntentFilter filter = new IntentFilter();
    filter.addAction(BluetoothAdapter.ACTION_STATE_CHANGED);
    filter.addAction(BluetoothDevice.ACTION_PAIRING_REQUEST);
    filter.addAction(BluetoothDevice.ACTION_BOND_STATE_CHANGED);
    filter.addAction(BluetoothDevice.ACTION_ACL_CONNECTED);
    filter.setPriority(1000);
    registerReceiver(changes, filter);
  }

  @Override public IBinder onBind(Intent intent) { return null; }

  @Override public int onStartCommand(Intent intent, int flags, int startId) {
    if (intent == null) { stopSelf(); return START_NOT_STICKY; }
    target = R1OnlyPolicy.address(intent.getStringExtra("target"));
    ensureEnabled();
    return START_NOT_STICKY;
  }

  @SuppressWarnings("deprecation")
  private void ensureEnabled() {
    if (adapter == null) return;
    try {
      if (!adapter.isEnabled()) {
        if (!adapter.enable()) Log.w("EonR1Policy", "Bluetooth enable request failed");
      } else enforce();
    } catch (SecurityException e) { Log.e("EonR1Policy", "Bluetooth permission denied", e); }
  }

  private void enforce() {
    if (adapter == null || target.isEmpty()) return;  // Preserve pairing until an R1 is learned.
    try {
      for (BluetoothDevice device : adapter.getBondedDevices()) {
        if (R1OnlyPolicy.rejects(target, device.getAddress())) reject(device);
      }
    } catch (SecurityException e) { Log.e("EonR1Policy", "Cannot inspect paired devices", e); }
  }

  private void reject(BluetoothDevice device) {
    try {
      int state = device.getBondState();
      if (state == BluetoothDevice.BOND_BONDING) {
        BluetoothDevice.class.getMethod("cancelBondProcess").invoke(device);
      } else if (state == BluetoothDevice.BOND_BONDED) {
        Object result = BluetoothDevice.class.getMethod("removeBond").invoke(device);
        if (!Boolean.TRUE.equals(result)) Log.w("EonR1Policy", "Unpair request rejected");
      }
    } catch (Exception e) { Log.e("EonR1Policy", "Cannot reject non-R1 pairing", e); }
  }

  @Override public void onDestroy() {
    handler.removeCallbacksAndMessages(null);
    unregisterReceiver(changes);
    super.onDestroy();
  }
}
