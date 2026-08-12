import math
import signal
import time

import cereal.messaging as messaging
from common.params import Params

from selfdrive.eon_cluster.renderer import HudRenderer, read_navi_state
from selfdrive.eon_cluster.scene import extract_driving_scene, extract_radar_points
from selfdrive.eon_cluster.trip import TripTracker
PARAM_ENABLED = "EonClusterHud"
PARAM_CONNECTED = "EonClusterHudConnected"
PARAM_BRIGHTNESS = "EonClusterHudBrightness"
PARAM_FPS = "EonClusterHudFps"
PARAM_JPEG_QUALITY = "EonClusterHudJpegQuality"
PARAM_SCREEN_MODE = "EonClusterHudScreenMode"
PARAM_THEME = "EonClusterHudTheme"
PARAM_ORIENTATION = "EonClusterHudOrientation"
PARAM_MIRROR = "EonClusterHudMirror"
PARAM_LANGUAGE = "EonClusterHudLanguage"
PARAM_RADAR_INFO = "EonClusterHudRadarInfo"
PARAM_RADAR_DISPLAY = "EonClusterHudRadarDisplay"
TURZX_92_PRODUCT_ID = 0x0092
RECONNECT_INTERVAL_S = 5.0
SETTINGS_POLL_INTERVAL_S = 0.25


def _param_int(params, key, default, minimum, maximum):
  try:
    raw = params.get(key)
    value = int(raw) if raw is not None else default
  except (TypeError, ValueError):
    value = default
  return max(minimum, min(maximum, value))


def _field(message, name, default=0.0):
  try:
    return getattr(message, name)
  except Exception:
    return default


def _param_bool(params, key, default=False):
  try:
    raw = params.get(key)
    if raw is None:
      return bool(default)
    return raw not in (b"0", "0", b"", "", False)
  except Exception:
    return bool(default)


def _orientation(params):
  return 2 if _param_int(params, PARAM_ORIENTATION, 0, 0, 2) == 2 else 0


def _resolved_brightness(params, device_state, camera_state=None, camera_valid=False):
  setting = _param_int(params, PARAM_BRIGHTNESS, 65, 0, 100)
  if setting > 0:
    return setting
  exposure = float(_field(camera_state, "exposureValPercent", float("nan")))
  if camera_valid and math.isfinite(exposure):
    lightness = max(0.0, min(100.0, 100.0 - exposure))
    normalized = lightness / 903.3 if lightness <= 8.0 else math.pow((lightness + 16.0) / 116.0, 3.0)
    return max(30, min(100, int(round(30.0 + max(0.0, min(1.0, normalized)) * 70.0))))
  screen_brightness = float(_field(device_state, "screenBrightnessPercent", 0.0) or 0.0)
  return max(10, min(100, int(round(screen_brightness)))) if screen_brightness > 0.0 else 65


def _energy_mode(car_params):
  fingerprint = str(_field(car_params, "carFingerprint", "") or "").upper()
  if any(token in fingerprint for token in ("PHEV", "HYBRID", " HEV")):
    return "HEV"
  if "ELECTRIC" in fingerprint or " EV" in fingerprint:
    return "EV"
  return ""


def _service_healthy(sm, service):
  try:
    return bool(sm.alive.get(service, False) and sm.valid.get(service, False))
  except Exception:
    return False


def main():
  params = Params()
  params.put_bool(PARAM_CONNECTED, False)
  running = [True]

  def stop(_signum, _frame):
    running[0] = False

  signal.signal(signal.SIGINT, stop)
  signal.signal(signal.SIGTERM, stop)
  sm = messaging.SubMaster(["carState", "carParams", "carControl", "controlsState", "deviceState", "modelV2",
                            "radarState", "liveTracks", "longitudinalPlan", "wideRoadCameraState"])
  display = None
  renderer = None
  usb_monitor = None
  next_connect = 0.0
  next_frame = 0.0
  next_settings_read = 0.0
  active_fps = 10
  last_render_error_log = 0.0
  trip = TripTracker()

  try:
    while running[0]:
      if not params.get_bool(PARAM_ENABLED):
        if display is not None:
          display.close()
          display = None
          renderer = None
          params.put_bool(PARAM_CONNECTED, False)
        if usb_monitor is not None:
          usb_monitor.close()
          usb_monitor = None
        time.sleep(1.0)
        continue

      now = time.monotonic()
      if display is None:
        if usb_monitor is None:
          from selfdrive.eon_cluster.usb_display import UsbEventMonitor
          usb_monitor = UsbEventMonitor(TURZX_92_PRODUCT_ID)
        if usb_monitor.poll():
          next_connect = 0.0
        if now < next_connect:
          time.sleep(min(0.2, next_connect - now))
          continue
        fps = _param_int(params, PARAM_FPS, 10, 5, 15)
        brightness = _resolved_brightness(params, sm["deviceState"], sm["wideRoadCameraState"],
                                          _service_healthy(sm, "wideRoadCameraState"))
        quality = _param_int(params, PARAM_JPEG_QUALITY, 58, 1, 95)
        try:
          # Keep the default-disabled process harmless even on images missing
          # an optional USB/crypto runtime dependency. Pin USB open to the
          # 9.2-inch TURZX PID like carrot-wip HUD mode 1 does, so another
          # supported TURZX panel cannot be selected accidentally.
          from selfdrive.eon_cluster.usb_display import TurzxDisplay
          display = TurzxDisplay(brightness=brightness, frame_rate=fps, orientation=_orientation(params),
                                 expected_product_id=TURZX_92_PRODUCT_ID)
          display.open()
          renderer = HudRenderer(display.landscape_size[0], display.landscape_size[1], quality)
          renderer.set_mirror(_param_bool(params, PARAM_MIRROR))
          params.put_bool(PARAM_CONNECTED, True)
          next_frame = now
          next_settings_read = now + SETTINGS_POLL_INTERVAL_S
          active_fps = fps
          print("EON cluster connected: pid=0x%04x, %dx%d, %d fps" %
                (display.product_id, display.landscape_size[0], display.landscape_size[1], fps), flush=True)
        except Exception as exc:
          if display is not None:
            display.close()
          display = None
          renderer = None
          params.put_bool(PARAM_CONNECTED, False)
          next_connect = now + RECONNECT_INTERVAL_S
          print("EON cluster waiting for TURZX display: %s" % exc, flush=True)
          continue

      if now >= next_settings_read:
        try:
          next_fps = _param_int(params, PARAM_FPS, 10, 5, 15)
          display.set_frame_rate(next_fps)
          display.set_brightness(_resolved_brightness(params, sm["deviceState"], sm["wideRoadCameraState"],
                                                      _service_healthy(sm, "wideRoadCameraState")))
          display.set_orientation(_orientation(params))
          renderer.set_jpeg_quality(_param_int(params, PARAM_JPEG_QUALITY, 58, 1, 95))
          renderer.set_mirror(_param_bool(params, PARAM_MIRROR))
          active_fps = next_fps
          next_settings_read = now + SETTINGS_POLL_INTERVAL_S
        except Exception as exc:
          print("EON cluster live setting failed: %s" % exc, flush=True)
          display.close()
          display = None
          renderer = None
          params.put_bool(PARAM_CONNECTED, False)
          next_connect = time.monotonic() + RECONNECT_INTERVAL_S
          continue

      interval = 1.0 / active_fps
      if now < next_frame:
        time.sleep(min(0.02, next_frame - now))
        continue
      next_frame = max(next_frame + interval, now)
      sm.update(0)
      car_state = sm["carState"]
      controls_state = sm["controlsState"]
      device_state = sm["deviceState"]
      speed_mps = float(_field(car_state, "vEgoCluster", _field(car_state, "vEgo", 0.0)))
      cruise_kph = float(_field(controls_state, "vCruiseCluster", _field(controls_state, "vCruise", 0.0)))
      enabled = bool(_field(controls_state, "enabled", False))
      try:
        scene = extract_driving_scene(sm["modelV2"], sm["radarState"])
        speed_kph = speed_mps * 3.6
        accels = _field(sm["longitudinalPlan"], "accels", [])
        accel = float(accels[0]) if accels is not None and len(accels) else 0.0
        trip.update(bool(_field(device_state, "started", False)), speed_kph, now, enabled, accel)
        tpms = _field(car_state, "tpms")
        scene["tpms"] = {key: _field(tpms, key, None) for key in ("fl", "fr", "rl", "rr")}
        # Genesis DH LCA11 (0x58B) exposes blind-spot presence, not target
        # distance. Pass the two stable booleans through to the vector HUD and
        # let the renderer place warning vehicles at fixed rear-quarter spots.
        scene["left_blindspot"] = bool(_field(car_state, "leftBlindspot", False))
        scene["right_blindspot"] = bool(_field(car_state, "rightBlindspot", False))
        scene["driving_mode"] = _param_int(params, "MyDrivingMode", 3, 1, 4)
        scene["panel_layout"] = _param_int(params, "EonClusterHudPanelLayout", 0, 0, 1)
        scene["screen_mode"] = _param_int(params, PARAM_SCREEN_MODE, 0, 0, 5)
        scene["theme"] = _param_int(params, PARAM_THEME, 0, 0, 2)
        scene["language"] = "en" if _param_int(params, PARAM_LANGUAGE, 0, 0, 1) == 1 else "ko"
        scene["is_metric"] = _param_bool(params, "IsMetric", True)
        scene["energy_mode"] = _energy_mode(sm["carParams"])
        scene["radar_info"] = _param_int(params, PARAM_RADAR_INFO, 4, 0, 4)
        if _param_int(params, PARAM_RADAR_DISPLAY, 0, 0, 1) == 1:
          scene["radar_points"] = extract_radar_points(sm["liveTracks"])
        scene["show_path_status_color"] = _param_int(params, "ShowPathStatusColor", 1, 0, 1) == 1
        scene["accel"] = accel
        actuators = _field(sm["carControl"], "actuatorsOutput", _field(sm["carControl"], "actuators"))
        steer_output = float(_field(actuators, "steer", 0.0) or 0.0)
        if abs(steer_output) < 1e-4:
          steer_output = max(-1.0, min(1.0, float(_field(car_state, "steeringAngleDeg", 0.0) or 0.0) / 90.0))
        scene["steer"] = max(-1.0, min(1.0, steer_output))
        cpu_values = list(_field(device_state, "cpuUsagePercent", []) or [])
        temp_values = list(_field(device_state, "cpuTempC", []) or [])
        free_space = float(_field(device_state, "freeSpacePercent", 0.0) or 0.0)
        scene["system"] = {
          "cpu": (sum(float(v) for v in cpu_values) / len(cpu_values)) if cpu_values else 0.0,
          "temp": (sum(float(v) for v in temp_values) / len(temp_values)) if temp_values else 0.0,
          "memory": float(_field(device_state, "memoryUsagePercent", 0.0) or 0.0),
          "disk": max(0.0, min(100.0, 100.0 - free_space)) if free_space > 0.0 else 0.0,
          "cores": [float(v) for v in cpu_values[:8]],
        }
        gear = str(_field(car_state, "gearShifter", "")).lower()
        scene["parked"] = gear in ("p", "park") or gear.endswith(".park")
        scene["trip_report"] = trip.snapshot()
        alert_text1 = str(_field(controls_state, "alertText1", "") or "")
        if alert_text1:
          scene["alert"] = {
            "text1": alert_text1,
            "text2": str(_field(controls_state, "alertText2", "") or ""),
            "status": str(_field(controls_state, "alertStatus", "")),
          }
        navi_state = read_navi_state()
        scene["navi_live"] = bool(navi_state)
        frame = renderer.render(speed_kph, cruise_kph, enabled, navi_state, scene)
      except Exception as exc:
        # Rendering failures (for example an EON Pillow build without the
        # optional _imagingft module) must not be treated as USB disconnects.
        # Closing the display here sends brightness 0 and creates a visible
        # black/reconnect loop even though the USB transport is healthy.
        if now - last_render_error_log >= RECONNECT_INTERVAL_S:
          print("EON cluster render failed: %s" % exc, flush=True)
          last_render_error_log = now
        continue

      try:
        display.send_jpeg(renderer.encode_portrait_jpeg(frame))
      except Exception as exc:
        print("EON cluster USB frame failed: %s" % exc, flush=True)
        display.close()
        display = None
        renderer = None
        params.put_bool(PARAM_CONNECTED, False)
        next_connect = time.monotonic() + RECONNECT_INTERVAL_S
  finally:
    if display is not None:
      display.close()
    if usb_monitor is not None:
      usb_monitor.close()
    params.put_bool(PARAM_CONNECTED, False)


if __name__ == "__main__":
  main()
