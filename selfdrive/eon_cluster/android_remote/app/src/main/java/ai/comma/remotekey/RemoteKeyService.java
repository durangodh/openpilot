package ai.comma.remotekey;

import android.accessibilityservice.AccessibilityService;
import android.app.KeyguardManager;
import android.content.SharedPreferences;
import android.hardware.input.InputManager;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.view.KeyEvent;
import android.view.accessibility.AccessibilityEvent;
import java.util.HashSet;
import java.util.Set;

/** Only the explicitly selected external device and mapped keys are consumed. */
public final class RemoteKeyService extends AccessibilityService implements InputManager.InputDeviceListener,
        SharedPreferences.OnSharedPreferenceChangeListener {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Set<Integer> consumed = new HashSet<>();
    private RemoteKeyEngine engine;
    private InputManager inputs;
    private KeyguardManager keyguard;
    private RelayClient relay;
    private final Runnable tick = new Runnable() {
        public void run() {
            if (!RemoteControl.drivingAllowed()) engine.blockDrivingHolds();
            if (RemoteControl.enabled(RemoteKeyService.this)) engine.tick(SystemClock.uptimeMillis());
            else engine.reset();
            handler.postDelayed(this, 50);
        }
    };

    @Override protected void onServiceConnected() {
        engine = new RemoteKeyEngine((key, gesture) -> RemoteControl.mapping(this, key, gesture),
                action -> RemoteControl.send(this, action));
        inputs = (InputManager) getSystemService(INPUT_SERVICE);
        inputs.registerInputDeviceListener(this, handler);
        keyguard = (KeyguardManager) getSystemService(KEYGUARD_SERVICE);
        RemoteControl.prefs(this).registerOnSharedPreferenceChangeListener(this);
        relay = new RelayClient(this);
        relay.start();
        RemoteControl.client = relay;
        RemoteControl.connected = true;
        handler.removeCallbacks(tick);
        handler.post(tick);
    }

    @Override protected boolean onKeyEvent(KeyEvent event) {
        if (engine == null || !RemoteControl.selected(this, event.getDevice()) || !RemoteControl.keyAllowed(event.getKeyCode())) return false;
        int key = event.getKeyCode();
        boolean down = event.getAction() == KeyEvent.ACTION_DOWN;
        boolean up = event.getAction() == KeyEvent.ACTION_UP;
        if (!down && !up) return false;
        long now = SystemClock.uptimeMillis();
        // Reject queued old events; do not turn an unmatched release into a command.
        if (now - event.getEventTime() > 400 || now < event.getEventTime()) {
            engine.reset();
            return consumed.contains(key);
        }
        if (up && consumed.remove(key)) {
            if (!RemoteControl.configuring) engine.release(key, now, event.isCanceled());
            return true;
        }
        if (!RemoteControl.configuring && (!RemoteControl.enabled(this) || !RemoteControl.mapped(this, key))) return false;
        boolean locked = keyguard != null && keyguard.isKeyguardLocked();
        RemoteControl.lastInput = KeyEvent.keyCodeToString(key) + " / " + (down ? "누름" : "뗌") + (locked ? " (잠금 중)" : "");
        if (down) {
            consumed.add(key);
            if (locked && event.getRepeatCount() == 0) RemoteControl.lockedInputs++;
            if (RemoteControl.configuring) {
                RemoteControl.learnedKey = key;
                engine.reset();
            } else if (event.getRepeatCount() == 0) {
                engine.press(key, now);
                if (!RemoteControl.drivingAllowed()) engine.blockDrivingHolds();
            }
        }
        return true;
    }

    @Override public void onAccessibilityEvent(AccessibilityEvent event) { }
    @Override public void onInterrupt() { if (engine != null) engine.reset(); }
    @Override public void onInputDeviceRemoved(int id) { if (engine != null) engine.reset(); consumed.clear(); }
    @Override public void onInputDeviceChanged(int id) { if (engine != null) engine.reset(); }
    @Override public void onInputDeviceAdded(int id) { }
    @Override public void onSharedPreferenceChanged(SharedPreferences prefs, String key) { if (engine != null) engine.reset(); }

    @Override public void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        if (inputs != null) inputs.unregisterInputDeviceListener(this);
        RemoteControl.prefs(this).unregisterOnSharedPreferenceChangeListener(this);
        RemoteControl.connected = false;
        RemoteControl.client = null;
        if (relay != null) relay.stop();
        super.onDestroy();
    }
}
