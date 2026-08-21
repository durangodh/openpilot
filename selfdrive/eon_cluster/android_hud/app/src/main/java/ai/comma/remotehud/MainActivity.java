package ai.comma.remotehud;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.View;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.CompoundButton;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.Switch;
import android.widget.TextView;

import java.util.Locale;

/**
 * v0.13 변경점
 * 1) USB 연결로 실행된 경우에는 안내 표시 여부·알림 권한과 무관하게
 *    서비스만 시작하고 즉시 finish() 한다. → TMAP 전면 보호가 항상 동작.
 * 2) 서비스 시작을 알림 권한에서 분리한다. 알림 권한은 알림이 보이느냐만
 *    좌우하며, 포그라운드 서비스 자체는 권한 없이도 동작한다.
 */
public final class MainActivity extends Activity {

    private static final int NOTIFICATION_PERMISSION_REQUEST = 72;

    private static final int GREEN = Color.rgb(0, 208, 132);
    private static final int RED = Color.rgb(255, 92, 92);
    private static final int AMBER = Color.rgb(255, 193, 74);

    private Switch autoSwitch;
    private TextView autoValue;
    private TextView eonValue;
    private Spinner displayProfileSpinner;
    private TextView fpsValue;
    private TextView jpegValue;
    private TextView mapValue;
    private Button overlayPermissionButton;
    private TextView permissionValue;
    private Button rescanUsbButton;
    private TextView serviceValue;
    private Button startButton;
    private Button stopButton;
    private TextView usbValue;
    private boolean openOverlayAfterNotification;

    private final Handler handler = new Handler(Looper.getMainLooper());

    private final Runnable refreshTask = new Runnable() {
        @Override
        public void run() {
            refreshStatus();
            handler.postDelayed(this, 500L);
        }
    };

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        boolean fromUsbAttach =
                "android.hardware.usb.action.USB_DEVICE_ATTACHED".equals(getIntent().getAction());

        // --- v0.13: TMAP 전면 보호 -------------------------------------------
        // USB 연결로 깨어난 경우에는 화면을 절대 띄우지 않는다.
        // 조건을 모두 제거해 어떤 상태에서도 전면을 빼앗지 않도록 했다.
        if (fromUsbAttach) {
            AppPrefs.markGuideShown(this);
            startHudService();
            finish();
            return;
        }
        // ---------------------------------------------------------------------

        setContentView(buildUi());

        autoSwitch.setChecked(AppPrefs.isAutoStart(this));
        autoSwitch.setOnCheckedChangeListener(new CompoundButton.OnCheckedChangeListener() {
            @Override
            public void onCheckedChanged(CompoundButton buttonView, boolean checked) {
                onAutoStartChanged(buttonView, checked);
            }
        });
        startButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                requestSetupPermissionsThenStart();
            }
        });
        stopButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                stopService(new Intent(MainActivity.this, HudService.class));
            }
        });
        rescanUsbButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                startForegroundService(new Intent(MainActivity.this, HudService.class)
                        .setAction(HudService.ACTION_RESCAN_USB));
            }
        });

        if (AppPrefs.isAutoStart(this)) {
            startHudService();
        }
        if (!AppPrefs.wasGuideShown(this)) {
            showFirstRunGuide();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (AppPrefs.isAutoStart(this)) {
            startHudService();
        }
        handler.post(refreshTask);
    }

    @Override
    protected void onPause() {
        handler.removeCallbacks(refreshTask);
        super.onPause();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode == NOTIFICATION_PERMISSION_REQUEST) {
            startHudService();
            if (openOverlayAfterNotification && !Settings.canDrawOverlays(this)) {
                openOverlayAfterNotification = false;
                openOverlayPermission();
            }
        }
    }

    private View buildUi() {
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(Color.rgb(7, 11, 16));

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(20), dp(22), dp(20), dp(28));
        scroll.addView(root, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.WRAP_CONTENT));

        root.addView(text("EON Remote HUD", 27.0f, Color.WHITE, Typeface.BOLD));

        View subtitle = text("v0.35  ·  8인치/9.2인치 화면 프로필 / 1CBE:0092",
                14.0f, Color.rgb(145, 158, 171), Typeface.NORMAL);
        LinearLayout.LayoutParams subtitleParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        subtitleParams.setMargins(0, dp(3), 0, dp(18));
        root.addView(subtitle, subtitleParams);

        LinearLayout controlCard = card();
        controlCard.addView(text("서비스 제어", 18.0f, Color.WHITE, Typeface.BOLD));

        LinearLayout buttonRow = new LinearLayout(this);
        buttonRow.setOrientation(LinearLayout.HORIZONTAL);
        buttonRow.setPadding(0, dp(12), 0, 0);
        startButton = button("서비스 시작", GREEN);
        stopButton = button("서비스 중지", Color.rgb(168, 54, 64));
        LinearLayout.LayoutParams startParams = new LinearLayout.LayoutParams(0, dp(50), 1.0f);
        startParams.setMargins(0, 0, dp(6), 0);
        buttonRow.addView(startButton, startParams);
        LinearLayout.LayoutParams stopParams = new LinearLayout.LayoutParams(0, dp(50), 1.0f);
        stopParams.setMargins(dp(6), 0, 0, 0);
        buttonRow.addView(stopButton, stopParams);
        controlCard.addView(buttonRow);

        rescanUsbButton = button("외부 HUD USB 다시 검색", Color.rgb(40, 92, 132));
        LinearLayout.LayoutParams rescanParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(48));
        rescanParams.setMargins(0, dp(10), 0, 0);
        controlCard.addView(rescanUsbButton, rescanParams);
        root.addView(controlCard, cardParams());

        LinearLayout statusCard = card();
        statusCard.addView(text("실시간 상태", 18.0f, Color.WHITE, Typeface.BOLD));
        serviceValue = addStatusRow(statusCard, "HUD 서비스");
        eonValue = addStatusRow(statusCard, "EON 데이터  UDP 7210");
        mapValue = addStatusRow(statusCard, "TMAP 영상  TCP 7211");
        usbValue = addStatusRow(statusCard, "외부 HUD USB");
        fpsValue = addStatusRow(statusCard, "전송 FPS");
        jpegValue = addStatusRow(statusCard, "JPEG 전송");
        root.addView(statusCard, cardParams());

        LinearLayout autoCard = card();
        autoCard.addView(text("자동실행", 18.0f, Color.WHITE, Typeface.BOLD));
        LinearLayout autoRow = new LinearLayout(this);
        autoRow.setOrientation(LinearLayout.HORIZONTAL);
        autoRow.setGravity(android.view.Gravity.CENTER_VERTICAL);
        autoRow.setPadding(0, dp(10), 0, 0);
        autoValue = text("부팅·USB 연결·앱 실행 시 시작",
                15.0f, Color.rgb(190, 200, 210), Typeface.NORMAL);
        autoRow.addView(autoValue, new LinearLayout.LayoutParams(
                0, LinearLayout.LayoutParams.WRAP_CONTENT, 1.0f));
        autoSwitch = new Switch(this);
        autoRow.addView(autoSwitch);
        autoCard.addView(autoRow);
        root.addView(autoCard, cardParams());

        LinearLayout displayCard = card();
        displayCard.addView(text("순정 내비 화면", 18.0f, Color.WHITE, Typeface.BOLD));
        TextView displayGuide = text(
                "자동 감지는 현재 화면 해상도를 사용합니다. nMirror 화면이 맞지 않으면 차량의 순정 모니터 크기를 직접 선택하세요.",
                14.0f, Color.rgb(190, 200, 210), Typeface.NORMAL);
        displayGuide.setLineSpacing(0.0f, 1.18f);
        LinearLayout.LayoutParams displayGuideParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        displayGuideParams.setMargins(0, dp(9), 0, dp(10));
        displayCard.addView(displayGuide, displayGuideParams);

        displayProfileSpinner = new Spinner(this);
        String[] profiles = {
                "자동 감지 (권장)",
                "제네시스 순정 8인치 · 800×480",
                "제네시스 순정 9.2인치 · 1280×720"
        };
        ArrayAdapter<String> profileAdapter = new ArrayAdapter<>(this,
                android.R.layout.simple_spinner_item, profiles);
        profileAdapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        displayProfileSpinner.setAdapter(profileAdapter);
        displayProfileSpinner.setSelection(AppPrefs.getDisplayProfile(this));
        displayProfileSpinner.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(AdapterView<?> parent, View view, int position, long id) {
                AppPrefs.setDisplayProfile(MainActivity.this, position);
            }

            @Override
            public void onNothingSelected(AdapterView<?> parent) {
            }
        });
        displayCard.addView(displayProfileSpinner, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(52)));
        root.addView(displayCard, cardParams());

        LinearLayout permissionCard = card();
        permissionCard.addView(text("최초 실행 권한 안내", 18.0f, Color.WHITE, Typeface.BOLD));
        permissionValue = text(
                "TMAP 위에 HUD 전환 버튼을 표시하려면 화면 오버레이 권한이 필요합니다. 외부 HUD를 연결하면 USB 사용 창에서 ‘항상 허용’을 선택하세요.",
                15.0f, Color.rgb(190, 200, 210), Typeface.NORMAL);
        permissionValue.setLineSpacing(0.0f, 1.18f);
        LinearLayout.LayoutParams permissionParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        permissionParams.setMargins(0, dp(10), 0, dp(10));
        permissionCard.addView(permissionValue, permissionParams);

        Button permissionButton = button("알림 권한 확인", Color.rgb(40, 92, 132));
        permissionButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                openNotificationPermission();
            }
        });
        permissionCard.addView(permissionButton, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(48)));

        overlayPermissionButton = button("HUD 전환 버튼 오버레이 권한", Color.rgb(40, 92, 132));
        overlayPermissionButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                openOverlayPermission();
            }
        });
        LinearLayout.LayoutParams overlayPermissionParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(48));
        overlayPermissionParams.setMargins(0, dp(10), 0, 0);
        permissionCard.addView(overlayPermissionButton, overlayPermissionParams);

        root.addView(permissionCard, cardParams());

        TextView footer = text(
                "화면 프로필은 S9/nMirror에만 적용됩니다. 외부 TURZX HUD는 원본 1920×462 출력을 유지합니다.",
                13.0f, Color.rgb(120, 135, 149), Typeface.NORMAL);
        footer.setGravity(android.view.Gravity.CENTER);
        root.addView(footer);

        return scroll;
    }

    private void refreshStatus() {
        HudService.StatusSnapshot s = HudService.getStatusSnapshot();

        setStatus(serviceValue, s.running ? "실행 중" : "중지됨", s.running ? GREEN : RED);
        setStatus(eonValue,
                s.eonConnected ? "연결됨 · " + s.eonAddress : "데이터 대기",
                s.eonConnected ? GREEN : AMBER);
        setStatus(mapValue, s.mapConnected ? "연결됨" : "영상 대기", s.mapConnected ? GREEN : AMBER);
        setStatus(usbValue, s.usbStatus,
                s.usbConnected ? GREEN : (s.usbError ? RED : AMBER));
        setStatus(fpsValue,
                String.format(Locale.US, "%.1f / 목표 8", s.fps),
                s.fps > 0.1f ? GREEN : Color.LTGRAY);
        setStatus(jpegValue,
                s.lastJpegBytes > 0
                        ? String.format(Locale.US, "전송 중 · %.1f KB", s.lastJpegBytes / 1024.0f)
                        : "전송 대기",
                s.lastJpegBytes > 0 ? GREEN : AMBER);

        autoValue.setText(AppPrefs.isAutoStart(this)
                ? "부팅·USB 연결·앱 실행 시 시작"
                : "수동 시작만 사용");

        startButton.setEnabled(!s.running);
        stopButton.setEnabled(s.running);
        rescanUsbButton.setEnabled(s.running);

        boolean notifyGranted = Build.VERSION.SDK_INT < 33
                || checkSelfPermission("android.permission.POST_NOTIFICATIONS") == 0;
        boolean overlayGranted = Settings.canDrawOverlays(this);
        String notificationStatus = notifyGranted
                ? "알림 권한: 허용됨"
                : "알림 권한: 미허용 (서비스는 동작하지만 알림이 보이지 않습니다)";
        permissionValue.setText(notificationStatus
                + "\nHUD 전환 버튼: " + (overlayGranted ? "허용됨" : "미허용")
                + "\n화면 전환: TMAP의 HUD 버튼 ↔ HUD의 TMAP 버튼"
                + "\nUSB 권한: 외부 HUD 사용 시 ‘항상 허용’을 선택하세요.");
        overlayPermissionButton.setText(overlayGranted
                ? "HUD 전환 버튼 오버레이 허용됨" : "HUD 전환 버튼 오버레이 권한");
    }

    private void onAutoStartChanged(CompoundButton buttonView, boolean checked) {
        AppPrefs.setAutoStart(this, checked);
        refreshStatus();
    }

    private void showFirstRunGuide() {
        new AlertDialog.Builder(this)
                .setTitle("최초 실행 안내")
                .setMessage("1. 알림 권한과 HUD 전환 버튼 오버레이 권한을 허용합니다.\n\n"
                        + "2. nMirror는 기존처럼 TMAP을 자동 실행합니다.\n\n"
                        + "3. TMAP 위 HUD 버튼을 누르면 HUD 전체화면, HUD 위 TMAP 버튼을 누르면 기존 TMAP으로 돌아갑니다.\n\n"
                        + "4. 외부 HUD도 함께 쓰는 경우 USB 창에서 ‘항상 허용’을 선택합니다.\n\n"
                        + "EON과 S9은 같은 네트워크에서 UDP 7210 / TCP 7211 통신이 가능해야 합니다.")
                .setPositiveButton("권한 확인", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        AppPrefs.markGuideShown(MainActivity.this);
                        requestSetupPermissionsThenStart();
                    }
                })
                .setNegativeButton("나중에", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        AppPrefs.markGuideShown(MainActivity.this);
                    }
                })
                .setCancelable(false)
                .show();
    }

    private void startHudService() {
        try {
            startForegroundService(new Intent(this, HudService.class));
        } catch (Exception ignored) {
        }
    }

    private void openNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission("android.permission.POST_NOTIFICATIONS") != 0) {
            requestPermissions(
                    new String[]{"android.permission.POST_NOTIFICATIONS"},
                    NOTIFICATION_PERMISSION_REQUEST);
        } else {
            startActivity(new Intent("android.settings.APP_NOTIFICATION_SETTINGS")
                    .putExtra("android.provider.extra.APP_PACKAGE", getPackageName()));
        }
    }

    private void requestSetupPermissionsThenStart() {
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission("android.permission.POST_NOTIFICATIONS") != 0) {
            openOverlayAfterNotification = true;
            requestPermissions(
                    new String[]{"android.permission.POST_NOTIFICATIONS"},
                    NOTIFICATION_PERMISSION_REQUEST);
            startHudService();
        } else {
            startHudService();
            if (!Settings.canDrawOverlays(this)) {
                openOverlayPermission();
            }
        }
    }

    private void openOverlayPermission() {
        try {
            Intent intent = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:" + getPackageName()));
            startActivity(intent);
        } catch (Exception ignored) {
            startActivity(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION));
        }
    }

    private LinearLayout card() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(17), dp(16), dp(17), dp(16));
        card.setBackgroundColor(Color.rgb(18, 25, 33));
        return card;
    }

    private LinearLayout.LayoutParams cardParams() {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, 0, 0, dp(12));
        return params;
    }

    private TextView addStatusRow(LinearLayout parent, String label) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(android.view.Gravity.CENTER_VERTICAL);
        row.setPadding(0, dp(13), 0, 0);
        row.addView(text(label, 14.0f, Color.rgb(148, 162, 175), Typeface.NORMAL),
                new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1.0f));
        TextView value = text("대기", 15.0f, Color.LTGRAY, Typeface.BOLD);
        value.setGravity(android.view.Gravity.END);
        row.addView(value,
                new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1.0f));
        parent.addView(row);
        return value;
    }

    private TextView text(String content, float size, int color, int style) {
        TextView view = new TextView(this);
        view.setText(content);
        view.setTextSize(size);
        view.setTextColor(color);
        view.setTypeface(Typeface.create("sans", style));
        return view;
    }

    private Button button(String label, int color) {
        Button view = new Button(this);
        view.setText(label);
        view.setTextColor(Color.WHITE);
        view.setTextSize(15.0f);
        view.setAllCaps(false);
        view.setBackgroundColor(color);
        return view;
    }

    private static void setStatus(TextView view, String value, int color) {
        view.setText(value);
        view.setTextColor(color);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
