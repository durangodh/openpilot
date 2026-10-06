"""Bounded MPC response adjustment for a persistent near-path error.

This adjusts optimization weights, never actuator or safety limits. Distance is
measured against the current curvature at 0.8 seconds, before any new steering.
"""
import math


class LateralResponse:
  def __init__(self):
    self.previous_error = 0.0
    self.blend = 0.0

  def update(self, base_cost, preview_y, curvature, speed, active, dt):
    if not active or not all(math.isfinite(x) for x in
                             (base_cost, preview_y, curvature, speed, dt)) or dt <= 0:
      self.previous_error = 0.0
      self.blend = 0.0
      return base_cost

    error = preview_y - 0.5 * curvature * (speed * 0.8) ** 2
    # Two consecutive model frames must request the same direction. A single
    # jump or alternating target must not make the controller more aggressive.
    coherent = error * self.previous_error > 0 and abs(self.previous_error) > 0.12
    self.previous_error = error
    demand = max(0.0, min(1.0, (abs(error) - 0.12) / 0.23)) if coherent else 0.0
    speed_blend = min(max((speed - 1.5) / 1.5, 0.0),
                      max(min((22.2 - speed) / (22.2 - 13.9), 1.0), 0.0))
    target = demand * speed_blend
    # Fast, bounded engagement and a slower return avoid weight discontinuities.
    step = dt / (0.15 if target > self.blend else 0.5)
    self.blend += max(-step, min(step, target - self.blend))
    return max(200.0, base_cost * (1.0 - 0.15 * self.blend))
