#!/usr/bin/env python3
import math
from typing import NamedTuple, Optional


SCC_EMPTY_DISTANCE = 204.7
SCC_MIN_DISTANCE = 0.75
SCC_MAX_VALID_DISTANCE = SCC_EMPTY_DISTANCE - 0.7
SCC_MAX_ABS_YREL = 20.0
SCC_MAX_ABS_VREL = 100.0

SCC_DEFAULT_DT = 0.02
SCC_DROPOUT_HOLD_FRAMES = 3

SCC_DISTANCE_ALPHA = 0.65
SCC_VELOCITY_ALPHA = 0.65
SCC_ACCEL_ALPHA = 0.25
SCC_MIN_REL_ACCEL = -8.0
SCC_MAX_REL_ACCEL = 5.0


class SCCLeadSample(NamedTuple):
  track_id: int
  d_rel: float
  y_rel: float
  v_rel: float
  a_rel: float
  measured: bool


def scc_object_valid(obj_valid, obj_status, d_rel, y_rel, v_rel):
  """Validate a fresh SCC11 target without accepting the 204.7 m empty value."""
  values = (d_rel, y_rel, v_rel)
  if not all(math.isfinite(float(value)) for value in values):
    return False

  return (bool(obj_valid) and int(obj_status) != 0 and
          SCC_MIN_DISTANCE < float(d_rel) < SCC_MAX_VALID_DISTANCE and
          abs(float(y_rel)) <= SCC_MAX_ABS_YREL and
          abs(float(v_rel)) <= SCC_MAX_ABS_VREL)


class SCCLeadTracker:
  """Track the single SCC-selected lead without smearing target handoffs.

  SCC11 has no persistent object ID. A physically discontinuous measurement is
  therefore assigned a new monotonic track ID so RadarD also resets its lead
  acceleration state. Brief invalid status frames are extrapolated as
  unmeasured points; they are never allowed to outlive the short hold window.
  """

  def __init__(self, dt=SCC_DEFAULT_DT, dropout_hold_frames=SCC_DROPOUT_HOLD_FRAMES):
    self.dt = float(dt)
    self.dropout_hold_frames = int(dropout_hold_frames)
    self.next_track_id = 0
    self.track_id = None
    self.d_rel = 0.0
    self.y_rel = 0.0
    self.v_rel = 0.0
    self.a_rel = float('nan')
    self.last_raw_d_rel = 0.0
    self.last_raw_y_rel = 0.0
    self.last_raw_v_rel = 0.0
    self.missed_frames = 0
    self.active = False

  def _start_track(self, d_rel, y_rel, v_rel):
    self.track_id = self.next_track_id
    self.next_track_id += 1
    self.d_rel = float(d_rel)
    self.y_rel = float(y_rel)
    self.v_rel = float(v_rel)
    self.a_rel = float('nan')
    self.last_raw_d_rel = float(d_rel)
    self.last_raw_y_rel = float(y_rel)
    self.last_raw_v_rel = float(v_rel)
    self.missed_frames = 0
    self.active = True

  def _target_changed(self, d_rel, y_rel, v_rel):
    predicted_d_rel = self.d_rel + self.v_rel * self.dt
    distance_jump = abs(float(d_rel) - predicted_d_rel)
    velocity_jump = abs(float(v_rel) - self.last_raw_v_rel)
    lateral_jump = abs(float(y_rel) - self.last_raw_y_rel)

    distance_gate = max(4.0, min(10.0, abs(predicted_d_rel) * 0.18))
    return (distance_jump > 12.0 or
            (distance_jump > distance_gate and velocity_jump > 2.0) or
            velocity_jump > 8.0 or
            lateral_jump > 2.5)

  def _update_filter(self, d_rel, y_rel, v_rel):
    previous_v_rel = self.v_rel
    predicted_d_rel = self.d_rel + self.v_rel * self.dt
    distance_residual = float(d_rel) - predicted_d_rel

    self.d_rel = predicted_d_rel + SCC_DISTANCE_ALPHA * distance_residual
    # SCC distance is quantized to 0.1 m. Feeding its frame-to-frame derivative
    # into velocity creates several m/s^2 of false acceleration at 50 Hz, so
    # keep the distance correction separate from the reported relative speed.
    self.v_rel = ((1.0 - SCC_VELOCITY_ALPHA) * self.v_rel +
                  SCC_VELOCITY_ALPHA * float(v_rel))
    self.y_rel = float(y_rel)

    raw_accel = (self.v_rel - previous_v_rel) / self.dt
    raw_accel = max(SCC_MIN_REL_ACCEL, min(SCC_MAX_REL_ACCEL, raw_accel))
    if math.isfinite(self.a_rel):
      self.a_rel = (1.0 - SCC_ACCEL_ALPHA) * self.a_rel + SCC_ACCEL_ALPHA * raw_accel
    else:
      self.a_rel = raw_accel

    self.last_raw_d_rel = float(d_rel)
    self.last_raw_y_rel = float(y_rel)
    self.last_raw_v_rel = float(v_rel)
    self.missed_frames = 0

  def update(self, obj_valid, obj_status, d_rel, y_rel, v_rel) -> Optional[SCCLeadSample]:
    valid = scc_object_valid(obj_valid, obj_status, d_rel, y_rel, v_rel)
    if valid:
      if not self.active or self._target_changed(d_rel, y_rel, v_rel):
        self._start_track(d_rel, y_rel, v_rel)
      else:
        self._update_filter(d_rel, y_rel, v_rel)

      return SCCLeadSample(self.track_id, self.d_rel, self.y_rel, self.v_rel,
                           self.a_rel, True)

    if self.active and self.missed_frames < self.dropout_hold_frames:
      self.missed_frames += 1
      self.d_rel += self.v_rel * self.dt
      if self.d_rel > SCC_MIN_DISTANCE:
        if math.isfinite(self.a_rel):
          self.a_rel *= 0.5
        return SCCLeadSample(self.track_id, self.d_rel, self.y_rel, self.v_rel,
                             self.a_rel, False)

    self.active = False
    self.track_id = None
    self.missed_frames = 0
    return None
