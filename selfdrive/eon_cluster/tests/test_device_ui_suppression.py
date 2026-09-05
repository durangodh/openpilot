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
      "EonClusterHudVisionDetector",
      "EonClusterHudVisionDetectorFps",
      "EonClusterHudVisionDetectorThreshold",
      "EonClusterHudFps",
      "EonClusterHudMapFps",
      "EonClusterHudBrightness",
      "EonClusterHudDayBrightness",
      "EonClusterHudNightBrightness",
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


def test_s9_hud_auto_brightness_uses_day_and_night_params():
  manager = (ROOT / "selfdrive" / "manager" / "manager.py").read_text(encoding="utf-8")
  params = (ROOT / "selfdrive" / "common" / "params.cc").read_text(encoding="utf-8")
  sender = (ROOT / "selfdrive" / "eon_cluster" / "remote_hud_s9.py").read_text(encoding="utf-8")
  service = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
             "main" / "java" / "ai" / "comma" / "remotehud" / "HudService.java").read_text(encoding="utf-8")

  assert '("EonClusterHudBrightness", "0")' in manager
  assert 'params.get("EonClusterHudBrightness") == b"65"' in manager
  assert 'params.put("EonClusterHudBrightness", "0")' in manager
  assert '_bounded_int("EonClusterHudBrightness", 0, 0, 100)' in sender
  assert 'currentState.optInt("hudBrightness", 0)' in service
  for key, default in (("EonClusterHudDayBrightness", "65"),
                       ("EonClusterHudNightBrightness", "35")):
    assert '("%s", "%s")' % (key, default) in manager
    assert '{"%s", PERSISTENT}' % key in params
    assert '"%s"' % key in sender

  assert 'currentState.optInt("hudDayBrightness", 65)' in service
  assert 'currentState.optInt("hudNightBrightness", 35)' in service
  assert '? configuredNightBrightness : configuredDayBrightness' in service


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
  assert "drawBsd" in renderer and "bsdWarning" in renderer and "bsdArcChunk" in renderer
  assert "BSD_CORE_ALPHA = 190f / 255f" in renderer
  assert "BSD_TRI_ALPHA = 225f / 255f" in renderer
  assert "drawMapContext" in renderer
  assert "HudMapStore" in renderer
  assert "visibleMapBuildings" in renderer
  assert "visibleMapBuildings[visible++] = i" in renderer
  assert "visibleMapAreas[visible++] = i" in renderer
  assert "clipMapArea" in renderer and "clipMapBoundary" in renderer
  assert renderer.index("drawMapAreas(snapshot.greens") < renderer.index("drawMapAreas(snapshot.waters")
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


def test_ego_brake_and_turn_lamps_use_original_size_without_green_arrows():
  service = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
             "main" / "java" / "ai" / "comma" / "remotehud" /
             "HudService.java").read_text(encoding="utf-8")
  renderer = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
              "main" / "java" / "ai" / "comma" / "remotehud" /
              "ModelWorldGL.java").read_text(encoding="utf-8")

  assert "EGO_CAR_WIDTH = 94f" in service
  assert "TURN_LAMP_SCALE = 1.00f" in service
  assert '"brakeLights": bool(_field(car, "brakeLights", False))' in (ROOT / "selfdrive" / "eon_cluster" / "remote_hud.py").read_text(encoding="utf-8")
  assert "drawEgoBrakeLamps(c, p, scratchRect, s)" in service
  assert 's.optBoolean("brakeLights", false)' in service
  assert "drawEgoTurnLamps(c, p, scratchRect, s)" in service
  assert "0.650f, 0.062f" in service
  assert 's.optBoolean("leftBlinker", false)' in service
  assert 's.optBoolean("rightBlinker", false)' in service
  assert "drawBlinkers(" not in service
  assert "Color.rgb(72, 226, 118)" not in service
  assert "EGO_SPRITE_W = 94f" in renderer


def test_primary_lead_source_and_distance_share_one_day_night_label():
  service = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
             "main" / "java" / "ai" / "comma" / "remotehud" /
             "HudService.java").read_text(encoding="utf-8")
  renderer = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
              "main" / "java" / "ai" / "comma" / "remotehud" /
              "ModelWorldGL.java").read_text(encoding="utf-8")

  assert "if (leadIndex == 0)" in service
  assert "drawLeadSourceLabel(c, p, leadSpriteInfo" in service
  assert 'String distanceLabel = String.format(Locale.US, "%d m"' in service
  assert 'String sourceLabel = vision' not in service
  assert 'Math.min(18f, width * 0.42f)' in service
  assert ': "RADAR"' in service
  assert "boolean placeRight" in service
  assert "frameDark ? Color.WHITE : Color.rgb(15, 20, 26)" in service
  assert "Color.rgb(255, 175, 3)" in service
  assert "drawLeadDistanceLabel" not in service
  assert "drawLeadCard(" not in service
  assert "Paint.Style.STROKE" in service
  assert "leadSpriteDistance(int index)" in renderer
  assert "leadSpriteValid[index]" in renderer
  assert "Color.rgb(255, 175, 3)" in renderer
  assert "Color.rgb(0, 82, 255)" in renderer


def test_remote_hud_displays_vision_candidates_without_control_feedback():
  remote = (ROOT / "selfdrive" / "eon_cluster" / "remote_hud.py").read_text(encoding="utf-8")
  wrapper = (ROOT / "selfdrive" / "eon_cluster" / "remote_hud_s9.py").read_text(encoding="utf-8")
  renderer = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
              "main" / "java" / "ai" / "comma" / "remotehud" /
              "ModelWorldGL.java").read_text(encoding="utf-8")
  service = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
             "main" / "java" / "ai" / "comma" / "remotehud" /
             "HudService.java").read_text(encoding="utf-8")

  assert 'VISION_OBJECTS_FILE = "/dev/shm/vision_vehicle_objects.json"' in remote
  assert '"visionObjects": _vision_objects(sm["modelV2"])' in remote
  assert '"src": "R" if bool(_field(lead, "radar", False)) else "V"' in remote
  assert 'for vehicle in packet.get("visionObjects") or []' in wrapper
  assert 'drawVisionObjects(scene.optJSONArray("visionObjects")' in renderer
  assert "nearTrackedLead(scene.optJSONObject(\"lead\")" in renderer
  assert "Math.min(objects.length(), 40)" in renderer
  assert "visionSprite" not in renderer
  assert "modelWorldGl.visionSprite" not in service
  assert "leadSpriteVision(int index)" in renderer
  assert "nearVisionObject(suppress, distance, lateral)" in renderer
  assert '"VISION %.0f%%"' in service
  assert '"RADAR"' in service
  assert '"SCC/RADAR"' not in service
  assert "visionLeadTint" in service
  # The new wire is consumed only by the HUD renderer, never RadarD/planner.
  assert "visionObjects" not in (ROOT / "selfdrive" / "controls" / "radard.py").read_text(encoding="utf-8")


def test_phone_vehicle_detector_is_bundled_rate_limited_and_display_only():
  manager = (ROOT / "selfdrive" / "manager" / "manager.py").read_text(encoding="utf-8")
  processes = (ROOT / "selfdrive" / "manager" / "process_config.py").read_text(encoding="utf-8")
  params = (ROOT / "selfdrive" / "common" / "params.cc").read_text(encoding="utf-8")
  preview = (ROOT / "selfdrive" / "eon_cluster" / "camera_preview.py").read_text(encoding="utf-8")
  sender = (ROOT / "selfdrive" / "eon_cluster" / "remote_hud.py").read_text(encoding="utf-8")
  service = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
             "main" / "java" / "ai" / "comma" / "remotehud" /
             "HudService.java").read_text(encoding="utf-8")
  phone = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
           "main" / "java" / "ai" / "comma" / "remotehud" /
           "PhoneVehicleDetector.java").read_text(encoding="utf-8")
  renderer = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
              "main" / "java" / "ai" / "comma" / "remotehud" /
              "ModelWorldGL.java").read_text(encoding="utf-8")
  gradle = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" /
            "build.gradle").read_text(encoding="utf-8")

  assert 'PythonProcess("hud_camera_previewd", "selfdrive.eon_cluster.camera_preview", enabled=EON)' in processes
  assert 'NativeProcess("vehicle_detectord", "selfdrive/modeld", ["./vehicle_detectord"], enabled=False)' in processes
  for key, default in (("EonClusterHudVisionDetector", "0"),
                       ("EonClusterHudVisionDetectorFps", "3"),
                       ("EonClusterHudVisionDetectorThreshold", "40")):
    assert '("%s", "%s")' % (key, default) in manager
    assert '{"%s", PERSISTENT}' % key in params
  assert 'PREVIEW_SIZE = (320, 240)' in preview
  assert 'return max(1, min(3, value))' in preview
  assert 'os.nice(10)' in preview
  assert '(b"CAM1", CAMERA_PREVIEW_FILE' in sender
  assert '"cameraGround": _camera_ground(sm["liveCalibration"])' in sender
  assert 'tagEquals(header, "CAM1")' in service
  assert "Process.THREAD_PRIORITY_BACKGROUND" in service
  assert "s9TempC >= 82f" in service and "s9TempC <= 78f" in service
  assert 'object.put("src", "P")' in phone
  assert "FADE_HOLD_MS = 450L" in (ROOT / "selfdrive" / "eon_cluster" /
          "android_hud" / "app" / "src" / "main" / "java" / "ai" /
          "comma" / "remotehud" / "CameraVehicleTracker.java").read_text(encoding="utf-8")
  assert "trackedOutput(observations, frameTime)" in phone
  assert "b.d=best.box.d*0.28+b.d*0.72" in (ROOT / "selfdrive" / "eon_cluster" /
          "android_hud" / "app" / "src" / "main" / "java" / "ai" /
          "comma" / "remotehud" / "CameraVehicleTracker.java").read_text(encoding="utf-8")
  assert "distanceStep" in phone and "lateralStep" in phone
  assert '"hudPathFlip"' not in phone
  assert 'object.put("type", normalizeVehicleType(vehicle.getLabel()))' in phone
  for vehicle_type in ("car", "truck", "bus", "motorcycle", "bicycle", "person"):
    assert '"%s"' % vehicle_type in phone
    assert '"%s"' % vehicle_type in renderer
  assert "drawVisionVehicleIcon" in renderer
  assert "drawTruckIcon" in renderer
  assert "drawBusIcon" in renderer
  assert "drawTwoWheelerIcon" in renderer
  assert "CAMERA_TO_BUMPER_M = 1.52f" in phone
  assert "mapHeading + headingError * 0.22" in renderer
  assert "Math.min(0.28, 12.0 / Math.max(1.0, jump))" in renderer
  assert 'context, "mobilenetv1.tflite", options' in phone
  assert 'tensorflow-lite-task-vision:0.4.0' in gradle
  model = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" /
           "src" / "main" / "assets" / "mobilenetv1.tflite")
  assert model.exists() and model.stat().st_size == 4185175
  # Legacy DLC is not started and no DLC is bundled.
  assert not (ROOT / "models" / "vehicle_detector.dlc").exists()


def test_c2_s9_status_card_restores_the_bottom_left_slot():
  service = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
             "main" / "java" / "ai" / "comma" / "remotehud" /
             "HudService.java").read_text(encoding="utf-8")

  assert "drawC2S9StatusCard(c, p, s, stale)" in service
  assert "scratchRect.set(8f, 376f, 156f, 454f)" in service
  assert 'textNormal(c, p, "C2", 33f, 403f, 11f' in service
  assert 'textNormal(c, p, "S9", 33f, 442f, 11f' in service
  assert 'systemValue(system, "temp", "°C")' in service
  assert 'systemValue(system, "cpu", "%")' in service
  assert 'String.format(Locale.US, "%.0f°C", s9TempC)' in service
  assert 'String.format(Locale.US, "%.0f%%", s9CpuPercent)' in service
  assert '14.5f, c2TempColor, Paint.Align.LEFT' in service
  assert '14.5f, c2CpuColor, Paint.Align.LEFT' in service
  assert '14.5f, phoneTempColor, Paint.Align.LEFT' in service
  assert '14.5f, phoneCpuColor, Paint.Align.LEFT' in service
  assert "c2TempValue >= 75d" in service
  assert "c2CpuValue > 90d" in service
  assert "s9TempC >= 75f" in service
  assert "s9CpuPercent > 90f" in service
  assert "Color.rgb(255, 58, 68)" in service
  assert 'Typeface.create("sans", Typeface.NORMAL)' in service
  assert "drawThermometerGlyph(c, p" in service
  assert "drawCpuGlyph(c, p" in service
  assert 'textNormal(c, p, "SoC"' not in service
  assert 'textNormal(c, p, "CPU"' not in service
  assert "Color.rgb(97, 213, 255)" in service
  assert "Color.rgb(157, 168, 255)" in service


def test_genesis_cluster_warnings_reach_external_hud():
  schema = (ROOT / "cereal" / "car.capnp").read_text(encoding="utf-8")
  carstate = (ROOT / "selfdrive" / "car" / "hyundai" / "carstate.py").read_text(
      encoding="utf-8")
  sender = (ROOT / "selfdrive" / "eon_cluster" / "remote_hud.py").read_text(
      encoding="utf-8")
  service = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
             "main" / "java" / "ai" / "comma" / "remotehud" /
             "HudService.java").read_text(encoding="utf-8")

  assert "aebSystemFault @60 :Bool" in schema
  assert "parkingSensors @61 :ParkingSensors" in schema
  assert '("FCA_Failinfo", "FCA11")' in carstate
  for signal in ("CF_Gway_PASDisplayFLH", "CF_Gway_PASDisplayFCTR",
                 "CF_Gway_PASDisplayFRH", "CF_Gway_PASDisplayRLH",
                 "CF_Gway_PASDisplayRCTR", "CF_Gway_PASDisplayRRH"):
    assert '("%s", "PAS11")' % signal in carstate
  assert '"aebSystemFault":' in sender
  assert '"parkingSensors": {' in sender
  assert "drawOemWarningPopup(c, p, s)" in service
  assert "drawAebSystemPopup(c, p)" in service
  assert "drawParkingSensorPopup(c, p, s)" in service
  assert "drawWhiteWarningPanel(c, p, cx)" in service


def test_rpm_arc_keeps_contrast_over_day_and_night_sky():
  service = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
             "main" / "java" / "ai" / "comma" / "remotehud" /
             "HudService.java").read_text(encoding="utf-8")

  driving = service.split("private void drawDriving", 1)[1].split(
      "private void drawAlert", 1)[0]
  assert driving.index("drawRpm(c, p") < driving.index("drawSpeed(c, p")
  rpm = service.split("private void drawRpm", 1)[1].split(
      "private static int mixColor", 1)[0]
  assert "float backdropR = r - 4f" in rpm
  assert "Color.argb(148, 7, 17, 26)" in rpm
  assert "Color.rgb(248, 250, 252)" in rpm
  assert "Color.rgb(0, 240, 224)" in rpm
  assert "Color.rgb(120, 133, 146)" in rpm
  assert "Color.rgb(52, 73, 88)" in rpm
  assert "RPM_BAR_W + 4f" in rpm
  assert "Color.rgb(255, 159, 50)" in rpm
  assert "Color.rgb(255, 63, 79)" in rpm


def test_wiper_mode_is_shown_beside_door_status():
  schema = (ROOT / "cereal" / "car.capnp").read_text(encoding="utf-8")
  carstate = (ROOT / "selfdrive" / "car" / "hyundai" / "carstate.py").read_text(
      encoding="utf-8")
  sender = (ROOT / "selfdrive" / "eon_cluster" / "remote_hud.py").read_text(
      encoding="utf-8")
  service = (ROOT / "selfdrive" / "eon_cluster" / "android_hud" / "app" / "src" /
             "main" / "java" / "ai" / "comma" / "remotehud" /
             "HudService.java").read_text(encoding="utf-8")

  assert "wiperMode @62 :UInt8" in schema
  for signal in ("CF_Gway_WiperAutoSw", "CF_Gway_WiperIntSw",
                 "CF_Gway_WiperLowSw", "CF_Gway_WiperHighSw",
                 "CF_Gway_WiperMistSw"):
    assert '("%s", "CGW1")' % signal in carstate
  assert '"wiperMode":' in sender
  lights = service.split("private void drawLights", 1)[1].split(
      "private int visibleWiperMode", 1)[0]
  assert lights.index("drawDoorStatus") < lights.index("drawWiperStatus")
  assert "WIPER_MODE_HIGHLIGHT_MS = 2500L" in service
  assert 'case 1: label = "AUTO"' in service
  assert 'case 3: label = "LOW"' in service
  assert 'case 4: label = "HIGH"' in service
