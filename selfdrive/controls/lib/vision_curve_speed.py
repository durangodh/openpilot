"""Distance-aware vision curve cap, independent of lead and traffic-stop control."""
import math

import numpy as np


UNLIMITED_SPEED = 250.0 / 3.6
RELEASE_HOLD = 0.4
RELEASE_RATE = 1.0  # m/s per second; only relax an existing speed cap gradually
INVALID_HOLD = 0.6
MODEL_POINTS = 33


def curve_speed_target(model, factor, minimum_speed, approach_decel):
  """Return the approach speed in m/s, or None for an unusable model frame.

  The speed at each curve is sqrt(a_lat / curvature). Distance to that point
  allows an approach speed sqrt(v_curve**2 + 2 * decel * distance). Use model
  speed at the SAME point to convert yaw rate to curvature, rather than ego
  speed for the whole horizon. Keep this fork's 1.9 m/s^2 comfort setting.
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
    if not all(math.isfinite(v) for v in (factor, minimum_speed, approach_decel)):
      return None
    distances = np.concatenate(([0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))))
    # Standstill / terminal stop samples cannot provide a useful yaw/speed ratio.
    usable = speed >= 1.0
    if not np.any(usable):
      return UNLIMITED_SPEED
    curvature = np.abs(yaw[usable]) / speed[usable] * np.clip(factor, 0.5, 3.0)
    point_speed = np.clip(np.sqrt(1.9 / np.maximum(curvature, 1e-9)),
                          np.clip(minimum_speed, 5.0 / 3.6, 80.0 / 3.6), UNLIMITED_SPEED)
    approach = np.sqrt(point_speed**2 + 2.0 * np.clip(approach_decel, 0.1, 3.0) * distances[usable])
    return float(min(UNLIMITED_SPEED, np.min(approach)))
  except (AttributeError, TypeError, ValueError, OverflowError):
    return None


class VisionCurveSpeed:
  def __init__(self):
    self.reset()

  def reset(self):
    self.target = UNLIMITED_SPEED
    self.release_time = 0.0
    self.invalid_time = 0.0

  def update(self, model, factor, minimum_speed, approach_decel, dt):
    dt = min(max(float(dt), 0.0), 0.5)
    raw = curve_speed_target(model, factor, minimum_speed, approach_decel)
    if raw is None:
      self.invalid_time += dt
      if self.invalid_time <= INVALID_HOLD:
        self.release_time = 0.0
        return self.target
      raw = UNLIMITED_SPEED
    else:
      self.invalid_time = 0.0

    if raw <= self.target:
      # Never postpone a newly required lower speed with a comfort filter.
      self.target = raw
      self.release_time = 0.0
    else:
      self.release_time += dt
      if self.release_time >= RELEASE_HOLD:
        self.target = min(raw, self.target + RELEASE_RATE * dt)
    return self.target
