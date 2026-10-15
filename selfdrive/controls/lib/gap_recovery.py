"""Hold opening-gap headroom, then recover it with continuous two-stage dynamics.

Ported from carrot-wip longitudinal_gap_recovery.py (2026-10-15). When the lead
pulls away, part of the extra gap is kept as additional following time so ego
does not lunge to close it, then released smoothly (two first-order stages,
RECOVERY_TAU). Only a quarter of the excess gap is held, and nothing at
acquisition (carrot holds half, also at acquisition). The headroom is released
once the gap reaches 1.2-1.5x the normal following distance, and recovery is
slow while the lead is (nearly) stopped.

This fork applies the result as one additive t_follow for the current MPC
solve instead of carrot's per-horizon margins. The lead "track" is a counter
the caller increments when the lead changes (no radar track ids here).
"""
import math


RECOVERY_TAU = (5.0, 4.0, 3.0, 2.0, 1.0)
DEFAULT_LEVEL = 2                # 3 s, carrot's middle level
MAX_TOTAL_TF = 2.5
CAPTURE_FRACTION = 0.25  # carrot 0.5; half the excess gap kept following noticeably too far back
MIN_CAPTURE_SPEED = 1.0
STOPPED_SPEED = 0.3
FULL_RECOVERY_SPEED = 5.0
OPENING_SPEED = 0.2
OPENING_FILTER_TAU = 0.3
ENTRY_TIME = 0.8
MAX_CAPTURE_RISE = 0.5  # seconds of additional TF per second, after acquisition
HOLD_DISTANCE_START = 1.2
HOLD_DISTANCE_END = 1.5


class LeadGapState:
  def __init__(self):
    self.key = None
    self.extra_tf = 0.0
    self.recovery_tf = 0.0
    self.strength = 0.0
    self.filtered_relative_speed = 0.0

  def update(self, *, level, track_id, enabled, dt, ego_speed, lead_speed, relative_speed,
             distance, desired_distance, base_tf, stop_distance=6.0):
    values = (dt, ego_speed, lead_speed, relative_speed, distance, desired_distance, base_tf, stop_distance)
    if (not enabled or level not in range(len(RECOVERY_TAU)) or track_id < 0
        or not all(map(math.isfinite, values)) or dt <= 0.0 or ego_speed < 0.0 or distance <= 0.0
        or base_tf < 0.0 or stop_distance < 0.0):
      self.__init__()
      return 0.0

    cap = max(0.0, MAX_TOTAL_TF - base_tf)
    candidate = min(cap, CAPTURE_FRACTION * max(0.0, distance - desired_distance) / max(ego_speed, MIN_CAPTURE_SPEED))
    key = (level, track_id)
    acquired = key != self.key
    if acquired:
      # A newly acquired lead (or a range jump) starts with no headroom: the
      # gap it was found at is not a gap that opened, so it is not held.
      self.key, self.extra_tf, self.strength = key, 0.0, 0.0
      self.recovery_tf = 0.0
      self.filtered_relative_speed = relative_speed

    # Measured relative speed: opening can continue through a lead accel lull.
    self.filtered_relative_speed += -math.expm1(-dt / OPENING_FILTER_TAU) * (relative_speed - self.filtered_relative_speed)
    if not acquired:
      release = distance_recovery_weight(distance, ego_speed, base_tf, stop_distance)
      target = max(candidate, self.recovery_tf, self.extra_tf) if self.filtered_relative_speed > OPENING_SPEED else None
      self.recovery_tf, self.extra_tf = advance_headroom(
        self.recovery_tf, self.extra_tf, target, dt, lead_speed, RECOVERY_TAU[level], cap, release)

    self.extra_tf = min(cap, self.extra_tf)
    self.recovery_tf = min(cap, self.recovery_tf)
    self.strength = min(1.0, self.strength + dt / ENTRY_TIME)
    return self.extra_tf * entry_weight(self.strength)


def entry_weight(strength):
  """Zero entry slope at both ends of the acquisition interval."""
  return strength * strength * (3.0 - 2.0 * strength)


def distance_recovery_weight(distance, ego_speed, base_tf, stop_distance):
  """Smoothly remove hold between 1.2 and 1.5 times TF distance plus standstill gap."""
  reference = max(1e-3, ego_speed * base_tf + stop_distance)
  fraction = min(1.0, max(0.0, (distance / reference - HOLD_DISTANCE_START) / (HOLD_DISTANCE_END - HOLD_DISTANCE_START)))
  return entry_weight(fraction)


def advance_headroom(reservoir, extra, target, dt, lead_speed, tau, cap, release=0.0):
  """Charge/hold an absolute envelope, or release through two filter stages."""
  reservoir, extra = min(cap, reservoir), min(cap, extra)
  strength = float(recovery_strength(lead_speed))
  k = 2.0 * (strength + release * (1.0 - strength)) / tau
  if target is not None:
    target = min(cap, target) * (1.0 - release)
    rise = MAX_CAPTURE_RISE * (1.0 - release)
    if target >= reservoir and rise > 0.0:
      charge_time = min(dt, (target - reservoir) / rise)
      if k > 0.0:
        following = -math.expm1(-k * charge_time)
        extra += (reservoir - extra) * following + rise * (charge_time - following / k)
      reservoir += rise * charge_time
      if k > 0.0:
        extra += (reservoir - extra) * -math.expm1(-k * (dt - charge_time))
      return reservoir, min(cap, max(0.0, extra))
  else:
    target = 0.0
  if k <= 0.0:
    return reservoir, extra
  z = k * dt
  decay = math.exp(-z)
  return target + (reservoir - target) * decay, target + (extra - target + z * (reservoir - target)) * decay


def recovery_strength(lead_speed):
  return min(1.0, max(0.0, (lead_speed - STOPPED_SPEED) / (FULL_RECOVERY_SPEED - STOPPED_SPEED)))
