from common.numpy_fast import clip, interp


CRUISE_GAP_BP = [1.0, 2.0, 3.0, 4.0]
CRUISE_GAP_V = [1.1, 1.2, 1.4, 1.6]

# After a deceleration the held (larger) following time is released toward the
# current-speed value at this rate instead of in one step (s per second).
T_FOLLOW_RELEASE_RATE = 0.3

STOPPED_LEAD_MAX_SPEED = 3.0
STOPPED_LEAD_MIN_EGO_SPEED = 10.0
STOPPED_LEAD_MIN_CLOSING_SPEED = 5.0


def get_t_follow_closing_margin(v_ego, v_lead, lead_status, d_rel=None):
  """Ease off progressively using closing speed and time-to-close.

  The old margin only looked at relative speed. That can leave the MPC relaxed
  while the lead is still far away, then ask for a noticeably stronger brake
  once the normal following envelope is reached. A small TTC preview grows the
  desired gap earlier, encouraging coast/light decel before that point without
  changing the physical accel limits or the close-range safety constraint.
  """
  if not lead_status:
    return 0.0

  closing_speed = max(0.0, float(v_ego - v_lead))
  if closing_speed <= 0.0:
    return 0.0

  speed_margin = float(interp(closing_speed, [0.0, 1.5, 4.0, 8.0],
                              [0.0, 0.03, 0.10, 0.18]))

  preview_margin = 0.0
  if d_rel is not None and d_rel > 0.0:
    ttc = float(d_rel) / max(closing_speed, 0.1)
    # Begin a gentle preview around 8 s TTC, peak through the normal approach
    # window, then let the existing speed margin/safety envelope own close range.
    preview_margin = float(interp(ttc, [2.0, 3.5, 6.0, 8.0, 10.0],
                                  [0.0, 0.04, 0.06, 0.03, 0.0]))
    # Tiny relative-speed differences should not create long-range hunting.
    preview_margin *= float(interp(closing_speed, [0.5, 1.5, 3.0],
                                   [0.0, 0.5, 1.0]))

  return speed_margin + preview_margin


def get_stopped_lead_comfort_brake(configured_comfort_brake, v_ego, v_lead, lead_status):
  """Use an earlier braking envelope for a confirmed slow lead at road speed.

  This does not create a lead or lower perception thresholds. It only prevents
  a high ComfortBrake setting from postponing braking after radar/vision has
  confirmed a nearly stationary vehicle with a large closing speed.
  """
  base = float(clip(configured_comfort_brake, 1.0, 4.0))
  if not lead_status:
    return base

  closing_speed = max(0.0, float(v_ego - v_lead))

  # At 70 km/h against a stopped lead this caps the planning assumption near
  # 1.5 m/s^2, moving the comfort-braking envelope roughly 30 m earlier than
  # the default 2.5 m/s^2 setting. The physical acceleration limit is unchanged.
  safety_cap = interp(closing_speed, [5.0, 10.0, 15.0, 20.0],
                      [4.0, 2.2, 1.7, 1.5])
  # Fade the cap across detection thresholds. A hard switch at 3 m/s lead
  # speed could suddenly change the MPC obstacle when a lead slows down.
  weight = (interp(v_ego, [STOPPED_LEAD_MIN_EGO_SPEED - 2.0, STOPPED_LEAD_MIN_EGO_SPEED], [0.0, 1.0]) *
            interp(v_lead, [STOPPED_LEAD_MAX_SPEED, STOPPED_LEAD_MAX_SPEED + 2.0], [1.0, 0.0]) *
            interp(closing_speed, [STOPPED_LEAD_MIN_CLOSING_SPEED - 2.0, STOPPED_LEAD_MIN_CLOSING_SPEED], [0.0, 1.0]))
  return float(base - weight * max(0.0, base - safety_cap))


class StoppedLeadComfortBrake(object):
  """Hold the stopped-lead braking envelope for the whole approach.

  get_stopped_lead_comfort_brake() depends on the current ego speed and
  closing speed, so recomputing it every frame relaxed the cap as ego slowed
  (fully released between 36 and 29 km/h). Riding that moving envelope, the
  planned deceleration eased off mid-approach and then grew again near the
  end. Keep the lowest value seen while the same slow lead is ahead, so the
  envelope only gets earlier, never later, until the lead is lost or moves.
  """

  def __init__(self):
    self.latched = None

  def reset(self):
    self.latched = None

  def update(self, configured_comfort_brake, v_ego, v_lead, lead_status,
             d_rel=None, t_follow=0.0, stop_distance=0.0):
    base = float(clip(configured_comfort_brake, 1.0, 4.0))
    target = get_stopped_lead_comfort_brake(configured_comfort_brake, v_ego, v_lead, lead_status)
    if not lead_status or v_lead > STOPPED_LEAD_MAX_SPEED + 2.0:
      self.latched = None
      return target
    if target < base and d_rel is not None:
      # Never let the earlier envelope start behind the car. Applied at once
      # on detection, the lower value made the desired gap tens of metres
      # larger than the real distance and the MPC answered with a sudden
      # brake stab. Only lower ComfortBrake as far as the current distance
      # still fits; as the approach goes on the cap tightens toward target.
      room = float(d_rel) - t_follow * v_ego - stop_distance
      fit = v_ego * v_ego / (2.0 * room) if room > 0.0 else base
      target = max(target, min(base, fit))
    if target < base:
      self.latched = target if self.latched is None else min(self.latched, target)
    return target if self.latched is None else min(self.latched, target)


def release_t_follow(tf_target, tf_previous, dt):
  """Raise the following time at once; lower it at T_FOLLOW_RELEASE_RATE.

  A larger gap is a safety request and applies immediately. A smaller one
  (deceleration finished, gap button lowered) is eased in, so the MPC does
  not pull ego toward the lead in one step.
  """
  if tf_previous is not None and tf_target < tf_previous:
    return float(max(tf_target, tf_previous - T_FOLLOW_RELEASE_RATE * dt))
  return float(tf_target)


def clamp_desired_follow_distance(safe_obstacle_distance, stopped_equivalence):
  """Keep the published/UI target physical when a faster lead is pulling away."""
  return max(0.0, float(safe_obstacle_distance - stopped_equivalence))
