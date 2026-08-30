from pathlib import Path


ROOT = Path(__file__).parents[3]
UI_DIR = ROOT / "selfdrive" / "ui" / "qt"

# OSM/OpenGL 전환으로 사라진 파라미터. 어느 층에도 남아 있으면 안 된다.
DEAD_PARAMS = ("EonClusterHudWorldWidth", "EonClusterHudBuildings",
               "EonClusterHudCarStyle", "EonClusterHudRoadSigns",
               "EonClusterHudGl", "EonClusterHudOutputTarget")


def _code_only(source):
  """전체 줄 주석을 걷어낸 소스. 주석에 남은 설명 문구가 단언에 걸리지 않게 한다."""
  return "\n".join(line for line in source.splitlines()
                    if not line.lstrip().startswith("//"))


def test_s9_connection_does_not_hide_eon_driving_ui():
  onroad = (UI_DIR / "onroad.cc").read_text(encoding="utf-8")
  header = (UI_DIR / "onroad.h").read_text(encoding="utf-8")
  assert 'getBool("EonClusterHudConnected")' not in onroad
  assert "eon_cluster_hud_connected" not in onroad
  assert "eon_cluster_hud_connected" not in header
  assert "drawLaneLines(p, s);" in onroad
  assert "drawCarrotPlot(p);" in onroad
  assert "drawCarrotLead(p);" in onroad
  # 내비 이미지 로드는 외부 HUD 생존 여부로만 가른다(현재는 s9HudActive).
  # 인자가 무엇으로 바뀌든 폰 ACK 파라미터에 묶이면 안 된다.
  navi_arg = onroad.split("updateCarrotNavi(", 1)[1].split(")", 1)[0]
  assert "EonClusterHudConnected" not in navi_arg
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
  # 라벨 문구와 기본값은 자주 손보는 부분이라 검사하지 않는다.
  # 여기서 지키려는 것은 "설정 UI 에서 손댈 수 있는가" 하나다.
  settings = (UI_DIR / "offroad" / "settings.cc").read_text(encoding="utf-8")
  exposed = (
      "EonClusterHud",
      "EonClusterHudFps",
      "EonClusterHudMapFps",
      "EonClusterHudBrightness",
      "EonClusterHudJpegQuality",
      "EonClusterHudOutputMode",
      "EonClusterHudLayoutMode",
      "EonClusterHudScreenMode",
      "EonClusterHudTheme",
      "EonClusterHudOrientation",
      "EonClusterHudMirror",
      "EonClusterHudLanguage",
      "EonClusterHudRadarInfo",
      "EonClusterHudNavRoute",
  )
  for key in exposed:
    assert '"%s"' % key in settings, key
  for dead in DEAD_PARAMS:
    assert dead not in settings, dead


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
  assert '{"EonClusterHudLayoutMode", PERSISTENT}' in params


def test_hud_output_is_external_panel_only():
  """순정 화면(nMirror) 출력 경로가 남아 있지 않은지."""
  manager = (ROOT / "selfdrive" / "manager" / "manager.py").read_text(encoding="utf-8")
  remote = (ROOT / "selfdrive" / "eon_cluster" / "remote_hud_s9.py").read_text(encoding="utf-8")
  java = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
          "main" / "java" / "ai" / "comma" / "remotehud")
  service = (java / "HudService.java").read_text(encoding="utf-8")
  prefs = (java / "AppPrefs.java").read_text(encoding="utf-8")
  main = (java / "MainActivity.java").read_text(encoding="utf-8")
  manifest = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
              "main" / "AndroidManifest.xml").read_text(encoding="utf-8")

  # 레이아웃 모드는 남는다(패널 안 3분할/2분할).
  assert 'packet["hudLayoutMode"] = _bounded_int("EonClusterHudLayoutMode", 1, 1, 2)' in remote
  assert '("EonClusterHudLayoutMode", "1")' in manager

  # 출력 대상 선택과 순정 화면 렌더 경로는 사라졌다.
  for gone in ("hudOutputTarget", "configuredOutputTarget", "phoneOutputEnabled",
               "usbOutputEnabled", "renderNativePhone", "nativeLayoutRendering",
               "drawFullscreenFrame", "drawNativeSystemPanel", "DISPLAY_PROFILE"):
    assert gone not in service, gone
  assert "DISPLAY_PROFILE" not in prefs
  assert "setDisplayProfile" not in main
  assert not (java / "HudFullscreenActivity.java").exists()
  assert not (java / "HudFavoriteActivity.java").exists()
  assert "HudFullscreenActivity" not in manifest
  assert "HudFavoriteActivity" not in manifest

  # phoneFrame 은 USB 회전 전 논리 프레임이라 남아 있어야 한다.
  assert "renderUsbFromPhone" in service
  assert "beginPhoneFrame" in service


def test_world3d_geometry_calibration_reaches_existing_renderer():
  manager = (ROOT / "selfdrive" / "manager" / "manager.py").read_text(encoding="utf-8")
  params = (ROOT / "selfdrive" / "common" / "params.cc").read_text(encoding="utf-8")
  settings = (UI_DIR / "offroad" / "settings.cc").read_text(encoding="utf-8")
  remote = (ROOT / "selfdrive" / "eon_cluster" / "remote_hud_s9.py").read_text(encoding="utf-8")

  assert '("EonClusterHudViewPitch", "0")' in manager
  assert '{"EonClusterHudViewPitch", PERSISTENT}' in params
  assert '"EonClusterHudViewPitch", "S9 HUD VIEW PITCH (X0.1°)"' in settings
  assert 'math.radians(view_pitch * 0.1)' in remote

  # OSM/건물 렌더가 빠지면서 사장된 파라미터는 어느 층에도 남아 있으면 안 된다.
  for dead in ("EonClusterHudWorldWidth", "EonClusterHudBuildings"):
    assert dead not in manager
    assert dead not in params
    assert dead not in settings
    assert dead not in remote


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
  # 2026-08-19 이후 EON 은 하트비트만 본다. 주석에는 그 경위가 남아 있으므로
  # 실행 코드에서만 사라졌는지 확인한다.
  assert "EonClusterHudConnected" not in _code_only(onroad)


def test_external_map_renderer_is_completely_removed():
  java = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
          "main" / "java" / "ai" / "comma" / "remotehud")
  service = (java / "HudService.java").read_text(encoding="utf-8")
  renderer = (java / "ModelWorldGL.java").read_text(encoding="utf-8")
  sender = (ROOT / "selfdrive" / "eon_cluster" / "remote_hud_s9.py").read_text(encoding="utf-8")

  assert not (java / "OsmWorld.java").exists()
  assert not (java / "OsmRoadMatcher.java").exists()
  # World3D 폴백은 제거됐다. ModelWorldGL 이 유일한 주행씬 렌더러다.
  assert not (java / "World3D.java").exists()
  assert "World3D" not in service
  assert "OsmWorld" not in service
  assert "hudBuildings" not in service + sender
  assert "laneInsideRoadEdges" in renderer
  assert "ROAD_EDGE_SAMPLE_XS" in renderer
  assert "GLES20.glFinish()" not in renderer


def test_s9_v6_visual_layers_and_local_map_context():
  java = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
          "main" / "java" / "ai" / "comma" / "remotehud")
  renderer = (java / "ModelWorldGL.java").read_text(encoding="utf-8")
  sender = (ROOT / "selfdrive" / "eon_cluster" / "remote_hud.py").read_text(
      encoding="utf-8")

  assert '"v": 6' in sender
  assert '"mapPose": map_pose' in sender
  assert '"desiredDistance":' in sender
  assert "drawPathLayers" in renderer
  assert "leadDistance[0] - 2.6f" in renderer
  assert "drawRoadEdge" in renderer
  assert "drawLaneMarking" in renderer
  assert "drawDesiredDistance" in renderer
  assert "routeShadow" in renderer
  assert "lineScreenX" in renderer and "lineScreenY" in renderer
  # 개수를 못 박으면 렌더러에 버퍼가 하나 늘 때마다 깨진다. 화면좌표 버퍼가
  # MAX_POINTS 로 한계 지어져 있다는 사실만 확인한다.
  assert renderer.count("new float[MAX_POINTS]") >= 5
  assert "bsdInnerX" in renderer and "bsdOuterX" in renderer
  assert "drawMapContext" in renderer
  assert "HudMapStore" in renderer
  assert (java / "HudMapStore.java").exists()


def test_android_hud_receiver_uses_s9_proven_direct_binding():
  service = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
             "main" / "java" / "ai" / "comma" / "remotehud" /
             "HudService.java").read_text(encoding="utf-8")
  receive = service.split("private void receiveLoop()", 1)[1].split(
      "private static boolean tagEquals", 1)[0]

  assert "new DatagramSocket(7210)" in receive
  assert "UDP_PACKET_MAX_BYTES = 65507" in service
  assert "new byte[UDP_PACKET_MAX_BYTES]" in receive
  assert "new DatagramSocket(null)" not in receive
  assert "socket.setReuseAddress(true)" not in receive
  assert "socket.bind(new InetSocketAddress(7210))" not in receive
  assert "udpLastRawRxElapsed = SystemClock.elapsedRealtime()" in receive
  assert "catch (JSONException malformed)" in receive


def test_android_hud_status_shows_actual_apk_and_udp_stage():
  activity = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
              "main" / "java" / "ai" / "comma" / "remotehud" /
              "MainActivity.java").read_text(encoding="utf-8")
  assert '"v" + appVersionName()' in activity
  assert '"데이터 대기 · UDP 포트 정상"' in activity
  assert '"원시 패킷 "' in activity
  assert '"UDP 포트 오류"' in activity
