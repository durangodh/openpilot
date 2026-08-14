from pathlib import Path


UI_DIR = Path(__file__).parents[2] / "ui" / "qt"


def test_external_hud_keeps_json_updates_but_skips_tmap_draw():
  onroad = (UI_DIR / "onroad.cc").read_text(encoding="utf-8")
  assert 'eon_hud_params.getBool("EonClusterHudConnected")' in onroad
  assert "CameraViewWidget::paintGL();" in onroad
  assert "p.fillRect(rect(), Qt::black);" not in onroad
  suppressed_scene = onroad.split("if (!eon_cluster_hud_connected) {", 1)[1].split("}", 1)[0]
  assert "drawLaneLines(p, s);" in suppressed_scene
  assert "drawCarrotPlot(p);" in suppressed_scene
  assert "drawCarrotLead(p);" in suppressed_scene
  assert "updateCarrotNavi(!eon_cluster_hud_connected);" in onroad
  assert "if (!eon_cluster_hud_connected) drawCarrotNavi(p);" in onroad


def test_image_loading_is_gated_separately_from_json_state():
  navi = (UI_DIR / "onroad_navi.inc").read_text(encoding="utf-8")
  image_guard = navi.index("if (load_images)")
  json_update = navi.index('root.value("updated_at_ms")')
  assert image_guard < json_update
  assert "void NvgWindow::drawCarrotNavi" in navi
  draw_body = navi.split("void NvgWindow::drawCarrotNavi", 1)[1]
  assert "updateCarrotNavi();" not in draw_body


def test_external_hud_params_are_exposed_in_settings():
  settings = (UI_DIR / "offroad" / "settings.cc").read_text(encoding="utf-8")
  assert '"EonClusterHud", "외부 클러스터 HUD 사용"' in settings
  assert '"EonClusterHudFps", "클러스터 HUD 프레임"' in settings
  assert '"../assets/offroad/icon_road.png", 0, 15, 1, 0, 10' in settings
  assert '"EonClusterHudBrightness", "클러스터 HUD 밝기"' in settings
  assert '"../assets/offroad/icon_road.png", 0, 100, 5, 0, 65' in settings
  assert '"EonClusterHudJpegQuality", "클러스터 HUD 화질"' in settings
  assert '"../assets/offroad/icon_road.png", 1, 95, 1, 0, 58' in settings
  assert 'EonClusterHudPanelLayout' not in settings
  assert '"EonClusterHudScreenMode", "클러스터 HUD 우측 화면"' in settings
  assert '1: 자동(길안내/주행리포트) / 2: 실시간 디버그 / 3: 주행리포트 고정' in settings
  assert '"../assets/offroad/icon_road.png", 1, 3, 1, 0, 1' in settings
  assert '"EonClusterHudOrientation", "클러스터 HUD 화면 회전"' in settings
  assert '"EonClusterHudMirror", "클러스터 HUD 좌우 반전"' in settings
  assert '"EonClusterHudLanguage", "클러스터 HUD 언어"' in settings
  assert '"EonClusterHudRadarInfo", "클러스터 레이더 정보"' in settings
  assert '"EonClusterHudRadarDisplay", "클러스터 레이더 표시"' in settings


def test_hud_frame_rate_setting_starts_at_zero():
  from pathlib import Path as _Path
  cluster = (_Path(__file__).parents[1] / "eon_cluster.py").read_text(encoding="utf-8")
  assert "MIN_FPS = 0" in cluster
  # Both the connect path and the live-settings poll must share the range.
  assert cluster.count("_param_int(params, PARAM_FPS, 10, MIN_FPS, MAX_FPS)") == 2
  # 1/0 would raise, so the pause branch has to come before the interval maths.
  assert cluster.index("if active_fps <= 0:") < cluster.index("interval = 1.0 / active_fps")
  # The panel itself never receives a zero refresh rate.
  assert "display.set_frame_rate(max(1, next_fps))" in cluster
