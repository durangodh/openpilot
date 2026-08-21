from pathlib import Path


ROOT = Path(__file__).parents[3]
UI_DIR = ROOT / "selfdrive" / "ui" / "qt"


def test_s9_connection_does_not_hide_eon_driving_ui():
  onroad = (UI_DIR / "onroad.cc").read_text(encoding="utf-8")
  header = (UI_DIR / "onroad.h").read_text(encoding="utf-8")
  assert 'getBool("EonClusterHudConnected")' not in onroad
  assert "eon_cluster_hud_connected" not in onroad
  assert "eon_cluster_hud_connected" not in header
  assert "drawLaneLines(p, s);" in onroad
  assert "drawCarrotPlot(p);" in onroad
  assert "drawCarrotLead(p);" in onroad
  assert "updateCarrotNavi(true);" in onroad
  assert "drawCarrotNavi(p);" in onroad


def test_eon_driving_path_matches_valid_mpc_with_model_fallback():
  ui = (ROOT / "selfdrive" / "ui" / "ui.cc").read_text(encoding="utf-8")
  assert "getMpcSolutionValid()" in ui
  assert "getMpcPathX()" in ui
  assert "getMpcPathY()" in ui
  assert "const float z = i < static_cast<int>(line_z.size()) ? line_z[i] : 0.0f;" in ui
  assert 's->sm->alive("lateralPlan") && s->sm->valid("lateralPlan")' in ui
  assert "!update_mpc_path_data(s, lateral_plan, model_position" in ui
  assert "update_line_data(s, model_position, path_width" in ui


def test_image_loading_is_gated_separately_from_json_state():
  navi = (UI_DIR / "onroad_navi.inc").read_text(encoding="utf-8")
  image_guard = navi.index("if (load_images)")
  json_update = navi.index('root.value("updated_at_ms")')
  assert image_guard < json_update
  assert "void NvgWindow::drawCarrotNavi" in navi
  draw_body = navi.split("void NvgWindow::drawCarrotNavi", 1)[1]
  assert "updateCarrotNavi();" not in draw_body


def test_s9_hud_params_are_exposed_in_settings():
  settings = (UI_DIR / "offroad" / "settings.cc").read_text(encoding="utf-8")
  assert '"EonClusterHud", "S9 HUD 사용"' in settings
  assert '"EonClusterHudOutputTarget", "HUD 출력 대상"' in settings
  assert '"1: 외부 HUD / 2: S9 화면 / 3: 동시 출력"' in settings
  assert '"EonClusterHudOutputMode", "S9 HUD 표시 내용"' in settings
  assert '"EonClusterHudFps", "S9 HUD 프레임"' in settings
  assert '"../assets/offroad/icon_road.png", 0, 15, 1, 0, 10' in settings
  assert '"EonClusterHudMapFps", "S9 HUD 지도 프레임"' in settings
  assert '"EonClusterHudBrightness", "S9 HUD 밝기"' in settings
  assert '"EonClusterHudJpegQuality", "S9 HUD 화질"' in settings
  assert '"../assets/offroad/icon_road.png", 20, 95, 1, 0, 58' in settings
  assert '"EonClusterHudScreenMode", "S9 HUD 우측 화면"' in settings
  assert '"EonClusterHudOrientation", "S9 HUD 화면 회전"' in settings
  assert '"EonClusterHudMirror", "S9 HUD 좌우 반전"' in settings
  assert '"EonClusterHudLanguage", "S9 HUD 언어"' in settings
  assert '"EonClusterHudRadarInfo", "S9 HUD 레이더 정보"' in settings


def test_manager_starts_only_s9_hud_publisher():
  processes = (ROOT / "selfdrive" / "manager" / "process_config.py").read_text(encoding="utf-8")
  remote = (ROOT / "selfdrive" / "eon_cluster" / "remote_hud.py").read_text(encoding="utf-8")
  params = (ROOT / "selfdrive" / "common" / "params.cc").read_text(encoding="utf-8")

  assert 'PythonProcess("remote_hud", "selfdrive.eon_cluster.remote_hud_s9", enabled=EON' in processes
  assert 'PythonProcess("eon_cluster"' not in processes
  assert "EonClusterHudOutputMode" not in remote
  assert "_migrate_legacy_remote_mode" not in remote
  assert 'params.put_bool(PARAM_ENABLED, True)' not in remote
  assert '{"EonClusterHudOutputMode", PERSISTENT}' in params
  assert '{"EonClusterHudOutputTarget", PERSISTENT}' in params


def test_s9_output_target_reaches_android_renderer():
  manager = (ROOT / "selfdrive" / "manager" / "manager.py").read_text(encoding="utf-8")
  remote = (ROOT / "selfdrive" / "eon_cluster" / "remote_hud_s9.py").read_text(encoding="utf-8")
  service = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
             "main" / "java" / "ai" / "comma" / "remotehud" / "HudService.java").read_text(encoding="utf-8")

  assert '("EonClusterHudOutputTarget", "3")' in manager
  assert 'packet["hudOutputTarget"] = _bounded_int("EonClusterHudOutputTarget", 3, 1, 3)' in remote
  assert 'currentState.optInt("hudOutputTarget", 3)' in service
  assert 'return configuredOutputTarget == 1 || configuredOutputTarget == 3;' in service
  assert 'return configuredOutputTarget == 2 || configuredOutputTarget == 3;' in service


def test_s9_launcher_uses_fullscreen_activity_without_overlay_permission():
  android = ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" / "main"
  manifest = (android / "AndroidManifest.xml").read_text(encoding="utf-8")
  fullscreen = (android / "java" / "ai" / "comma" / "remotehud" /
                "HudFullscreenActivity.java").read_text(encoding="utf-8")
  service = (android / "java" / "ai" / "comma" / "remotehud" /
             "HudService.java").read_text(encoding="utf-8")

  assert 'android:name=".HudFullscreenActivity"' in manifest
  assert 'android:label="EON HUD"' in manifest
  assert 'android:screenOrientation="landscape"' in manifest
  assert "SYSTEM_ALERT_WINDOW" not in manifest
  assert "SYSTEM_UI_FLAG_IMMERSIVE_STICKY" in fullscreen
  assert "WindowInsets.Type.statusBars()" in fullscreen
  on_create = fullscreen.split("public void onCreate", 1)[1].split("protected void onResume", 1)[0]
  assert on_create.index("setContentView(frameView);") < on_create.index("hideSystemUi();")
  assert "decorView.getWindowInsetsController()" in fullscreen
  assert "getWindow().getInsetsController()" not in fullscreen
  assert "HudService.drawFullscreenFrame" in fullscreen
  assert "static boolean drawFullscreenFrame" in service
  assert not (android / "java" / "ai" / "comma" / "remotehud" /
              "PhoneHudOverlay.java").exists()


def test_uiview_starts_only_s9_hud_publisher():
  debug_dir = ROOT / "selfdrive" / "debug"
  for script_name in ("uiview.py", "uiview_carrot.py"):
    script = (debug_dir / script_name).read_text(encoding="utf-8")
    process_line = next(line for line in script.splitlines() if line.strip().startswith("procs ="))
    assert "'remote_hud'" in process_line
    assert "'eon_cluster'" not in process_line


def test_map_activity_requires_valid_jpeg_and_stale_clear_survives_disconnect():
  server = (ROOT / "selfdrive" / "carrot_navi_server.py").read_text(encoding="utf-8")
  valid_marker = 'image = image_payload[start:end + 2]'
  activity_marker = "self.last_map_rx = now"
  first_activity = server.index(activity_marker, server.index(valid_marker))
  assert first_activity > server.index(valid_marker)
  watchdog = server.split("def _map_watchdog_loop", 1)[1].split("def local_ip", 1)[0]
  assert "if not control_present:\n        continue" not in watchdog
  assert "if age >= MAP_STALE_S and control_present:" in watchdog
  assert "if age >= MAP_STALE_CLEAR_S and not already_cleared:" in watchdog


def test_remote_ack_is_status_only():
  remote = (ROOT / "selfdrive" / "eon_cluster" / "remote_hud.py").read_text(encoding="utf-8")
  onroad = (UI_DIR / "onroad.cc").read_text(encoding="utf-8")
  assert 'PARAM_CONNECTED = "EonClusterHudConnected"' in remote
  assert "params.put_bool(PARAM_CONNECTED, value)" in remote
  assert "_publish_connected(params, published, connected)" in remote
  assert "EonClusterHudConnected" not in onroad
