package ai.comma.remotehud;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.InputType;
import android.view.InputDevice;
import android.view.View;
import android.widget.*;
import java.util.ArrayList;

public final class RemoteSettingsActivity extends Activity {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private TextView status, keyLabel;
    private LinearLayout bindings;
    private int displayedKey = -1;
    private final Runnable refresh = new Runnable() {
        public void run() {
            status.setText(RemoteControl.status());
            if (displayedKey != RemoteControl.learnedKey) showBindings(RemoteControl.learnedKey);
            handler.postDelayed(this, 250);
        }
    };
    @Override public void onCreate(Bundle saved) { super.onCreate(saved); build(); }
    @Override protected void onResume() {
        super.onResume(); RemoteControl.configuring = true; RemoteControl.reset(); handler.post(refresh);
    }
    @Override protected void onPause() {
        handler.removeCallbacks(refresh); RemoteControl.reset(); super.onPause();
    }
    @Override public void finish() { RemoteControl.configuring = false; RemoteControl.reset(); super.finish(); }
    private TextView text(String value) {
        TextView view = new TextView(this); view.setText(value); view.setTextSize(18); view.setPadding(8, 12, 8, 12); return view;
    }
    private void button(LinearLayout root, String label, Runnable action) {
        Button b = new Button(this); b.setText(label); b.setMinHeight(64); b.setOnClickListener(v -> action.run()); root.addView(b);
    }
    private void build() {
        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setPadding(20, 16, 20, 24);
        scroll.addView(root); setContentView(scroll);
        root.addView(text("무선 리모컨 · 오픈파일럿"));
        root.addView(text("이 화면에서는 버튼 확인만 하며 차량 명령은 보내지 않습니다.\nS9에 블루투스 키보드/미디어 버튼으로 연결되는 리모컨을 사용하세요. KEY1 배선형·터치 전용 리모컨은 지원하지 않습니다."));
        button(root, "1. 블루투스 연결", () -> startActivity(new Intent(Settings.ACTION_BLUETOOTH_SETTINGS)));
        button(root, "2. 접근성 → EON 리모컨 입력 켜기", () -> startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)));
        root.addView(text("권한이 차단되면 Android 앱 정보 → ⋮ → 제한된 설정 허용 후 다시 켜세요. 다른 접근성 서비스가 키 입력을 먼저 처리하면 버튼이 표시되지 않을 수 있습니다."));
        button(root, "3. 연결된 입력 장치 새로고침", this::build);
        ArrayList<String> names = new ArrayList<>(), descriptors = new ArrayList<>();
        names.add("리모컨 선택"); descriptors.add("");
        int selected = 0;
        for (int id : InputDevice.getDeviceIds()) {
            InputDevice device = InputDevice.getDevice(id);
            if (!RemoteControl.external(device)) continue;
            names.add(device.getName()); descriptors.add(device.getDescriptor());
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
        devices.setSelection(selected); root.addView(devices);
        devices.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            public void onNothingSelected(AdapterView<?> p) { }
            public void onItemSelected(AdapterView<?> p, View v, int position, long id) {
                String descriptor = descriptors.get(position);
                if (!descriptor.equals(RemoteControl.prefs(RemoteSettingsActivity.this).getString("device", ""))) {
                    // Mappings must be learned again for a different device.
                    String secret = RemoteControl.prefs(RemoteSettingsActivity.this).getString("secret", "");
                    RemoteControl.prefs(RemoteSettingsActivity.this).edit().clear().putString("secret", secret)
                            .putString("device", descriptor).putString("deviceName", names.get(position)).apply();
                    RemoteControl.learnedKey = -1;
                    build();
                }
            }
        });
        root.addView(text("4. EON 설정 → REMOTE PAIR에 표시되는 연결 키 입력"));
        EditText secret = new EditText(this);
        secret.setSingleLine(true); secret.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        secret.setHint("32자리 연결 키"); secret.setText(RemoteControl.prefs(this).getString("secret", "")); root.addView(secret);
        button(root, "연결 키 저장", () -> {
            String key = secret.getText().toString().trim().toLowerCase(java.util.Locale.ROOT);
            if (!RemoteCommandProtocol.hex(key, 32)) { secret.setError("32자리 영문 a–f / 숫자를 입력하세요"); return; }
            RemoteControl.prefs(this).edit().putString("secret", key).apply();
            Toast.makeText(this, "연결 키 저장됨", Toast.LENGTH_SHORT).show();
        });
        root.addView(text("5. 리모컨 버튼을 누르고 동작 지정\n길게 누르기: 0.7초. 속도 ±는 0.5초마다 1단계 반복합니다. 두 번 누르기를 지정하면 짧게 누르기는 최대 0.35초 기다립니다.\n차선변경 요청은 방향지시등을 켜지 않습니다. 기존 속도·차선·사각지대 조건을 따릅니다."));
        keyLabel = text(""); root.addView(keyLabel);
        bindings = new LinearLayout(this); bindings.setOrientation(LinearLayout.VERTICAL); root.addView(bindings);
        displayedKey = -2; showBindings(RemoteControl.learnedKey);
        Switch enabled = new Switch(this); enabled.setText("리모컨 조작 사용"); enabled.setTextSize(20);
        enabled.setChecked(RemoteControl.prefs(this).getBoolean("enabled", false));
        enabled.setOnCheckedChangeListener((v, checked) -> RemoteControl.prefs(this).edit().putBoolean("enabled", checked).apply());
        root.addView(enabled);
        status = text(RemoteControl.status()); root.addView(status);
        button(root, "설정 완료", this::finish);
    }
    private void showBindings(int key) {
        displayedKey = key; bindings.removeAllViews();
        keyLabel.setText(key < 0 ? "선택한 리모컨의 버튼을 눌러주세요" : android.view.KeyEvent.keyCodeToString(key));
        if (key < 0) return;
        String[] gestures = {"short", "long", "double"};
        String[] labels = {"짧게 누르기", "길게 누르기", "두 번 누르기"};
        for (int i = 0; i < gestures.length; i++) {
            String gesture = gestures[i]; bindings.addView(text(labels[i]));
            Spinner select = new Spinner(this);
            select.setAdapter(new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, RemoteControl.LABELS));
            String action = RemoteControl.mapping(this, key, gesture);
            for (int n = 0; n < RemoteControl.ACTIONS.length; n++) if (RemoteControl.ACTIONS[n].equals(action)) select.setSelection(n);
            select.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
                public void onNothingSelected(AdapterView<?> p) { }
                public void onItemSelected(AdapterView<?> p, View v, int position, long id) {
                    RemoteControl.prefs(RemoteSettingsActivity.this).edit().putString(key + "." + gesture, RemoteControl.ACTIONS[position]).apply();
                }
            });
            bindings.addView(select);
        }
    }
}
