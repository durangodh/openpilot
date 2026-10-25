package ai.comma.remotekey;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.provider.Settings;
import android.text.InputType;
import android.view.InputDevice;
import android.view.View;
import android.widget.*;
import java.util.ArrayList;

public final class MainActivity extends Activity {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private TextView status, keyLabel, battery;
    private LinearLayout bindings;
    private int displayedKey = -1;
    private final Runnable refresh = new Runnable() {
        public void run() {
            status.setText(RemoteControl.status());
            battery.setText(batteryText());
            if (displayedKey != RemoteControl.learnedKey) showBindings(RemoteControl.learnedKey);
            handler.postDelayed(this, 250);
        }
    };

    @Override public void onCreate(Bundle saved) { super.onCreate(saved); build(); }

    @Override protected void onResume() {
        super.onResume();
        RemoteControl.configuring = true;
        handler.post(refresh);
    }

    @Override protected void onPause() {
        handler.removeCallbacks(refresh);
        RemoteControl.configuring = false;
        super.onPause();
    }

    private TextView text(String value) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(17);
        view.setPadding(8, 12, 8, 12);
        return view;
    }

    private void button(LinearLayout root, String label, Runnable action) {
        Button b = new Button(this);
        b.setText(label);
        b.setMinHeight(64);
        b.setOnClickListener(v -> action.run());
        root.addView(b);
    }

    private String batteryText() {
        PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
        boolean exempt = power != null && power.isIgnoringBatteryOptimizations(getPackageName());
        return exempt ? "배터리 최적화: 제외됨 (정상)" : "배터리 최적화: 적용 중 → 아래 버튼으로 제외하세요";
    }

    private void build() {
        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(20, 16, 20, 24);
        scroll.addView(root);
        setContentView(scroll);
        root.addView(text("EON 리모컨 (S20 핫스팟 경유)"));
        root.addView(text("이 화면이 열려 있는 동안에는 버튼 확인만 하고 차량 명령은 보내지 않습니다."));
        status = text(RemoteControl.status());
        root.addView(status);

        button(root, "1. 블루투스 설정 (리모컨 페어링)", () -> startActivity(new Intent(Settings.ACTION_BLUETOOTH_SETTINGS)));
        button(root, "2. 접근성 → EON 리모컨 입력 켜기", () -> startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)));
        root.addView(text("켜기가 막히면: 설정 → 애플리케이션 → EON 리모컨 → ⋮ → 제한된 설정 허용 후 다시 켜세요."));
        battery = text(batteryText());
        root.addView(battery);
        button(root, "3. 배터리 최적화 제외", () -> {
            try {
                startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                        Uri.parse("package:" + getPackageName())));
            } catch (Exception e) {
                startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
            }
        });
        button(root, "4. 연결된 입력 장치 새로고침", this::build);

        ArrayList<String> names = new ArrayList<>(), descriptors = new ArrayList<>();
        names.add("리모컨 선택");
        descriptors.add("");
        int selected = 0;
        for (int id : InputDevice.getDeviceIds()) {
            InputDevice device = InputDevice.getDevice(id);
            if (!RemoteControl.external(device)) continue;
            names.add(device.getName());
            descriptors.add(device.getDescriptor());
            if (RemoteControl.selected(this, device)) selected = names.size() - 1;
        }
        String savedDevice = RemoteControl.prefs(this).getString("device", "");
        if (selected == 0 && !savedDevice.isEmpty()) {
            descriptors.add(savedDevice);
            names.add(RemoteControl.prefs(this).getString("deviceName", "저장된 리모컨") + " (연결 안 됨)");
            selected = names.size() - 1;
        }
        Spinner devices = new Spinner(this);
        devices.setAdapter(new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, names));
        devices.setSelection(selected);
        root.addView(devices);
        devices.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            public void onNothingSelected(AdapterView<?> p) { }
            public void onItemSelected(AdapterView<?> p, View v, int position, long id) {
                String descriptor = descriptors.get(position);
                if (!descriptor.equals(RemoteControl.prefs(MainActivity.this).getString("device", ""))) {
                    // Mappings must be learned again for a different device.
                    String secret = RemoteControl.prefs(MainActivity.this).getString("secret", "");
                    boolean enabled = RemoteControl.prefs(MainActivity.this).getBoolean("enabled", false);
                    RemoteControl.prefs(MainActivity.this).edit().clear().putString("secret", secret)
                            .putBoolean("enabled", enabled)
                            .putString("device", descriptor).putString("deviceName", names.get(position)).apply();
                    RemoteControl.learnedKey = -1;
                    build();
                }
            }
        });

        root.addView(text("5. EON 설정 → REMOTE PAIR 에 표시되는 연결 키 입력"));
        EditText secret = new EditText(this);
        secret.setSingleLine(true);
        secret.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        secret.setHint("8자리 연결 키");
        secret.setText(RemoteControl.prefs(this).getString("secret", ""));
        root.addView(secret);
        button(root, "연결 키 저장", () -> {
            String key = secret.getText().toString().trim().toLowerCase(java.util.Locale.ROOT);
            if (!RelayProtocol.key(key)) {
                secret.setError("8자리 영문 a–f / 숫자를 입력하세요");
                return;
            }
            RemoteControl.prefs(this).edit().putString("secret", key).apply();
            Toast.makeText(this, "연결 키 저장됨", Toast.LENGTH_SHORT).show();
        });

        root.addView(text("6. 리모컨 버튼을 누르고 동작 지정\n길게 누르기: 0.7초. 두 번 누르기를 지정하면 그 버튼의 짧게 누르기는 최대 0.35초 늦어집니다. "
                + "빠른 반응이 필요한 버튼(RES/SET/차선변경)에는 두 번 누르기를 지정하지 마세요.\n"
                + "차선변경 요청은 방향지시등을 켜지 않으며 기존 속도·차선·사각지대 조건을 따릅니다."));
        keyLabel = text("");
        root.addView(keyLabel);
        bindings = new LinearLayout(this);
        bindings.setOrientation(LinearLayout.VERTICAL);
        root.addView(bindings);
        displayedKey = -2;
        showBindings(RemoteControl.learnedKey);

        Switch enabled = new Switch(this);
        enabled.setText("리모컨 조작 사용");
        enabled.setTextSize(20);
        enabled.setChecked(RemoteControl.prefs(this).getBoolean("enabled", false));
        enabled.setOnCheckedChangeListener((v, checked) -> RemoteControl.prefs(this).edit().putBoolean("enabled", checked).apply());
        root.addView(enabled);
        root.addView(text("잠금 시험: 화면을 끄고 리모컨을 몇 번 누른 뒤 이 화면을 다시 열면 '잠금 중 받은 입력'이 늘어 있어야 합니다. "
                + "늘지 않는 버튼은 잠금 중에 쓸 수 없으니 다른 버튼에 기능을 배정하세요."));
        button(root, "잠금 시험 횟수 초기화", () -> RemoteControl.lockedInputs = 0);
    }

    private void showBindings(int key) {
        displayedKey = key;
        bindings.removeAllViews();
        keyLabel.setText(key < 0 ? "선택한 리모컨의 버튼을 눌러주세요" : android.view.KeyEvent.keyCodeToString(key));
        if (key < 0) return;
        String[] gestures = {"short", "long", "double"};
        String[] labels = {"짧게 누르기", "길게 누르기", "두 번 누르기"};
        for (int i = 0; i < gestures.length; i++) {
            String gesture = gestures[i];
            bindings.addView(text(labels[i]));
            Spinner select = new Spinner(this);
            select.setAdapter(new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, RemoteControl.LABELS));
            String action = RemoteControl.mapping(this, key, gesture);
            for (int n = 0; n < RemoteControl.ACTIONS.length; n++) if (RemoteControl.ACTIONS[n].equals(action)) select.setSelection(n);
            select.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
                public void onNothingSelected(AdapterView<?> p) { }
                public void onItemSelected(AdapterView<?> p, View v, int position, long id) {
                    RemoteControl.prefs(MainActivity.this).edit().putString(key + "." + gesture, RemoteControl.ACTIONS[position]).apply();
                }
            });
            bindings.addView(select);
        }
    }
}
