"""Bounded comfort policies around the existing C2 longitudinal planner.

These policies do not change perceived obstacles, braking limits, or the
configured time gap. Invalid/closing/stopping scenes keep the original costs.
"""
import math

from common.numpy_fast import interp

FOLLOW_COMFORT_FULL_ACCEL = 0.2  # m/s^2, fade out comfort before either acceleration reaches zero


def get_closing_lead_accel_limit(max_accel, v_ego, leads, desired_gap):
  """Lift positive acceleration as a measured closing lead consumes spare gap.

  Unlike the comfort policy, stopped/braking/near leads must not bypass this
  cap. It only removes throttle; the planner still owns all braking requests.
  """
  if (not all(math.isfinite(x) for x in (max_accel, v_ego, desired_gap)) or
      max_accel <= 0.0 or v_ego < 5.0 or desired_gap <= 0.0):
    return max_accel
  cap = max_accel
  for lead in leads:
    if not lead.status:
      continue
    distance, speed = float(lead.dRel), float(lead.vLead)
    if not all(math.isfinite(x) for x in (distance, speed)) or distance <= 0.0:
      continue
    closing = v_ego - speed
    if closing <= 0.35:
      continue
    time_to_gap = max(0.0, distance - desired_gap) / closing
    allowance = interp(time_to_gap, [4.0, 8.0], [0.0, 1.0])
    weight = interp(closing, [0.35, 0.75], [0.0, 1.0])
    cap = min(cap, max_accel * (1.0 - weight * (1.0 - allowance)))
  return cap


def get_follow_obstacle_cost(base_cost, v_ego, a_ego, planned_accel, leads,
                             t_follow, stop_distance, comfort_brake):
  """Ease gap recovery at road speed only while every lead has spare distance."""
  if (v_ego <= 30.0 / 3.6 or a_ego < 0.0 or planned_accel < 0.0 or
      not all(math.isfinite(float(x)) for x in
              (v_ego, a_ego, planned_accel, t_follow, stop_distance, comfort_brake)) or
      comfort_brake <= 0.0):
    return base_cost

  # Restore the normal distance weight continuously as actual OR planned
  # acceleration approaches coasting. A hard sign gate used to switch the
  # weight by up to 35% around zero. This instantaneous blend adds no filter
  # delay: braking/closing/short-gap guards still restore the base cost.
  accel_weight = interp(min(a_ego, planned_accel), [0.0, FOLLOW_COMFORT_FULL_ACCEL], [0.0, 1.0])
  comfort = accel_weight * accel_weight * (3.0 - 2.0 * accel_weight)
  has_lead = False
  for lead in leads:
    if not lead.status:
      continue
    has_lead = True
    distance, speed, accel = float(lead.dRel), float(lead.vLead), float(lead.aLeadK)
    if not all(math.isfinite(x) for x in (distance, speed, accel)):
      return base_cost
    desired_gap = max(0.0, (v_ego ** 2 - speed ** 2) / (2.0 * comfort_brake) +
                      t_follow * v_ego + stop_distance)
    # Stop/slow leads, braking leads, a deficit, or >0.5 m/s closing all restore
    # the original weight immediately. The second lead can veto comfort too.
    comfort = min(comfort,
                  interp(speed, [3.0, 5.0], [0.0, 1.0]),
                  interp(accel, [-0.2, 0.0], [0.0, 1.0]),
                  interp(v_ego - speed, [0.0, 0.5], [1.0, 0.0]),
                  interp(distance - desired_gap, [0.0, 3.0], [0.0, 1.0]))
  if not has_lead:
    return base_cost

  reduction = interp(v_ego * 3.6, [30.0, 60.0, 100.0], [0.0, 0.20, 0.35])
  # Respect already-soft user settings. At full comfort, default 6.0 becomes
  # 4.8 at 60 km/h and 3.9 at 100 km/h; the danger penalty stays unchanged.
  return max(min(base_cost, 3.0), base_cost * (1.0 - reduction * comfort))


def get_follow_approach_limit(max_accel, v_ego, leads, desired_gap):
  """Lift positive throttle before a distant lead consumes the spare gap.

  Return (positive allowance, comfortable scene). The latter permits gradual
  throttle lift while the existing hazard guards keep their immediate response.
  Never filter distance/speed measurements or weaken a braking request.
  desired_gap is the planner's current primary-lead gap with user settings.
  """
  if (not math.isfinite(desired_gap) or desired_gap <= 0.0 or
      not math.isfinite(v_ego) or v_ego < 30.0 / 3.6 or not leads[0].status):
    return max_accel, False

  primary = leads[0]
  for lead in leads:
    if not lead.status:
      continue
    distance, speed, accel = float(lead.dRel), float(lead.vLead), float(lead.aLeadK)
    if not all(math.isfinite(x) for x in (distance, speed, accel)):
      return max_accel, False
    closing = max(0.0, v_ego - speed)
    # The primary desired gap is used conservatively to qualify a secondary
    # lead too. A stopped/braking/closer second obstacle cancels comfort.
    if (speed <= 3.0 or accel <= -0.35 or closing >= 2.5 or
        distance <= max(45.0, desired_gap + 6.0) or
        (closing > 0.0 and distance / closing <= 8.0)):
      return max_accel, False

  closing = max(0.0, v_ego - float(primary.vLead))
  if closing <= 0.35:
    return max_accel, True
  time_to_gap = (float(primary.dRel) - desired_gap) / closing
  allowance = interp(time_to_gap, [4.0, 8.0], [0.0, 1.0])
  # Blend entry at road speed and at very small relative speeds.
  weight = (interp(v_ego * 3.6, [30.0, 40.0], [0.0, 1.0]) *
            interp(closing, [0.35, 0.75], [0.0, 1.0]))
  return max_accel * (1.0 - weight * (1.0 - allowance)), True


# ---- New-lead confirmation (cut-ins, adjacent-lane flicker) ----
NEW_LEAD_CONFIRM_S = 0.25          # a far, non-closing new lead must persist this long
NEW_LEAD_URGENT_TTC_S = 4.0        # closing faster than this is used at once
NEW_LEAD_URGENT_HEADWAY_S = 1.5    # closer than this headway is used at once
NEW_LEAD_URGENT_MIN_DIST = 25.0    # ... and always closer than this distance
LEAD_SWITCH_JUMP = 5.0             # m, dRel jump that means a different vehicle


class NoLead(object):
  status = False
  dRel = 0.0
  vLead = 0.0
  vLeadK = 0.0
  aLeadK = 0.0
  aLeadTau = 1.5
  modelProb = 0.0
  radar = False


NO_LEAD = NoLead()


class LeadConfirm(object):
  """Delay only far, non-closing new leads by NEW_LEAD_CONFIRM_S.

  radard publishes a lead from the first frame its probability passes 0.5,
  so a car in the next lane on a curve, or a cut-in that is already pulling
  away, was planned against immediately and produced a brake stab. Anything
  close or closing (TTC/headway/distance) is used at once, so a real
  hazard is never delayed. Only the MPC sees the delay.
  """

  def __init__(self):
    self.reset()

  def reset(self):
    self.seen_s = 0.0
    self.prev_d = None

  def update(self, lead, v_ego, dt):
    if lead is None or not lead.status:
      self.reset()
      return False
    d = float(lead.dRel)
    if not math.isfinite(d):
      self.reset()
      return True
    if self.prev_d is None or abs(d - self.prev_d) > max(LEAD_SWITCH_JUMP, 0.15 * self.prev_d):
      self.seen_s = 0.0
    self.prev_d = d
    self.seen_s += dt
    closing = max(0.0, float(v_ego) - float(lead.vLead))
    urgent = (d < max(NEW_LEAD_URGENT_MIN_DIST, NEW_LEAD_URGENT_HEADWAY_S * float(v_ego)) or
              (closing > 0.1 and d / closing < NEW_LEAD_URGENT_TTC_S))
    return urgent or self.seen_s >= NEW_LEAD_CONFIRM_S


# ---- Faster cut-in relief ----
CUT_IN_MIN_HEADWAY_S = 0.4         # never relax closer than this headway


def faster_lead_relief(d_rel, v_ego, v_lead, a_lead, desired_gap, obstacle_now, stop_distance):
  """Metres to add to a faster lead's obstacle so the MPC coasts, not brakes.

  A car that cuts in ahead but is faster than ego and not braking leaves the
  gap on its own. The MPC still saw a gap deficit and braked briefly. The
  relief removes only the current deficit, fades in with the lead's
  relative speed and out as it brakes or gets too close, so a slowing or
  braking cut-in gets the normal response.
  """
  values = (d_rel, v_ego, v_lead, a_lead, desired_gap, obstacle_now, stop_distance)
  if not all(math.isfinite(float(x)) for x in values):
    return 0.0
  deficit = float(desired_gap) - float(obstacle_now)
  if deficit <= 0.0:
    return 0.0
  min_gap = max(float(stop_distance), CUT_IN_MIN_HEADWAY_S * float(v_ego))
  weight = (interp(float(v_lead) - float(v_ego), [0.3, 1.5], [0.0, 1.0]) *
            interp(float(a_lead), [-0.5, -0.1], [0.0, 1.0]) *
            interp(float(d_rel), [min_gap, min_gap + 5.0], [0.0, 1.0]))
  return float(weight * deficit)
