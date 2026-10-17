"""Trip summary for the S9 HUD info box (EonClusterHudInfoBox = 2).

Ported from carrot-wip selfdrive/carrot/cluster/cluster_trip_report.py with
the same thresholds. Reset whenever the car goes onroad.
"""
import math

TRIP_MOVING_SPEED_MPS = 0.3
TRIP_MAX_DT_S = 0.5
TRIP_HARD_ACCEL_MPS2 = 2.5
TRIP_HARD_BRAKE_MPS2 = -3.0
TRIP_HARD_CORNER_MPS2 = 3.0
TRIP_CORNER_MIN_SPEED_MPS = 5.5
TRIP_EVENT_MIN_GAP_S = 1.5


class _Event:
  def __init__(self):
    self.active = False
    self.count = 0
    self.last_t = -1e9

  def update(self, t, triggered, held):
    if triggered and not self.active and t - self.last_t >= TRIP_EVENT_MIN_GAP_S:
      self.active = True
      self.count += 1
      self.last_t = t
    elif not held:
      self.active = False


class TripReport:
  def __init__(self):
    self.onroad = None
    self.reset()

  def reset(self):
    self.last_t = None
    self.duration_s = 0.0
    self.moving_s = 0.0
    self.distance_m = 0.0
    self.auto_distance_m = 0.0
    self.max_speed_kph = 0.0
    self.max_accel = 0.0
    self.max_decel = 0.0
    self.hard_accel = _Event()
    self.hard_brake = _Event()
    self.hard_corner = _Event()

  def update(self, t, onroad, speed, accel, enabled, curvature):
    if onroad and self.onroad is False:
      self.reset()
    self.onroad = onroad
    if not onroad:
      return
    speed = max(0.0, speed) if math.isfinite(speed) else 0.0
    accel = accel if math.isfinite(accel) else 0.0
    curvature = curvature if math.isfinite(curvature) else 0.0
    if self.last_t is None or t <= self.last_t:
      self.last_t = t if self.last_t is None else self.last_t
      return
    dt = min(t - self.last_t, TRIP_MAX_DT_S)
    self.last_t = t
    self.duration_s += dt
    step = speed * dt
    self.distance_m += step
    if speed >= TRIP_MOVING_SPEED_MPS:
      self.moving_s += dt
      if enabled:
        self.auto_distance_m += step
    self.max_speed_kph = max(self.max_speed_kph, speed * 3.6)
    self.max_accel = max(self.max_accel, accel)
    self.max_decel = min(self.max_decel, accel)
    self.hard_accel.update(t, accel >= TRIP_HARD_ACCEL_MPS2, accel >= TRIP_HARD_ACCEL_MPS2 * 0.8)
    self.hard_brake.update(t, accel <= TRIP_HARD_BRAKE_MPS2, accel <= TRIP_HARD_BRAKE_MPS2 * 0.8)
    lat = abs(speed * speed * curvature)
    fast = speed >= TRIP_CORNER_MIN_SPEED_MPS
    self.hard_corner.update(t, fast and lat >= TRIP_HARD_CORNER_MPS2, fast and lat >= TRIP_HARD_CORNER_MPS2 * 0.8)

  def snapshot(self):
    return {
      "time": int(self.duration_s),
      "dist": round(self.distance_m),
      "avg": round(self.distance_m / self.moving_s * 3.6, 1) if self.moving_s > 0 else 0.0,
      "max": round(self.max_speed_kph, 1),
      "auto": round(self.auto_distance_m / self.distance_m * 100.0) if self.distance_m > 0 else 0,
      "maxAccel": round(self.max_accel, 2),
      "maxDecel": round(self.max_decel, 2),
      "hardAccel": self.hard_accel.count,
      "hardBrake": self.hard_brake.count,
      "hardCorner": self.hard_corner.count,
    }
