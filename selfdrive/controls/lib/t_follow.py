from common.numpy_fast import clip, interp


CRUISE_GAP_BP = [1.0, 2.0, 3.0, 4.0]
CRUISE_GAP_V = [1.1, 1.2, 1.4, 1.6]

T_FOLLOW_MIN = 0.6
T_FOLLOW_DECEL_HOLD_ENTER_ACCEL = -0.3
T_FOLLOW_DECEL_HOLD_EXIT_ACCEL = -0.1
T_FOLLOW_ACCEL_FILTER_ALPHA = 0.2
T_FOLLOW_INCREASE_RATE = 0.1
T_FOLLOW_DECREASE_RATE = 0.3
T_FOLLOW_DT = 0.05


def get_t_follow_base(cruise_gap, gap_values, v_ego_kph, speed_ratio, safe_mode_factor):
  """Return the configured, speed-scaled following time before transient adjustments."""
  gap = float(clip(cruise_gap, CRUISE_GAP_BP[0], CRUISE_GAP_BP[-1]))
  tr = interp(gap, CRUISE_GAP_BP, gap_values)
  speed_scale = interp(v_ego_kph, [0.0, 100.0], [1.0, max(1.0, speed_ratio)])
  safe_scale = 2.0 - float(clip(safe_mode_factor, 0.5, 1.0))
  return max(T_FOLLOW_MIN, float(tr * speed_scale * safe_scale))


def filter_t_follow_accel(a_ego, a_ego_filtered=None, alpha=T_FOLLOW_ACCEL_FILTER_ALPHA):
  """Low-pass ego acceleration so a single noisy sample cannot release the hold."""
  if a_ego_filtered is None:
    return float(a_ego)
  alpha = float(clip(alpha, 0.0, 1.0))
  return float(a_ego_filtered + alpha * (a_ego - a_ego_filtered))


def update_t_follow_decel_hold(hold_active, a_ego_filtered):
  """Apply hysteresis: enter on real braking and exit only near zero acceleration."""
  if hold_active:
    return bool(a_ego_filtered <= T_FOLLOW_DECEL_HOLD_EXIT_ACCEL)
  return bool(a_ego_filtered <= T_FOLLOW_DECEL_HOLD_ENTER_ACCEL)


def hold_t_follow_while_decelerating(tf_target, tf_previous, hold_active):
  """Do not shrink the base gap while braking, but always permit a safer increase."""
  if tf_previous > 0.0 and hold_active and tf_target < tf_previous:
    return float(tf_previous)
  return float(tf_target)


def get_t_follow_decel_margin(a_ego, decel_boost, lead_status):
  """Add a bounded braking margin only while a real lead is being followed."""
  if not lead_status:
    return 0.0
  margin = interp(a_ego, [-2.5, -1.0, -0.2, 0.0],
                  [0.25, 0.12, 0.02, 0.0])
  return float(margin * clip(decel_boost, 0.0, 1.0))


def limit_t_follow_change(tf_target, tf_previous, dt=T_FOLLOW_DT):
  """Rate-limit both directions, with a faster release than safety-gap increase."""
  if tf_previous > 0.0 and tf_target > tf_previous:
    return float(min(tf_target, tf_previous + T_FOLLOW_INCREASE_RATE * dt))
  if tf_previous > 0.0 and tf_target < tf_previous:
    return float(max(tf_target, tf_previous - T_FOLLOW_DECREASE_RATE * dt))
  return float(tf_target)


def clamp_desired_follow_distance(safe_obstacle_distance, stopped_equivalence):
  """Keep the published/UI target physical when a faster lead is pulling away."""
  return max(0.0, float(safe_obstacle_distance - stopped_equivalence))
