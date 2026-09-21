"""Bounded comfort policies around the existing C2 longitudinal planner.

These policies do not change perceived obstacles, braking limits, or the
configured time gap. Invalid/closing/stopping scenes keep the original costs.
"""
import math

from common.numpy_fast import clip, interp


def get_follow_obstacle_cost(base_cost, v_ego, a_ego, planned_accel, leads,
                             t_follow, stop_distance, comfort_brake):
  """Ease gap recovery at road speed only while every lead has spare distance."""
  if (v_ego <= 30.0 / 3.6 or a_ego < 0.0 or planned_accel < 0.0 or
      not all(math.isfinite(float(x)) for x in
              (v_ego, a_ego, planned_accel, t_follow, stop_distance, comfort_brake)) or
      comfort_brake <= 0.0):
    return base_cost

  comfort = 1.0
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
  # Respect already-soft user settings. Default 6.0 becomes 4.8 at 60 km/h
  # and 3.9 at 100 km/h; no change to the danger-zone constraint penalty.
  return max(min(base_cost, 3.0), base_cost * (1.0 - reduction * comfort))
