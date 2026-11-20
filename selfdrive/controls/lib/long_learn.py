"""Learn the driver's own longitudinal habits while they drive themselves (LongLearn).

Only samples where neither openpilot nor the stock SCC controls speed are used, with a radar
lead (not vision only). Nothing is applied: recommend() turns the measurements into suggested
settings for the existing long params, shown by selfdrive/debug/long_learn.py.

gap         Steady following (lead speed within 0.7 m/s, |aEgo| < 0.5 for 3 s, above 8 m/s): one
            (distance, speed) sample per second. The MPC keeps t_follow * v + stopDistance at
            equal speeds, so the driver's t_follow is (distance - stop distance) / v.
stop        Distance to a stopped radar lead once the car has stood 1 s, and the decel used in
            the last 3 s / the last 1 s before standing.
start       Behind a stopped lead: time from the lead moving off to the car moving, and the mean
            acceleration over the first 2 s.
accel       1 s mean acceleration while the driver presses the gas and speeds up, with and
            without a close lead.
"""
from collections import deque

import numpy as np

DT = 0.01
GAP_BANDS = ((8.0, 15.0), (15.0, 22.0), (22.0, 30.0), (30.0, 45.0))  # m/s
GAP_STEADY_S = 3.0
GAP_MAX = 600                # samples kept per band
STOP_MAX = 200
START_MAX = 200
ACCEL_MAX = 600
MIN_GAP_SAMPLES = 300
MIN_STOPS = 5
MIN_STARTS = 5


def _median(values):
  return float(np.median(values)) if len(values) else None


class LongLearn:
  def __init__(self, saved=None):
    saved = saved or {}
    saved_gap = saved.get("gap", [])
    self.gap = [deque((tuple(x) for x in (saved_gap[i] if i < len(saved_gap) else [])), maxlen=GAP_MAX)
                for i in range(len(GAP_BANDS))]
    self.stops = deque((tuple(x) for x in saved.get("stops", [])), maxlen=STOP_MAX)
    self.starts = deque((tuple(x) for x in saved.get("starts", [])), maxlen=START_MAX)
    self.accel_free = deque(saved.get("accel_free", []), maxlen=ACCEL_MAX)
    self.accel_lead = deque(saved.get("accel_lead", []), maxlen=ACCEL_MAX)
    self.steady = 0.0
    self.next_gap = 0.0
    self.t = 0.0
    self.a_hist = deque(maxlen=int(3.0 / DT))
    self.standing = 0.0
    self.stop_logged = False
    self.waiting_lead = None     # time the stopped lead moved off
    self.start_t = None
    self.start_a = []
    self.accel_window = []

  def to_json(self):
    return {"gap": [[list(s) for s in band] for band in self.gap], "stops": list(self.stops),
            "starts": list(self.starts), "accel_free": list(self.accel_free),
            "accel_lead": list(self.accel_lead)}

  def update(self, human, v, a, gas, lead, d_rel, v_lead):
    """human: the driver controls speed (no openpilot / SCC). lead: a radar lead is present."""
    self.t += DT
    if not human:
      self._reset_events()
      return
    self.a_hist.append(a)
    self._gap(v, a, lead, d_rel, v_lead)
    self._stop_and_start(v, a, lead, d_rel, v_lead)
    self._accel(v, a, gas, lead and d_rel < 60.0)

  def _reset_events(self):
    self.steady = 0.0
    self.a_hist.clear()
    self.standing = 0.0
    self.stop_logged = False
    self.waiting_lead = self.start_t = None
    self.accel_window = []

  def _gap(self, v, a, lead, d_rel, v_lead):
    band = next((i for i, (lo, hi) in enumerate(GAP_BANDS) if lo <= v < hi), None)
    if band is None or not lead or not 5.0 < d_rel < 120.0 or abs(v_lead - v) > 0.7 or abs(a) > 0.5:
      self.steady = 0.0
      return
    self.steady += DT
    if self.steady >= GAP_STEADY_S and self.t >= self.next_gap:
      self.next_gap = self.t + 1.0
      self.gap[band].append((round(d_rel, 2), round(v, 2)))

  def _stop_and_start(self, v, a, lead, d_rel, v_lead):
    lead_stopped = lead and v_lead < 0.3 and d_rel < 20.0
    if v < 0.2:
      self.standing += DT
      if self.standing >= 1.0 and lead_stopped and not self.stop_logged:
        hist = list(self.a_hist)
        before = hist[:-100] if len(hist) > 100 else hist        # drop the second spent standing
        if before:
          self.stops.append((round(d_rel, 2), round(-min(before), 2), round(-float(np.mean(before[-100:])), 2)))
        self.stop_logged = True
      if self.stop_logged and lead and v_lead > 0.5 and self.waiting_lead is None:
        self.waiting_lead = self.t
    else:
      if self.waiting_lead is not None and self.start_t is None and v >= 0.5:
        self.start_t = self.t
        self.start_a = []
      self.standing = 0.0
      self.stop_logged = False
    if self.start_t is not None:
      self.start_a.append(a)
      if self.t - self.start_t >= 2.0:
        self.starts.append((round(self.start_t - self.waiting_lead, 2), round(float(np.mean(self.start_a)), 2)))
        self.start_t = self.waiting_lead = None

  def _accel(self, v, a, gas, close_lead):
    if gas and a > 0.3 and 2.0 < v < 30.0:
      self.accel_window.append(a)
      if len(self.accel_window) >= int(1.0 / DT):
        (self.accel_lead if close_lead else self.accel_free).append(round(float(np.mean(self.accel_window)), 2))
        self.accel_window = []
    else:
      self.accel_window = []

  def recommend(self, stop_distance_now, gap_now, t_follow_now, speed_ratio=1.2):
    """Suggested settings (x100 ints like the params) with the evidence behind them."""
    out = {"counts": {"gap": sum(len(b) for b in self.gap), "stops": len(self.stops),
                      "starts": len(self.starts), "accel_free": len(self.accel_free),
                      "accel_lead": len(self.accel_lead)}}
    stop_d = _median([s[0] for s in self.stops]) if len(self.stops) >= MIN_STOPS else None
    out["stop_distance_m"] = stop_d
    if stop_d is not None:
      out["StopDistance"] = int(round(float(np.clip(stop_d, 2.0, 10.0)) * 100))
    base = stop_d if stop_d is not None else stop_distance_now
    bands = []
    all_t = []
    for (lo, hi), band in zip(GAP_BANDS, self.gap):
      # The planner stretches TFollowGap from 1x at 40 km/h to TFollowSpeedRatio x at 100 km/h;
      # undo that so every speed band speaks of the same TFollowGap setting.
      t = [(d - base) / v / float(np.interp(v * 3.6, [40.0, 100.0], [1.0, speed_ratio])) for d, v in band if v > 1.0]
      bands.append({"kph": f"{lo * 3.6:.0f}-{hi * 3.6:.0f}", "n": len(t), "t_follow": _median(t)})
      all_t += t
    out["gap_bands"] = bands
    if len(all_t) >= MIN_GAP_SAMPLES:
      t_follow = float(np.clip(np.median(all_t), 0.8, 2.5))
      out["t_follow"] = round(t_follow, 2)
      out[f"TFollowGap{gap_now}"] = int(round(t_follow * 100))
      out["t_follow_now"] = t_follow_now
    if len(self.stops) >= MIN_STOPS:
      out["stop_decel_max"] = _median([s[1] for s in self.stops])
      out["stop_decel_last_1s"] = _median([s[2] for s in self.stops])
    if len(self.starts) >= MIN_STARTS:
      out["start_delay_s"] = _median([s[0] for s in self.starts])
      out["start_accel"] = _median([s[1] for s in self.starts])
    out["accel_free"] = _median(self.accel_free)
    out["accel_lead"] = _median(self.accel_lead)
    return out


# ── Stage 3: apply (LongLearnApply) ──────────────────────────────────────────
# Only the two settings with a direct meaning are applied, with floors the driver's habit can
# never go under: a human looking far ahead is safer at a given gap than the radar/camera MPC.
T_FOLLOW_BOUNDS = (1.2, 2.5)     # s
STOP_BOUNDS = (3.5, 10.0)        # m
T_FOLLOW_DOWN = 0.02             # s per step (a minute); longer gaps apply at once
STOP_DOWN = 0.2                  # m per step; longer distances apply at once


def _toward(applied, target, down):
  return target if target >= applied else max(target, applied - down)


def apply_step(applied, rec, gap_now, t_follow_now, stop_now):
  """applied: the previous LongLearnApplied dict (or {}). Returns the new one.
  The gap a learned time belongs to is the one in use; a different gap starts from its setting."""
  out = {"gap": gap_now}
  t_prev = applied.get("t_follow") if applied.get("gap") == gap_now else None
  t_prev = t_follow_now if t_prev is None else t_prev
  if rec.get("t_follow") is not None:
    out["t_follow"] = round(float(np.clip(_toward(t_prev, rec["t_follow"], T_FOLLOW_DOWN), *T_FOLLOW_BOUNDS)), 3)
  stop_prev = applied.get("stop_distance", stop_now)
  if rec.get("stop_distance_m") is not None and rec.get("StopDistance") is not None:
    out["stop_distance"] = round(float(np.clip(_toward(stop_prev, rec["StopDistance"] / 100.0, STOP_DOWN),
                                               *STOP_BOUNDS)), 3)
  return out
