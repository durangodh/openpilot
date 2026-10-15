"""Distance-aware vision curve cap, independent of lead and traffic-stop control.

Ported from carrot-wip selfdrive/carrot/curve_speed.py (2026-10-15):
  1. braking starts a response distance earlier (reaction + jerk-limited onset),
  2. a three-node median rejects isolated yaw spikes,
  3. the cap is released as soon as fresh model frames confirm a higher speed,
  4. only the first 6 s / 180 m of the model path and nodes >= 3 m/s are used.
The approach deceleration stays this fork's AutoCurveSpeedDecelRate setting.
"""
from collections import deque
import math

import numpy as np


UNLIMITED_SPEED = 250.0 / 3.6
MODEL_POINTS = 33
TARGET_LAT_ACCEL = 1.9          # m/s^2, this fork's comfort setting (same as carrot)
MIN_MODEL_SPEED = 3.0           # m/s; slower nodes give unreliable yaw/speed curvature
MAX_MODEL_TIME = 6.0            # s
MAX_PREVIEW_DISTANCE = 180.0    # m
RESPONSE_TIME = 1.0             # s, controller/actuator response before braking builds
APPROACH_JERK = 0.8             # m/s^3, gentle braking onset
RELEASE_HOLD = 0.35             # s without usable geometry before ramping the cap up
RELEASE_RATE = 7.2 / 3.6        # m/s per second while geometry is missing
GEOMETRY_RELEASE_WINDOW = 0.25  # s of fresh frames considered for a release
GEOMETRY_MIN_SPAN = 0.20        # s those frames must span to confirm a release


def curve_speed_target(model, factor, minimum_speed, approach_decel, v_ego=None, a_ego=0.0):
  """Return the approach speed in m/s, or None for an unusable model frame.

  The speed at each curve is sqrt(a_lat / curvature). From the point where
  braking can actually start (distance minus the response distance) the
  approach speed is sqrt(v_curve**2 + 2 * decel * braking_distance). Model
  speed at the SAME point converts yaw rate to curvature. v_ego defaults to the
  model's current speed.
  """
  try:
    x, y, t, speed, yaw = [np.asarray(value, dtype=float) for value in (
      model.position.x, model.position.y, model.position.t,
      model.velocity.x, model.orientationRate.z)]
    arrays = (x, y, t, speed, yaw)
    if any(a.shape != (MODEL_POINTS,) or not np.isfinite(a).all() for a in arrays):
      return None
    if not np.all(np.diff(t) > 0.0) or np.any(speed < 0.0):
      return None
    if not all(math.isfinite(v) for v in (factor, minimum_speed, approach_decel, a_ego)):
      return None
    v_ego = float(speed[0]) if v_ego is None else float(v_ego)
    if not math.isfinite(v_ego) or v_ego < 0.0:
      return None

    end = int(np.searchsorted(t, MAX_MODEL_TIME, side="right"))
    x, y, speed, yaw = x[:end], y[:end], speed[:end], yaw[:end]
    distances = np.concatenate(([0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))))
    max_distance = min(MAX_PREVIEW_DISTANCE, max(30.0, v_ego * MAX_MODEL_TIME))
    valid = speed >= MIN_MODEL_SPEED
    curvature = np.divide(yaw, speed, out=np.zeros_like(yaw), where=valid) * float(np.clip(factor, 0.5, 3.0))

    decel = float(np.clip(approach_decel, 0.1, 3.0))
    response_distance = v_ego * (RESPONSE_TIME + (max(0.0, float(a_ego)) + decel) / (2.0 * APPROACH_JERK))
    floor = float(np.clip(minimum_speed, 5.0 / 3.6, 80.0 / 3.6))
    n = len(distances)
    if n < 3:
      return UNLIMITED_SPEED
    # Three-node neighbourhood of every node (edges reuse the first/last three).
    starts = np.clip(np.arange(n) - 1, 0, n - 3)
    a, b, c = curvature[starts], curvature[starts + 1], curvature[starts + 2]
    median = a + b + c - np.maximum(np.maximum(a, b), c) - np.minimum(np.minimum(a, b), c)
    usable = (distances <= max_distance) & valid[starts] & valid[starts + 1] & valid[starts + 2] & \
             (distances[starts + 2] - distances[starts] >= 0.05) & (np.abs(median) >= 1e-6)
    if not np.any(usable):
      return UNLIMITED_SPEED
    point_speed = np.clip(np.sqrt(TARGET_LAT_ACCEL / np.abs(median[usable])), floor, UNLIMITED_SPEED)
    braking_distance = np.maximum(0.0, distances[usable] - response_distance)
    best = float(np.min(np.sqrt(point_speed ** 2 + 2.0 * decel * braking_distance)))
    return min(UNLIMITED_SPEED, best)
  except (AttributeError, TypeError, ValueError, OverflowError):
    return None


class VisionCurveSpeed:
  """Tighten immediately; release against a short history of fresh geometry."""
  def __init__(self):
    self.reset()

  def reset(self):
    self.target = UNLIMITED_SPEED
    self.now = 0.0
    self.invalid_time = 0.0
    self.geometry = deque()

  def update(self, model, factor, minimum_speed, approach_decel, dt, v_ego=None, a_ego=0.0, new_frame=True):
    dt = min(max(float(dt), 0.0), 0.5)
    self.now += dt
    raw = None if model is None else curve_speed_target(model, factor, minimum_speed, approach_decel, v_ego, a_ego)
    if raw is None:
      # Missing/invalid geometry is not evidence that the road has straightened.
      self.geometry.clear()
      self.invalid_time += dt
      if self.invalid_time >= RELEASE_HOLD:
        self.target = min(UNLIMITED_SPEED, self.target + RELEASE_RATE * dt)
      return self.target
    self.invalid_time = 0.0

    if not new_frame:
      # Re-reading one model frame is not independent confirmation of an exit,
      # but updated ego speed/acceleration may still tighten the same path.
      if raw < self.target:
        self.target = raw
        self.geometry.clear()
      return self.target

    if self.geometry and self.now - self.geometry[-1][0] > GEOMETRY_MIN_SPAN + 1e-6:
      self.geometry.clear()
    self.geometry.append((self.now, raw))
    while self.geometry and self.now - self.geometry[0][0] > GEOMETRY_RELEASE_WINDOW + 1e-6:
      self.geometry.popleft()
    confirmed = len(self.geometry) >= 3 and self.now - self.geometry[0][0] >= GEOMETRY_MIN_SPAN - 1e-6
    if raw <= self.target:
      # Never postpone a newly required lower speed with a comfort filter.
      self.target = raw
    elif confirmed:
      # Release the ceiling, not a commanded acceleration; the longitudinal
      # planner still limits acceleration and jerk.
      self.target = min(value for _, value in self.geometry)
    return self.target
