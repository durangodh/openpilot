"""Bounded early deceleration when the lead starts braking relative to ego.

Ported from carrot-wip longitudinal_preview.py (2026-10-15, NORMAL mode tuning).
When the lead's acceleration falls below ego's by more than 0.1 m/s^2, the
controller reads the MPC plan up to 1.5 s further ahead (the MPC already
contains the coming deceleration), so braking starts earlier. The result can
only lower the target: while the base target accelerates it may at most cut it
to -0.03 m/s^2, and while already braking it may add at most 0.08 m/s^2.
The preview advances at 1.6 s/s and releases at 0.6 s/s.
"""
import math


LEAD_ACCEL_DEADBAND = 0.10
EGO_ACCEL_LIMIT = 3.0
DECEL_FACTOR = 1.0
DECEL_PREVIEW_MAX = 1.5
PREBRAKE_FLOOR = -0.03
ACTIVE_DECEL_DELTA_MAX = 0.08
ATTACK_RATE = 0.08 / 0.05   # preview seconds per second
RELEASE_RATE = 0.03 / 0.05
MIN_ACTION_TIME = 0.05
MAX_ACTION_TIME = 2.50


def lead_accel_signal(a_lead, a_ego):
  value = float(a_lead) - max(-EGO_ACCEL_LIMIT, min(EGO_ACCEL_LIMIT, float(a_ego)))
  if value > LEAD_ACCEL_DEADBAND:
    return value - LEAD_ACCEL_DEADBAND
  if value < -LEAD_ACCEL_DEADBAND:
    return value + LEAD_ACCEL_DEADBAND
  return 0.0


def apply_preview_target(base_target, preview_target):
  """Only bounded extra deceleration; never add acceleration."""
  base = float(base_target)
  candidate = min(float(preview_target), base)
  if base > 0.0:
    return max(candidate, PREBRAKE_FLOOR)
  return max(candidate, base - ACTIVE_DECEL_DELTA_MAX)


class LeadDecelPreview:
  def __init__(self):
    self.preview_s = 0.0

  def reset(self):
    self.preview_s = 0.0

  def update(self, lead, a_ego, dt):
    """Return the rate-limited preview offset in seconds (0 = none)."""
    target = 0.0
    try:
      if lead is not None and lead.status and math.isfinite(lead.aLeadK) and math.isfinite(a_ego):
        signal = lead_accel_signal(lead.aLeadK, a_ego)
        if signal < 0.0:
          target = min(-signal * DECEL_FACTOR, DECEL_PREVIEW_MAX)
    except (AttributeError, TypeError, ValueError):
      target = 0.0
    dt = max(0.0, float(dt))
    step = (ATTACK_RATE if target > self.preview_s and target > 0.0 else RELEASE_RATE) * dt
    self.preview_s = max(self.preview_s - step, min(self.preview_s + step, target))
    return self.preview_s


def action_time(base_action_t, preview_s):
  return max(MIN_ACTION_TIME, min(MAX_ACTION_TIME, float(base_action_t) + float(preview_s)))
