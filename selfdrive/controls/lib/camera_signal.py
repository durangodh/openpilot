"""Traffic light colour read by the HUD phone (S20) from the EON road camera.

Ported in spirit from carrot-wip's experimental signal supervision (Autoware traffic light
detector + classifier). The phone runs the networks on an upper region of each road photo
the EON serves (/api/road.jpg?signal=1, ~3 fps) and sends back a confirmed colour per photo
(HUDSIG1, remote_hud.py). This module stores and reads it.

It is evidence, not proof: the selected light may not be the one for ego's lane, and the
result arrives 0.5-1 s after the photo. Consumers may only use a fresh red to (a) accept a
weaker model slowing as a signal stop and (b) hold a model departure while stopped. It never
grants acceleration and never sets a stop position.

CameraSignal param: 0 off / 1 HUD display only (default) / 2 display + control assist.
"""
import json
import os
import time

CAMERA_SIGNAL_FILE = "/dev/shm/hud_camera_signal.json"
# Photo capture time (EON clock) to now; older results are ignored.
CAMERA_SIGNAL_MAX_AGE_S = 1.5
COLORS = ("red", "yellow", "green", "none")


def parse_hud_signal(payload):
  """HUDSIG1 body from the phone -> record to store, or None when malformed."""
  try:
    data = json.loads(payload)
    t_ms = int(data["t"])
    color = str(data.get("color", "none"))
  except (ValueError, TypeError, KeyError):
    return None
  if color not in COLORS or t_ms <= 0:
    return None
  label = str(data.get("label", ""))[:32]
  try:
    score = round(max(0.0, min(1.0, float(data.get("score", 0.0)))), 2)
  except (TypeError, ValueError):
    score = 0.0
  return {"t": t_ms, "color": color, "label": label, "score": score, "rx": round(time.time(), 3)}


def store_hud_signal(payload, path=CAMERA_SIGNAL_FILE):
  record = parse_hud_signal(payload)
  if record is None:
    return False
  tmp = path + ".tmp"
  with open(tmp, "w") as f:
    json.dump(record, f, separators=(",", ":"))
  os.replace(tmp, path)
  return True


class CameraSignalReader:
  """Latest camera colour for the planner; re-reads the file only when it changed."""

  def __init__(self, path=CAMERA_SIGNAL_FILE):
    self.path = path
    self.mtime = None
    self.record = None

  def update(self, now=None):
    try:
      mtime = os.stat(self.path).st_mtime_ns
      if mtime != self.mtime:
        with open(self.path) as f:
          self.record = json.load(f)
        self.mtime = mtime
    except (OSError, ValueError):
      self.record = None
      self.mtime = None
    return self.color(now)

  def color(self, now=None):
    record = self.record
    if not isinstance(record, dict):
      return "none"
    now = time.time() if now is None else now
    try:
      age = now - float(record["t"]) / 1000.0
    except (KeyError, TypeError, ValueError):
      return "none"
    if not -0.5 <= age <= CAMERA_SIGNAL_MAX_AGE_S:
      return "none"
    color = record.get("color")
    return color if color in COLORS else "none"
