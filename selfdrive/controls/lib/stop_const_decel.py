"""Stop behind a stopping/stopped lead with one steady deceleration.

The MPC obstacle distance includes t_follow * v, so on approach to a stopped
car it brakes hard early and then eases off, finishing with a slow crawl.
This controller instead computes the deceleration that stops exactly
STOP_GAP behind the lead's (predicted) stopping point,

  a_req = v^2 / (2 * (d_rel + v_lead^2 / (2 * LEAD_DECEL) - STOP_GAP)),

waits until a_req reaches START_DECEL and then follows it, which keeps the
brake pressure nearly constant down to the stop.  The MPC plan is used
unchanged whenever a_req exceeds FALLBACK_DECEL or the gap is already below
STOP_GAP (late detection, cut-in), so the override never brakes less in a
hard situation.

Replay of 7 logged stops (2026-10-03/04): peak decel 1.82 -> 1.33 m/s^2,
decel spread 0.59 -> 0.43, time below 7 km/h 4.6 s -> 2.4 s, final gap
2.7-5.5 m -> 3.8-4.1 m.
"""
import math

import numpy as np

STOP_GAP = 4.0          # m, radar distance to keep at standstill
START_DECEL = 0.8       # m/s^2, begin once the required decel reaches this
RELEASE_DECEL = 0.3     # m/s^2, lead pulled away: hand back to the MPC
FALLBACK_DECEL = 2.5    # m/s^2, above this the MPC plan is used as is
LEAD_DECEL = 2.0        # m/s^2, assumed lead decel to predict where it stops
JERK = 2.0              # m/s^3, onset/offset rate of the planned decel
MIN_EGO_SPEED = 2.0     # m/s, below this the normal stop-and-go logic stays
MAX_LEAD_SPEED = 30.0 / 3.6   # only leads that are slow or slowing down
SLOW_LEAD_SPEED = 3.0   # m/s, a lead this slow counts as stopping
SLOWING_LEAD_ACCEL = -0.3     # m/s^2, a faster lead must be decelerating
V_STOPPING = 0.8        # m/s, finish with STOPPING_DECEL
STOPPING_DECEL = 0.6
MAX_DECEL = 3.5
# The lead can drop out for a moment (vision-only lead, 2026-10-05: 2 s at
# 70 m). Resetting released the brake to ~0 and the stop then needed up to
# 2.2 m/s^2. Keep the last target this long instead.
LEAD_LOST_HOLD = 1.5    # s


def required_decel(v_ego, d_rel, v_lead):
  """Deceleration (positive) that stops STOP_GAP behind the lead's stop point."""
  v_lead = max(0.0, v_lead)
  room = d_rel + v_lead * v_lead / (2.0 * LEAD_DECEL) - STOP_GAP
  if room <= 0.3:
    return float('inf')
  return v_ego * v_ego / (2.0 * room)


class ConstDecelStop:
  def __init__(self):
    self.active = False
    self.decel = 0.0        # current planned decel (positive), rate limited
    self.target = 0.0
    self.lost_time = 0.0

  def reset(self):
    self.active = False
    self.decel = 0.0
    self.target = 0.0
    self.lost_time = 0.0

  def _ramp(self, target, dt):
    self.target = target
    step = JERK * dt
    self.decel += max(-step, min(step, target - self.decel))
    return self.decel, target

  def update(self, allowed, v_ego, lead_status, d_rel, v_lead, a_lead, a_now, dt):
    """Returns (planned decel now, target decel), both positive m/s^2, or None
    to keep the MPC plan.  a_now is the accel currently being planned; the
    override starts from it and returns to it on fallback, so switching
    between the two never steps the command."""
    if not allowed or v_ego < 0.1:
      self.reset()
      return None
    if not lead_status or not math.isfinite(d_rel) or not math.isfinite(v_lead):
      # Brief dropout while stopping: keep braking toward the last target.
      if self.active and self.target > 0.0 and self.lost_time + dt <= LEAD_LOST_HOLD + 1e-6:
        self.lost_time += dt
        return self._ramp(self.target, dt)
      self.reset()
      return None
    self.lost_time = 0.0

    a_req = required_decel(v_ego, d_rel, v_lead)
    closing = v_lead < v_ego - 0.5
    stopping_lead = v_lead < SLOW_LEAD_SPEED or (v_lead < MAX_LEAD_SPEED and a_lead < SLOWING_LEAD_ACCEL)

    if not self.active:
      if (v_ego > MIN_EGO_SPEED and closing and stopping_lead and
          START_DECEL <= a_req <= FALLBACK_DECEL and d_rel > STOP_GAP):
        self.active = True
        self.decel = max(0.0, -a_now)
    elif a_req < RELEASE_DECEL or v_lead > v_ego + 0.5:
      self.reset()

    if not self.active:
      return None
    # Late detection or a cut-in: the MPC handles it; resume from its plan.
    if a_req > FALLBACK_DECEL or d_rel <= STOP_GAP:
      self.decel = max(0.0, -a_now)
      self.target = 0.0
      return None

    target = min(MAX_DECEL, a_req)
    if v_ego < V_STOPPING:
      target = max(target, STOPPING_DECEL)
    return self._ramp(target, dt)

  @staticmethod
  def trajectory(v0, decel_now, target, t_idxs):
    """Speeds/accels/jerks that ramp from -decel_now to -target at JERK and hold it to a stop."""
    n = len(t_idxs)
    a = np.zeros(n)
    v = np.zeros(n)
    v[0] = max(0.0, v0)
    a[0] = -decel_now
    for k in range(1, n):
      dt = t_idxs[k] - t_idxs[k - 1]
      prev = a[k - 1]
      a_k = prev + max(-JERK * dt, min(JERK * dt, -target - prev))
      v_k = v[k - 1] + 0.5 * (prev + a_k) * dt
      if v_k <= 1e-3:
        v_k, a_k = 0.0, 0.0
      v[k] = v_k
      a[k] = a_k
    if v[0] <= 0.0:
      a[0] = 0.0
    j = np.diff(a) / np.maximum(np.diff(t_idxs), 1e-3)
    return v, a, j
