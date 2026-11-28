#!/usr/bin/env python3
"""
UI data for the S20 screen app (s20/ui/app): openpilot state plus the model's path, lane lines, road edges and lead,
already projected into the road camera frame (1928x1208 px, the frame s20camerad publishes), written as JSON to
/s20share/ui.json about 20 times a second. The Android app draws the camera frame and this on top.
"""
import json, os, time
import numpy as np

from openpilot.cereal import messaging, log
from openpilot.common.params import Params
from s20.ui.settings_bridge import SettingsBridge
from openpilot.common.transformations.camera import DEVICE_CAMERAS
from openpilot.common.transformations.orientation import rot_from_euler
from openpilot.common.transformations.camera import view_frame_from_device_frame

SHARE = os.getenv("S20_SHARE", "/s20share")
OUT = os.path.join(SHARE, "ui.json")
CAM = DEVICE_CAMERAS[("pc", "unknown")].narrow_road  # the bridge makes the S20 frames look like this camera
HEIGHT_INIT = 1.22
MIN_DIST, MAX_DIST = 10.0, 100.0


def project(T: np.ndarray, pts: np.ndarray) -> np.ndarray:
  p = T @ pts.T
  ok = p[2] > 1e-3
  return (p[:2, ok] / p[2, ok]).T


def line_polygon(T, line: np.ndarray, half_w: float, z_off: float, max_x: float) -> list:
  pts = line[(line[:, 0] >= 0) & (line[:, 0] <= max_x)]
  if len(pts) < 2:
    return []
  left = pts + np.array([0, -half_w, z_off]); right = pts + np.array([0, half_w, z_off])
  l, r = project(T, left), project(T, right)
  n = min(len(l), len(r))
  if n < 2:
    return []
  poly = np.concatenate([l[:n], r[:n][::-1]])
  return np.round(poly).astype(int).tolist()


def offroad_info(params: Params) -> dict:
  """Home screen data like the comma 3 offroad screen: version and the offroad alerts (Offroad_* params)."""
  def s(k):
    try:
      v = params.get(k)
      return v.decode() if isinstance(v, bytes) else (v or "")
    except Exception:
      return ""
  alerts = []
  for k in params.all_keys():
    k = k.decode() if isinstance(k, bytes) else k
    if not k.startswith("Offroad_"):
      continue
    try:
      a = params.get(k)
    except Exception:
      continue
    if isinstance(a, (bytes, str)):
      try:
        a = json.loads(a)
      except Exception:
        a = None
    if isinstance(a, dict) and a.get("text"):
      alerts.append({"key": k[8:], "severity": int(a.get("severity", 0)), "text": a["text"].replace("%1", str(a.get("extra", "")))})
  alerts.sort(key=lambda a: -a["severity"])
  return {"version": s("Version"), "branch": s("GitBranch"), "commit": s("GitCommit")[:7], "dongle": s("DongleId"),
          "alerts": alerts}


def main():
  sm = messaging.SubMaster(["modelV2", "carState", "selfdriveState", "radarState", "extrinsicsCalibration", "deviceState",
                            "narrowRoadCameraState", "pandaStates", "carParams"], ignore_alive=True)
  params = Params()
  info, t_info = {}, 0.0
  settings, settings_vals, t_settings, seen_cmd = SettingsBridge(params, os.path.join(SHARE, "cmd")), {}, 0.0, None
  view_from_calib = view_frame_from_device_frame.copy()
  tmp = OUT + ".tmp"
  last_write = 0.0
  while True:
    sm.update(50)
    calib = sm["extrinsicsCalibration"]
    if sm.updated["extrinsicsCalibration"] and len(calib.rpyCalib) == 3:
      view_from_calib = view_frame_from_device_frame @ rot_from_euler(calib.rpyCalib)
    T = CAM.intrinsics @ view_from_calib
    z_off = calib.height[0] if len(calib.height) else HEIGHT_INIT

    m, cs, ss, rs = sm["modelV2"], sm["carState"], sm["selfdriveState"], sm["radarState"]
    if time.monotonic() - t_info > 5.0:
      info, t_info = offroad_info(params), time.monotonic()
    ds = sm["deviceState"]
    # settings screen of the app: apply its commands, report the values (like the comma 3X settings panels)
    settings.poll(engaged=ss.enabled)
    if time.monotonic() - t_settings > 1.0 or settings.last is not seen_cmd:  # right away after a command
      settings_vals, t_settings, seen_cmd = settings.values(), time.monotonic(), settings.last
    cp = sm["carParams"]
    model_alive = sm.seen["modelV2"] and sm.alive["modelV2"]
    pandas = [p for p in sm["pandaStates"] if str(p.pandaType) != "unknown"] if sm.seen["pandaStates"] and sm.alive["pandaStates"] else []
    out = {"t": time.time(), "w": CAM.width, "h": CAM.height, "frame": sm["narrowRoadCameraState"].frameId,
           "v_ego": cs.vEgoCluster or cs.vEgo, "v_cruise": cs.vCruiseCluster, "blink": [cs.leftBlinker, cs.rightBlinker],
           "steer": cs.steeringAngleDeg, "state": str(ss.state), "enabled": ss.enabled, "active": ss.active,
           "engageable": ss.engageable, "alert": [ss.alertText1, ss.alertText2], "alert_status": str(ss.alertStatus),
           "calib": str(calib.calStatus) if sm.seen["extrinsicsCalibration"] else "none",
           "model_ok": sm.seen["modelV2"] and sm.alive["modelV2"], "lanes": [], "edges": [], "path": [], "lead": None,
           "cpu_temp": max(ds.cpuTempC) if len(ds.cpuTempC) else 0,
           # home screen (offroad) like the comma 3: started comes from hardwared (ignition), demo mode has only the model
           "mode": os.getenv("S20_MODE", "manager"), "started": ds.started, "onroad": bool(ds.started or model_alive),
           "thermal": str(ds.thermalStatus) if sm.seen["deviceState"] else "", "mem": ds.memoryUsagePercent,
           "free_space": round(ds.freeSpacePercent), "panda": str(pandas[0].pandaType) if pandas else "",
           "ignition": any(p.ignitionLine or p.ignitionCan for p in pandas), **info,
           "settings": {"values": settings_vals, "last": settings.last, "engaged": ss.enabled,
                        "has_long": bool(sm.seen["carParams"] and cp.openpilotLongitudinalControl),
                        "alpha_avail": bool(sm.seen["carParams"] and cp.alphaLongitudinalAvailable),
                        "car": cp.carFingerprint if sm.seen["carParams"] else "",
                        "calib_perc": calib.calPerc if sm.seen["extrinsicsCalibration"] else 0,
                        "calib_rpy": [round(float(np.degrees(x)), 1) for x in calib.rpyCalib] if len(calib.rpyCalib) == 3 else []}}
    if sm.seen["modelV2"] and len(m.position.x):
      path = np.array([m.position.x, m.position.y, m.position.z]).T
      max_x = float(np.clip(path[-1, 0], MIN_DIST, MAX_DIST))
      lead = rs.leadOne if sm.seen["radarState"] else None
      if lead is not None and lead.status:
        max_x = float(np.clip(lead.dRel * 2.0 - min(lead.dRel * 0.7, 10.0), 0.0, max_x))
      out["path"] = line_polygon(T, path, 0.9, z_off, max_x)
      for i, ll in enumerate(m.laneLines):
        prob = float(m.laneLineProbs[i]) if i < len(m.laneLineProbs) else 0.0
        out["lanes"].append({"p": round(prob, 2), "poly": line_polygon(T, np.array([ll.x, ll.y, ll.z]).T, 0.025 * prob + 0.01, 0, MAX_DIST)})
      for i, re in enumerate(m.roadEdges):
        std = float(m.roadEdgeStds[i]) if i < len(m.roadEdgeStds) else 1.0
        out["edges"].append({"std": round(std, 2), "poly": line_polygon(T, np.array([re.x, re.y, re.z]).T, 0.025, 0, MAX_DIST)})
      if lead is not None and lead.status:
        idx = int(np.searchsorted(path[:, 0], lead.dRel).clip(0, len(path) - 1))
        p = project(T, np.array([[lead.dRel, -lead.yRel, path[idx, 2] + z_off]]))
        if len(p):
          out["lead"] = {"x": int(p[0, 0]), "y": int(p[0, 1]), "d": round(lead.dRel, 1), "v": round(lead.vRel, 1)}
    now = time.monotonic()
    if now - last_write >= 0.045:
      with open(tmp, "w") as f:
        json.dump(out, f, separators=(",", ":"))
      os.chmod(tmp, 0o644)
      os.replace(tmp, OUT)
      last_write = now


if __name__ == "__main__":
  main()
