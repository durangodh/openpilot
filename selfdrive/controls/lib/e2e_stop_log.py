"""Records traffic-signal stops for tuning day-time detection (2026-11-22).

The automatic signal stop (conditional_e2e) is inferred from the model's slowing path. It works
well at night but misses many day-time red lights, and navigation C-ITS signal data is missing at
many junctions. To tune the day-time thresholds on real numbers this logs, per line of JSON:

  AUTO    the automatic stop started: model values at that moment.
  MISSED  the driver braked to a standstill with no lead near (a stop the system did not take):
          model values when the brake was pressed and their extremes over the 3 s before.

Nothing here changes driving. View with: python selfdrive/debug/e2e_stop_log.py
"""
import json
import os
import time
from collections import deque

LOG_PATH = "/data/e2e_stop.log"
MAX_BYTES = 1024 * 1024
HISTORY_S = 3.0
MISSED_MIN_SPEED = 4.0       # m/s at the brake press (slower is creeping, not a signal approach)
MISSED_STOP_WINDOW_S = 15.0  # the standstill must follow the brake press within this
STOP_SPEED = 0.5
LEAD_NEAR_M = 60.0           # a lead this close at the brake press: following, not a signal stop
LEAD_STOPPED_AHEAD_M = 15.0  # a lead this close at standstill: stopped behind a car


def kst_hour(now_epoch):
  return time.gmtime(now_epoch + 9 * 3600).tm_hour


class E2EStopLog:
  def __init__(self, dt, path=LOG_PATH, clock=time.time):
    self.dt = dt
    self.path = path
    self.clock = clock
    self.history = deque(maxlen=max(1, int(round(HISTORY_S / dt))))
    self.t = 0.0
    self.prev_stopping = False
    self.prev_brake = False
    self.armed = None

  def _write(self, kind, rec):
    rec = dict(rec)
    now = self.clock()
    rec.update({"kind": kind, "epoch": round(now, 1), "hour": kst_hour(now)})
    try:
      if os.path.exists(self.path) and os.path.getsize(self.path) > MAX_BYTES:
        os.replace(self.path, self.path + ".1")
      with open(self.path, "a") as f:
        f.write(json.dumps(rec) + "\n")
    except OSError:
      pass

  def _snapshot(self, v_ego, model_x, model_v_end, model_v0, model_y, lead_d, signal_phase):
    xs = [h[0] for h in self.history] or [model_x]
    vs = [h[1] for h in self.history] or [model_v_end]
    return {
      "v": round(v_ego, 2), "mx": round(model_x, 1), "mv": round(model_v_end, 2),
      "mv0": round(model_v0, 2), "my": round(model_y, 2), "lead": round(lead_d, 1),
      "mx_min3s": round(min(xs), 1), "mv_min3s": round(min(vs), 2),
      "signal": signal_phase or "",
    }

  def update(self, *, enabled, stopping, v_ego, brake_pressed, gas_pressed, model_valid,
             model_x, model_v_end, model_v0, model_y, lead_present, lead_distance, signal_phase=None):
    self.t += self.dt
    lead_d = float(lead_distance) if lead_present else -1.0
    if model_valid:
      self.history.append((float(model_x), float(model_v_end)))
      if stopping and not self.prev_stopping:
        self._write("AUTO", self._snapshot(v_ego, model_x, model_v_end, model_v0, model_y, lead_d, signal_phase))
      brake_edge = brake_pressed and not self.prev_brake
      if (brake_edge and enabled and not stopping and v_ego >= MISSED_MIN_SPEED and
          not (lead_present and lead_distance < LEAD_NEAR_M)):
        self.armed = (self.t, self._snapshot(v_ego, model_x, model_v_end, model_v0, model_y, lead_d, signal_phase))
    self.prev_stopping = stopping
    self.prev_brake = brake_pressed

    if self.armed is not None:
      start, rec = self.armed
      if gas_pressed or self.t - start > MISSED_STOP_WINDOW_S:
        self.armed = None
      elif v_ego < STOP_SPEED:
        self.armed = None
        if not (lead_present and lead_distance < LEAD_STOPPED_AHEAD_M):
          rec = dict(rec)
          rec["brake_to_stop_s"] = round(self.t - start, 1)
          self._write("MISSED", rec)
