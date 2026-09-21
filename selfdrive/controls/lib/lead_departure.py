"""Bounded stop/start handoff; never replace a planner braking request."""
from math import isfinite


DEPARTURE_WINDOW = 1.0
DEPARTURE_MAX_SPEED = 1.5
DEPARTURE_MIN_ACCEL = 0.15


class LeadDepartureAssist:
  def __init__(self, dt):
    self.dt = dt
    self.remaining = 0.0
    self.active = False
    self.accel_floor = 0.0

  def reset(self):
    self.remaining = 0.0
    self.active = False
    self.accel_floor = 0.0

  def update(self, *, enabled, stopping, confirmed, cs, plan, radar,
             radar_valid, plan_valid, plan_age, a_now, a_target,
             v_target, v_future, soft_hold):
    # This is only an adapter for a positive, fresh MPC departure request.
    # No stored acceleration survives a stop/brake request or a sensor failure.
    values = (cs.vEgo, plan_age, a_now, a_target, v_target, v_future)
    safe = (enabled and plan_valid and all(isfinite(x) for x in values) and
            0.0 <= plan_age <= 0.2 and 0.0 <= cs.vEgo < DEPARTURE_MAX_SPEED and
            not cs.brakePressed and not cs.gasPressed and not soft_hold and
            not getattr(plan, 'onStop', True) and not getattr(plan, 'fcw', True) and
            int(getattr(plan, 'trafficState', 1)) % 100 != 1 and
            a_now >= 0.0 and a_target > DEPARTURE_MIN_ACCEL and
            v_target >= 0.0 and v_future > max(v_target, cs.vEgo) + 0.01 and
            radar_valid and radar is not None and not radar.radarErrors)
    lead = radar.leadOne if safe else None
    if safe:
      desired_gap = float(getattr(plan, 'desiredDistance', float('nan')))
      min_gap = max(3.5, desired_gap - 0.5)
      safe = (isfinite(desired_gap) and desired_gap > 0.0 and
              self._moving_lead(lead, min_gap))
      # A closer second obstacle must agree with departure too.
      second = getattr(radar, 'leadTwo', None)
      if second is not None and second.status:
        safe = safe and isfinite(second.dRel) and (
          second.dRel > lead.dRel + 2.0 or self._moving_lead(second, min_gap))
    if not safe:
      self.reset()
      return False

    if stopping:
      # Arm once per actual stop, using LongControl's two fresh radar samples.
      if not confirmed or cs.vEgo > 0.3:
        self.reset()
        return False
      self.remaining = DEPARTURE_WINDOW
    else:
      self.remaining = max(0.0, self.remaining - self.dt)

    self.active = self.remaining > 0.0
    # Unlike StarPilot's planner override, never exceed the acceleration that
    # this fork's MPC already permits (including mode and cornering limits).
    self.accel_floor = min(a_target, 0.35 if lead.vLeadK >= 0.6 else 0.18) if self.active else 0.0
    return self.active

  @staticmethod
  def _moving_lead(lead, min_gap):
    if not lead.status or not getattr(lead, 'radar', False):
      return False
    values = (lead.dRel, lead.vLeadK, lead.vRel, lead.aLeadK)
    return (all(isfinite(x) for x in values) and min_gap <= lead.dRel <= 20.0 and
            lead.vLeadK > 0.25 and lead.vRel > 0.1 and lead.aLeadK >= 0.0)


def departure_jerk_upper(normal_upper, configured_start, pid_upper, assisted):
  """Remove the SCC startup bottleneck only for a confirmed assisted launch."""
  if not assisted:
    return normal_upper
  # Retain a deliberately softer user PID setting and all existing hard caps.
  launch_upper = min(2.0, max(0.0, pid_upper), max(0.0, configured_start) * 2.0)
  return min(5.0, max(normal_upper, launch_upper))
