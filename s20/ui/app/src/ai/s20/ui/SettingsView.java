package ai.s20.ui;

import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.provider.Settings;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.function.Supplier;

/**
 * Settings like the comma 3X (selfdrive/ui/layouts/settings): panels on the left, rows on the right.
 * openpilot params change through s20uid (commands in /data/s20pilot/share/cmd, see s20/ui/settings_bridge.py);
 * phone actions (reboot, openpilot restart, run mode) use root (Magisk asks once).
 */
final class SettingsView extends FrameLayout {
  static final File CMD_DIR = new File("/data/s20pilot/share/cmd");
  static final String AUTOSTART = "sh /data/s20pilot/rootfs/root/openpilot/s20/android/autostart.sh";
  static final int GREEN = Color.rgb(51, 171, 76), BTN = Color.rgb(57, 57, 57), LINE = Color.rgb(51, 51, 51),
      GREY = Color.rgb(170, 170, 170), RED = Color.rgb(201, 34, 49);
  private static final String[] PANELS = {"장치", "토글", "소프트웨어", "개발자", "S20"};

  private final Supplier<JSONObject> ui;
  private final Runnable onClose;
  private final LinearLayout panelList, content;
  private final TextView banner;
  private final List<Runnable> refreshers = new ArrayList<>();
  private final Map<String, Long> pending = new HashMap<>();  // key -> time of our last change (ignore stale values)
  private int panel = 0;
  private String lastCmdId = "";
  private JSONObject vals = new JSONObject(), st = new JSONObject();
  private boolean online;

  SettingsView(Context c, Supplier<JSONObject> ui, Runnable onClose) {
    super(c);
    this.ui = ui; this.onClose = onClose;
    setBackgroundColor(Color.BLACK);
    setClickable(true);  // keep touches away from the view below
    LinearLayout row = new LinearLayout(c);
    row.setOrientation(LinearLayout.HORIZONTAL);
    addView(row, new LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT));

    panelList = new LinearLayout(c);
    panelList.setOrientation(LinearLayout.VERTICAL);
    panelList.setPadding(dp(24), dp(24), dp(12), dp(24));
    row.addView(panelList, new LinearLayout.LayoutParams(dp(210), LayoutParams.MATCH_PARENT));

    LinearLayout right = new LinearLayout(c);
    right.setOrientation(LinearLayout.VERTICAL);
    row.addView(right, new LinearLayout.LayoutParams(0, LayoutParams.MATCH_PARENT, 1));
    banner = text(c, "", 15, Color.WHITE, false);
    banner.setPadding(dp(20), dp(10), dp(20), dp(10));
    banner.setBackground(round(RED, 12));
    banner.setVisibility(GONE);
    LinearLayout.LayoutParams blp = new LinearLayout.LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT);
    blp.setMargins(dp(12), dp(16), dp(24), 0);
    right.addView(banner, blp);
    ScrollView scroll = new ScrollView(c);
    content = new LinearLayout(c);
    content.setOrientation(LinearLayout.VERTICAL);
    content.setPadding(dp(12), dp(8), dp(36), dp(40));
    scroll.addView(content);
    right.addView(scroll, new LinearLayout.LayoutParams(LayoutParams.MATCH_PARENT, 0, 1));
    buildPanels();
    tick();
  }

  // ---------- refresh from ui.json ----------

  private void tick() {
    if (getVisibility() == VISIBLE) refresh();
    postDelayed(this::tick, 500);
  }

  void open() {
    setVisibility(VISIBLE);
    refresh();
    show(panel);
  }

  private void refresh() {
    JSONObject u = ui.get();
    online = u != null && System.currentTimeMillis() / 1000.0 - u.optDouble("t") < 3.0 && u.has("settings");
    st = online ? u.optJSONObject("settings") : new JSONObject();
    if (st == null) st = new JSONObject();
    JSONObject v = st.optJSONObject("values");
    if (v != null) vals = v;
    banner.setVisibility(online ? GONE : VISIBLE);
    banner.setText("openpilot 이 실행 중이 아니라 openpilot 설정은 바꿀 수 없습니다 (S20 메뉴는 사용 가능)");
    JSONObject last = st.optJSONObject("last");
    if (last != null && !lastCmdId.isEmpty() && lastCmdId.equals(last.optString("id"))) {
      String msg = last.optString("msg");
      if (!msg.isEmpty() || !last.optBoolean("ok")) toast(msg.isEmpty() ? "실패" : msg);
      if (!last.optBoolean("ok")) pending.clear();
      lastCmdId = "";
    }
    for (Runnable r : refreshers) r.run();
  }

  private boolean fresh(String key) {  // our own change is newer than the value in ui.json
    Long t = pending.get(key);
    return t != null && System.currentTimeMillis() - t < 2500;
  }

  // ---------- commands to s20uid ----------

  private void send(String op, String key, Object value) {
    try {
      String id = Long.toString(System.nanoTime());
      JSONObject j = new JSONObject().put("id", id).put("op", op);
      if (key != null) { j.put("key", key); pending.put(key, System.currentTimeMillis()); vals.put(key, value); }
      if (value != null) j.put("value", value);
      if (!CMD_DIR.isDirectory()) CMD_DIR.mkdirs();
      File tmp = new File(CMD_DIR, id + ".tmp"), out = new File(CMD_DIR, id + ".json");
      try (FileOutputStream f = new FileOutputStream(tmp)) { f.write(j.toString().getBytes(StandardCharsets.UTF_8)); }
      tmp.setReadable(true, false); tmp.setWritable(true, false);
      if (!tmp.renameTo(out)) throw new Exception("rename failed");
      lastCmdId = id;
    } catch (Exception e) {
      toast("설정 전달 실패: " + e.getMessage());
    }
  }

  private void root(String what, String cmd) {
    toast(what + "...");
    new Thread(() -> {
      String res;
      try {
        Process p = Runtime.getRuntime().exec(new String[]{"su", "-c", cmd});
        res = p.waitFor() == 0 ? what + " 완료" : what + " 실패 (Magisk 에서 앱의 루트 권한을 허용하세요)";
      } catch (Exception e) { res = what + " 실패: " + e.getMessage(); }
      final String r = res;
      post(() -> toast(r));
    }, "s20ui-root").start();
  }

  private void confirm(String title, String msg, String ok, Runnable action) {
    new AlertDialog.Builder(getContext(), AlertDialog.THEME_DEVICE_DEFAULT_DARK)
        .setTitle(title).setMessage(msg)
        .setPositiveButton(ok, (d, w) -> action.run())
        .setNegativeButton("취소", null).show();
  }

  // ---------- panels ----------

  private void buildPanels() {
    Context c = getContext();
    Button close = new Button(c);
    close.setText("✕");
    close.setTextSize(TypedValue.COMPLEX_UNIT_SP, 26);
    close.setTextColor(Color.WHITE);
    close.setBackground(round(BTN, 60));
    close.setOnClickListener(v -> { setVisibility(GONE); onClose.run(); });
    LinearLayout.LayoutParams clp = new LinearLayout.LayoutParams(dp(76), dp(76));
    clp.bottomMargin = dp(24);
    panelList.addView(close, clp);
    for (int i = 0; i < PANELS.length; i++) {
      final int idx = i;
      TextView t = text(c, PANELS[i], 24, GREY, true);
      t.setPadding(dp(8), dp(14), dp(8), dp(14));
      t.setOnClickListener(v -> show(idx));
      panelList.addView(t);
    }
  }

  private void show(int idx) {
    panel = idx;
    for (int i = 0; i < PANELS.length; i++)
      ((TextView) panelList.getChildAt(i + 1)).setTextColor(i == idx ? Color.WHITE : GREY);
    content.removeAllViews();
    refreshers.clear();
    switch (idx) {
      case 0: device(); break;
      case 1: toggles(); break;
      case 2: software(); break;
      case 3: developer(); break;
      default: s20(); break;
    }
    for (Runnable r : refreshers) r.run();
  }

  private void device() {
    textRow("Dongle ID", () -> or(vals.optString("DongleId"), "없음 (comma connect 미등록)"));
    textRow("시리얼", () -> or(vals.optString("HardwareSerial"), "–"));
    textRow("차량", () -> or(st.optString("car"), "인식 안 됨"));
    buttonRow("캘리브레이션 초기화", this::calibDesc, "초기화", true,
        () -> confirm("캘리브레이션 초기화", "캘리브레이션을 초기화할까요?", "초기화", () -> send("reset_calib", null, null)));
    LinearLayout r = row("재부팅 / 전원 끄기", "폰 전체를 다시 켜거나 끕니다.");
    Button rb = button("재부팅", BTN), off = button("전원 끄기", RED);
    rb.setOnClickListener(v -> confirm("재부팅", "폰을 재부팅할까요?", "재부팅", () -> root("재부팅", "reboot")));
    off.setOnClickListener(v -> confirm("전원 끄기", "폰을 끌까요?", "전원 끄기", () -> root("전원 끄기", "reboot -p")));
    actions(r, rb, off);
  }

  private String calibDesc() {
    StringBuilder d = new StringBuilder("openpilot 은 계속 캘리브레이션하므로 초기화는 거의 필요 없습니다.");
    JSONArray rpy = st.optJSONArray("calib_rpy");
    if (rpy != null && rpy.length() == 3) {
      double pitch = rpy.optDouble(1), yaw = rpy.optDouble(2);
      d.append(String.format(Locale.US, " 지금 장치는 %.1f° %s, %.1f° %s 향해 있습니다.", Math.abs(pitch), pitch > 0 ? "아래" : "위",
          Math.abs(yaw), yaw > 0 ? "왼쪽" : "오른쪽"));
    }
    int perc = st.optInt("calib_perc");
    if (perc > 0 && perc < 100) d.append(" 진행 ").append(perc).append("%.");
    return d.toString();
  }

  private void toggles() {
    toggleRow("OpenpilotEnabledToggle", "openpilot 사용",
        "openpilot 으로 차간거리 크루즈와 차로 유지 보조를 사용합니다. 운전자는 항상 주의를 기울여야 합니다. "
            + "바꾸면 시동이 켜져 있을 때 openpilot 이 다시 시작됩니다.", true);
    toggleRow("DisengageOnAccelerator", "가속 페달로 해제", "켜면 가속 페달을 밟을 때 openpilot 이 해제됩니다.", false);
    choiceRow("LongitudinalPersonality", "주행 성향",
        "표준을 추천합니다. 공격적은 앞차를 더 가깝게 따라가고 가감속이 강합니다. 편안함은 앞차와 더 멀리 떨어집니다. "
            + "지원 차량은 핸들의 차간거리 버튼으로도 바꿀 수 있습니다.", new String[]{"공격적", "표준", "편안함"});
    speedRow();
    toggleRow("IsLdwEnabled", "차선 이탈 경고", "50 km/h 이상에서 깜빡이 없이 차선을 넘으면 경고합니다.", false);
    toggleRow("IsMetric", "미터법 사용", "속도를 mph 대신 km/h 로 표시합니다.", false);
    note("운전자 모니터링(실내 카메라)과 마이크 녹음은 S20 에서 쓰지 않아 뺐습니다.");
  }

  private void software() {
    textRow("버전", () -> or(vals.optString("Version"), "–"));
    textRow("브랜치", () -> or(vals.optString("GitBranch"), "–"));
    textRow("커밋", () -> {
      String cm = vals.optString("GitCommit");
      return cm.isEmpty() ? "–" : cm.substring(0, Math.min(10, cm.length())) + "  " + vals.optString("GitCommitDate");
    });
    note("업데이트: 우분투 창에서 cd /root/openpilot && git pull 후 S20 메뉴의 'openpilot 다시 시작'.");
  }

  private void developer() {
    toggleRow("ShowDebugInfo", "UI 디버그 모드", "주행 화면 아래에 상태 줄(카메라 fps, 캘리브레이션, CPU 온도)을 표시합니다.", false);
    if (vals.optBoolean("IsReleaseBranch")) { note("릴리즈 브랜치에서는 개발자 토글이 숨겨집니다."); return; }
    toggleRow("JoystickDebugMode", "조이스틱 디버그 모드", "조이스틱으로 차를 직접 조작하는 테스트 모드입니다.", false);
    toggleRow("LongitudinalManeuverMode", "종방향 기동 모드", "가감속 기동 테스트 모드입니다 (openpilot 종방향 제어 차량).", false);
    toggleRow("LateralManeuverMode", "횡방향 기동 모드", "조향 기동 테스트 모드입니다.", false);
    View alpha = toggleRow("AlphaLongitudinalEnabled", "openpilot 종방향 제어 (알파)",
        "경고: 이 차량의 openpilot 종방향 제어는 알파 단계이며 자동 긴급 제동(AEB)이 꺼집니다. "
            + "바꾸면 시동이 켜져 있을 때 openpilot 이 다시 시작됩니다.", true);
    refreshers.add(() -> alpha.setVisibility(st.optBoolean("alpha_avail") || vals.optBoolean("AlphaLongitudinalEnabled") ? VISIBLE : GONE));
  }

  private void s20() {
    LinearLayout m = row("실행 모드", "manager: 보통 (판다와 차가 있으면 주행). demo: 판다 없이 모델만 돌려 주행 화면 확인 (GPU 사용, 폰이 뜨거워짐).");
    TextView cur = text(getContext(), "", 20, Color.WHITE, true);
    Button bm = button("manager", BTN), bd = button("demo", BTN);
    bm.setOnClickListener(v -> root("manager 모드", AUTOSTART + " mode manager"));
    bd.setOnClickListener(v -> confirm("demo 모드", "판다 없이 모델만 돌립니다. BEAM 튜닝 중이면 하지 마세요.", "demo", () -> root("demo 모드", AUTOSTART + " mode demo")));
    actions(m, cur, bm, bd);
    refreshers.add(() -> cur.setText(readMode() + "   "));
    LinearLayout r = row("openpilot 다시 시작", "카메라 브리지와 openpilot 을 다시 시작합니다 (git pull 후 등).");
    Button rs = button("다시 시작", BTN);
    rs.setOnClickListener(v -> confirm("openpilot 다시 시작", "다시 시작할까요?", "다시 시작", () -> root("openpilot 다시 시작", AUTOSTART + " restart")));
    actions(r, rs);
    LinearLayout a = row("안드로이드 설정", "와이파이, 블루투스, 화면 밝기 등. 홈 앱을 원래 런처로 바꾸려면 '홈 앱'.");
    Button as = button("설정", BTN), home = button("홈 앱", BTN);
    as.setOnClickListener(v -> getContext().startActivity(new Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)));
    home.setOnClickListener(v -> getContext().startActivity(new Intent(Settings.ACTION_HOME_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)));
    actions(a, as, home);
    note("로그: /data/s20pilot/share/log  ·  PC 에서 상태: autostart.sh status");
  }

  private static String readMode() {
    try { return new String(Files.readAllBytes(new File("/data/s20pilot/autostart.mode").toPath()), StandardCharsets.UTF_8).trim(); }
    catch (Exception e) { return "manager"; }
  }

  // ---------- rows ----------

  private LinearLayout row(String title, String desc) {
    Context c = getContext();
    LinearLayout box = new LinearLayout(c);
    box.setOrientation(LinearLayout.VERTICAL);
    box.setPadding(0, dp(22), 0, dp(22));
    LinearLayout top = new LinearLayout(c);
    top.setOrientation(LinearLayout.HORIZONTAL);
    top.setGravity(Gravity.CENTER_VERTICAL);
    TextView t = text(c, title, 22, Color.WHITE, true);
    top.addView(t, new LinearLayout.LayoutParams(0, LayoutParams.WRAP_CONTENT, 1));
    box.addView(top);
    if (desc != null && !desc.isEmpty()) {
      TextView d = text(c, desc, 15, GREY, false);
      d.setPadding(0, dp(8), dp(24), 0);
      box.addView(d);
      box.setTag(d);
    }
    content.addView(box);
    View line = new View(c);
    line.setBackgroundColor(LINE);
    content.addView(line, new LinearLayout.LayoutParams(LayoutParams.MATCH_PARENT, dp(1)));
    return box;
  }

  private void actions(LinearLayout row, View... vs) {
    LinearLayout top = (LinearLayout) row.getChildAt(0);
    for (View v : vs) {
      LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(LayoutParams.WRAP_CONTENT, dp(64));
      lp.leftMargin = dp(10);
      top.addView(v, lp);
    }
  }

  private void textRow(String title, Supplier<String> value) {
    LinearLayout r = row(title, null);
    TextView v = text(getContext(), "", 20, GREY, false);
    v.setMaxWidth(dp(520)); v.setSingleLine(true); v.setEllipsize(TextUtils.TruncateAt.END);
    ((LinearLayout) r.getChildAt(0)).addView(v);
    refreshers.add(() -> v.setText(value.get()));
  }

  private void buttonRow(String title, Supplier<String> desc, String label, boolean needsOnline, Runnable action) {
    LinearLayout r = row(title, " ");
    Button b = button(label, BTN);
    b.setOnClickListener(v -> action.run());
    actions(r, b);
    TextView d = (TextView) r.getTag();
    refreshers.add(() -> { d.setText(desc.get()); b.setEnabled(!needsOnline || online); b.setAlpha(b.isEnabled() ? 1f : 0.4f); });
  }

  private View toggleRow(String key, String title, String desc, boolean restart) {
    LinearLayout r = row(title, desc);
    Switch sw = new Switch(getContext());
    sw.setScaleX(1.5f); sw.setScaleY(1.5f);
    sw.setThumbTintList(ColorStateList.valueOf(Color.WHITE));
    sw.setTrackTintList(new ColorStateList(new int[][]{{android.R.attr.state_checked}, {}}, new int[]{GREEN, Color.rgb(90, 90, 90)}));
    final boolean[] updating = {false};
    sw.setOnCheckedChangeListener((v, on) -> { if (!updating[0]) send("put_bool", key, on); });
    actions(r, sw);
    refreshers.add(() -> {
      boolean locked = restart && st.optBoolean("engaged");
      sw.setEnabled(online && !locked); sw.setAlpha(sw.isEnabled() ? 1f : 0.4f);
      if (!fresh(key)) { updating[0] = true; sw.setChecked(vals.optBoolean(key)); updating[0] = false; }
    });
    return r;
  }

  private void choiceRow(String key, String title, String desc, String[] labels) {
    LinearLayout r = row(title, desc);
    Button[] bs = new Button[labels.length];
    for (int i = 0; i < labels.length; i++) {
      final int idx = i;
      bs[i] = button(labels[i], BTN);
      bs[i].setOnClickListener(v -> { send("put_int", key, idx); paint(bs, idx); });
    }
    actions(r, bs);
    refreshers.add(() -> {
      // like the comma UI: only with openpilot longitudinal control once the car is known
      boolean en = online && (st.optString("car").isEmpty() || st.optBoolean("has_long"));
      for (Button b : bs) { b.setEnabled(en); b.setAlpha(en ? 1f : 0.4f); }
      if (!fresh(key)) paint(bs, vals.optInt(key, 1));
    });
  }

  private void paint(Button[] bs, int sel) {
    for (int i = 0; i < bs.length; i++) bs[i].setBackground(round(i == sel ? GREEN : BTN, 16));
  }

  /** Initial Max Speed: 5 km/h (or mph) steps from 40 to 145 km/h, stored in km/h like the comma UI */
  private void speedRow() {
    LinearLayout r = row("초기 최대 속도", "openpilot 을 켤 때의 최대 속도. 이미 더 빨리 달리고 있으면 현재 속도를 씁니다. "
        + "다음 인게이지부터 적용됩니다 (openpilot 종방향 제어 차량).");
    TextView v = text(getContext(), "", 22, Color.WHITE, true);
    v.setGravity(Gravity.CENTER); v.setMinWidth(dp(150));
    Button minus = button("–", BTN), plus = button("+", BTN);
    minus.setOnClickListener(x -> step(-1, v));
    plus.setOnClickListener(x -> step(1, v));
    actions(r, minus, v, plus);
    refreshers.add(() -> {
      boolean en = online;
      minus.setEnabled(en); plus.setEnabled(en);
      v.setText(speedText(vals.optInt("InitialCruiseSpeedKph", 40)));
    });
  }

  private double conv() { return vals.optBoolean("IsMetric") ? 1.0 : 0.621371; }

  private String speedText(int kph) {
    return Math.round(kph * conv()) + (vals.optBoolean("IsMetric") ? " km/h" : " mph");
  }

  private void step(int dir, TextView v) {
    double cv = conv();
    int lo = (int) Math.round(40 * cv), hi = (int) Math.round(145 * cv);
    int cur = (int) Math.round(vals.optInt("InitialCruiseSpeedKph", 40) * cv);
    int idx = Math.round((cur - lo) / 5f) + dir;
    int shown = Math.max(lo, Math.min(hi, lo + idx * 5));
    int kph = (int) Math.max(40, Math.min(145, Math.round(shown / cv)));
    send("put_int", "InitialCruiseSpeedKph", kph);
    v.setText(speedText(kph));
  }

  private void note(String s) {
    TextView t = text(getContext(), s, 15, GREY, false);
    t.setPadding(0, dp(20), 0, 0);
    content.addView(t);
  }

  // ---------- helpers ----------

  private Button button(String label, int color) {
    Button b = new Button(getContext());
    b.setText(label);
    b.setAllCaps(false);
    b.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18);
    b.setTextColor(Color.WHITE);
    b.setPadding(dp(26), 0, dp(26), 0);
    b.setBackground(round(color, 16));
    return b;
  }

  private TextView text(Context c, String s, int sp, int color, boolean bold) {
    TextView t = new TextView(c);
    t.setText(s);
    t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
    t.setTextColor(color);
    if (bold) t.setTypeface(Typeface.DEFAULT_BOLD);
    return t;
  }

  private static GradientDrawable round(int color, int radiusDp) {
    GradientDrawable g = new GradientDrawable();
    g.setColor(color);
    g.setCornerRadius(radiusDp * 3f);
    return g;
  }

  private int dp(int v) { return Math.round(v * getResources().getDisplayMetrics().density); }

  private static String or(String s, String d) { return s == null || s.isEmpty() ? d : s; }

  private void toast(String s) { Toast.makeText(getContext(), s, Toast.LENGTH_SHORT).show(); }
}
