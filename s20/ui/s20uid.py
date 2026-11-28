#!/usr/bin/env python3
"""
UI data for the S20 screen app (s20/ui/app): openpilot state plus the model's path, lane lines, road edges and lead,
already projected into the road camera frame (1928x1208 px, the frame s20camerad publishes), written as JSON to
/s20share/ui.json about 20 times a second. The Android app draws the camera frame and this on top.
"""
import json, os, time
import numpy as np

from openpilot.cereal import messaging, log
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


def main():
  sm = messaging.SubMaster(["modelV2", "carState", "selfdriveState", "radarState", "extrinsicsCalibration", "deviceState",
                            "narrowRoadCameraState"], ignore_alive=True)
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
    out = {"t": time.time(), "w": CAM.width, "h": CAM.height, "frame": sm["narrowRoadCameraState"].frameId,
           "v_ego": cs.vEgoCluster or cs.vEgo, "v_cruise": cs.vCruiseCluster, "blink": [cs.leftBlinker, cs.rightBlinker],
           "steer": cs.steeringAngleDeg, "state": str(ss.state), "enabled": ss.enabled, "active": ss.active,
           "engageable": ss.engageable, "alert": [ss.alertText1, ss.alertText2], "alert_status": str(ss.alertStatus),
           "calib": str(calib.calStatus) if sm.seen["extrinsicsCalibration"] else "none",
           "model_ok": sm.seen["modelV2"] and sm.alive["modelV2"], "lanes": [], "edges": [], "path": [], "lead": None,
           "cpu_temp": max(sm["deviceState"].cpuTempC) if len(sm["deviceState"].cpuTempC) else 0}
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
