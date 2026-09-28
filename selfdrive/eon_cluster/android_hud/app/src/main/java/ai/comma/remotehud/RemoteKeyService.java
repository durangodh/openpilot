package ai.comma.remotehud;

import android.accessibilityservice.AccessibilityService;
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
    private long generation = -1;
    private void checkGeneration() {
        long current = RemoteControl.generation();
        if (current != generation) { engine.reset(); generation = current; }
    }
    private final Runnable tick = new Runnable() {
        public void run() {
            checkGeneration();
            if (!RemoteControl.drivingAllowed) engine.blockDrivingHolds();
            if (RemoteControl.enabled(RemoteKeyService.this) && RemoteControl.ready()) engine.tick(SystemClock.uptimeMillis());
            else engine.reset();
            handler.postDelayed(this, 50);
        }
    };
    @Override protected void onServiceConnected() {
        engine = new RemoteKeyEngine((key, gesture) -> RemoteControl.mapping(this, key, gesture),
                action -> RemoteControl.send(this, action));
        inputs = (InputManager) getSystemService(INPUT_SERVICE);
        inputs.registerInputDeviceListener(this, handler);
        RemoteControl.prefs(this).registerOnSharedPreferenceChangeListener(this);
        RemoteControl.connected = true;
        handler.removeCallbacks(tick); handler.post(tick);
    }
    @Override protected boolean onKeyEvent(KeyEvent event) {
        if (engine == null || !RemoteControl.selected(this, event.getDevice()) || !RemoteControl.keyAllowed(event.getKeyCode())) return false;
        checkGeneration();
        int key = event.getKeyCode();
        boolean down = event.getAction() == KeyEvent.ACTION_DOWN;
        boolean up = event.getAction() == KeyEvent.ACTION_UP;
        if (!down && !up) return false;
        long now = SystemClock.uptimeMillis();
        if (now - event.getEventTime() > 400 || now < event.getEventTime()) {
            engine.reset(); return consumed.contains(key);
        }
        if (up && consumed.remove(key)) {
            if (!RemoteControl.configuring) engine.release(key, SystemClock.uptimeMillis(), event.isCanceled());
            return true;
        }
        if (!RemoteControl.configuring && (!RemoteControl.enabled(this) || !RemoteControl.mapped(this, key))) return false;
        // Reject queued old events; do not turn an unmatched release into a command.
        RemoteControl.lastInput = KeyEvent.keyCodeToString(key) + " / " + (down ? "누름" : "뗌");
        if (down) {
            consumed.add(key);
            if (RemoteControl.configuring) { RemoteControl.learnedKey = key; engine.reset(); }
            else if (event.getRepeatCount() == 0 && RemoteControl.ready()) {
                engine.press(key, now);
                if (!RemoteControl.drivingAllowed) engine.blockDrivingHolds();
            }
        }
        return true;
    }
    @Override public void onAccessibilityEvent(AccessibilityEvent event) { }
    @Override public void onInterrupt() { reset(); }
    private void reset() { if (engine != null) engine.reset(); RemoteControl.reset(); }
    @Override public void onInputDeviceRemoved(int id) { reset(); consumed.clear(); }
    @Override public void onInputDeviceChanged(int id) { reset(); }
    @Override public void onInputDeviceAdded(int id) { }
    @Override public void onSharedPreferenceChanged(SharedPreferences prefs, String key) { reset(); }
    @Override public void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        if (inputs != null) inputs.unregisterInputDeviceListener(this);
        RemoteControl.prefs(this).unregisterOnSharedPreferenceChangeListener(this);
        RemoteControl.connected = false; reset(); super.onDestroy();
    }
}
