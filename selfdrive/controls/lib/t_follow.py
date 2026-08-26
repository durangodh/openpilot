from common.numpy_fast import clip, interp


CRUISE_GAP_BP = [1.0, 2.0, 3.0, 4.0]
CRUISE_GAP_V = [1.1, 1.2, 1.4, 1.6]

T_FOLLOW_MIN = 0.6
T_FOLLOW_DECEL_HOLD_ACCEL = -0.2
T_FOLLOW_INCREASE_RATE = 0.1
T_FOLLOW_DT = 0.05


def get_t_follow_base(cruise_gap, gap_values, v_ego_kph, speed_ratio, safe_mode_factor):
  """Return the configured, speed-scaled following time before transient adjustments."""
  gap = float(clip(cruise_gap, CRUISE_GAP_BP[0], CRUISE_GAP_BP[-1]))
  tr = interp(gap, CRUISE_GAP_BP, gap_values)
  speed_scale = interp(v_ego_kph, [0.0, 100.0], [1.0, max(1.0, speed_ratio)])
  safe_scale = 2.0 - float(clip(safe_mode_factor, 0.5, 1.0))
  return max(T_FOLLOW_MIN, float(tr * speed_scale * safe_scale))


def hold_t_follow_while_decelerating(tf_target, tf_previous, a_ego):
  """Do not shrink the base gap while braking, but always permit a safer increase."""
  if tf_previous > 0.0 and a_ego <= T_FOLLOW_DECEL_HOLD_ACCEL and tf_target < tf_previous:
    return float(tf_previous)
  return float(tf_target)


def get_t_follow_decel_margin(a_ego, decel_boost, lead_status):
  """Add a bounded braking margin only while a real lead is being followed."""
  if not lead_status:
    return 0.0
  margin = interp(a_ego, [-2.5, -1.0, T_FOLLOW_DECEL_HOLD_ACCEL, 0.0],
                  [0.25, 0.12, 0.02, 0.0])
  return float(margin * clip(decel_boost, 0.0, 1.0))


def limit_t_follow_increase(tf_target, tf_previous, dt=T_FOLLOW_DT):
  """Ramp increases to avoid an abrupt virtual-obstacle movement; allow decreases immediately."""
  if tf_previous > 0.0 and tf_target > tf_previous:
    return float(min(tf_target, tf_previous + T_FOLLOW_INCREASE_RATE * dt))
  return float(tf_target)


def desired_follow_distance(v_ego, v_lead, t_follow, stop_dist, comfort_brake):
  """Return the bumper-to-bumper target using one braking model for ego and lead."""
  ego_stopping_distance = (v_ego ** 2) / (2 * comfort_brake)
  lead_stopping_distance = (v_lead ** 2) / (2 * comfort_brake)
  return ego_stopping_distance + t_follow * v_ego + stop_dist - lead_stopping_distance
