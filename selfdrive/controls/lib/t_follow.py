from common.numpy_fast import clip, interp


CRUISE_GAP_BP = [1.0, 2.0, 3.0, 4.0]
CRUISE_GAP_V = [1.1, 1.2, 1.4, 1.6]

T_FOLLOW_MIN = 0.6
T_FOLLOW_DECEL_HOLD_ENTER_ACCEL = -0.3
T_FOLLOW_DECEL_HOLD_EXIT_ACCEL = -0.1
T_FOLLOW_ACCEL_FILTER_ALPHA = 0.2
T_FOLLOW_INCREASE_RATE = 0.1
T_FOLLOW_DECREASE_RATE = 0.3
T_FOLLOW_DECEL_RELEASE_RATE = 0.08
T_FOLLOW_DT = 0.05

STOPPED_LEAD_MAX_SPEED = 3.0
STOPPED_LEAD_MIN_EGO_SPEED = 10.0
STOPPED_LEAD_MIN_CLOSING_SPEED = 5.0

STATIONARY_APPROACH_MIN_EGO_SPEED = 2.0
STATIONARY_APPROACH_MIN_DECEL = 0.25
STATIONARY_APPROACH_MAX_DECEL = 2.5
STATIONARY_APPROACH_CONFIRM_FRAMES = 3
STATIONARY_APPROACH_DROPOUT_FRAMES = 6
STATIONARY_APPROACH_BRAKE_JERK = 0.6
STATIONARY_APPROACH_RELEASE_JERK = 0.4


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
  """Taper a braking gap back to its target instead of holding then releasing it."""
  if tf_previous > 0.0 and hold_active and tf_target < tf_previous:
    # Avoid a held brake demand followed by an abrupt coast transition.
    return float(max(tf_target, tf_previous - T_FOLLOW_DECEL_RELEASE_RATE * T_FOLLOW_DT))
  return float(tf_target)


def get_t_follow_decel_margin(a_ego, decel_boost, lead_status):
  """Add a bounded braking margin only while a real lead is being followed."""
  if not lead_status:
    return 0.0
  margin = interp(a_ego, [-2.5, -1.0, -0.2, 0.0],
                  [0.25, 0.12, 0.02, 0.0])
  return float(margin * clip(decel_boost, 0.0, 1.0))


def get_t_follow_closing_margin(v_ego, v_lead, lead_status):
  """Start easing off earlier when a confirmed lead is closing quickly."""
  if not lead_status:
    return 0.0
  closing_speed = max(0.0, float(v_ego - v_lead))
  return float(interp(closing_speed, [0.0, 1.5, 4.0, 8.0],
                      [0.0, 0.03, 0.10, 0.18]))


def get_stopped_lead_comfort_brake(configured_comfort_brake, v_ego, v_lead, lead_status):
  """Use an earlier braking envelope for a confirmed slow lead at road speed.

  This does not create a lead or lower perception thresholds. It only prevents
  a high ComfortBrake setting from postponing braking after radar/vision has
  confirmed a nearly stationary vehicle with a large closing speed.
  """
  base = float(clip(configured_comfort_brake, 1.0, 4.0))
  if not lead_status or v_ego < STOPPED_LEAD_MIN_EGO_SPEED or v_lead > STOPPED_LEAD_MAX_SPEED:
    return base

  closing_speed = max(0.0, float(v_ego - v_lead))
  if closing_speed < STOPPED_LEAD_MIN_CLOSING_SPEED:
    return base

  # At 70 km/h against a stopped lead this caps the planning assumption near
  # 1.5 m/s^2, moving the comfort-braking envelope roughly 30 m earlier than
  # the default 2.5 m/s^2 setting. The physical acceleration limit is unchanged.
  safety_cap = interp(closing_speed, [5.0, 10.0, 15.0, 20.0],
                      [4.0, 2.2, 1.7, 1.5])
  return float(min(base, safety_cap))


def get_stationary_lead_target_accel(v_ego, d_rel, stop_distance):
  """Return the constant deceleration needed to stop at the configured gap."""
  if v_ego < STATIONARY_APPROACH_MIN_EGO_SPEED:
    return 0.0

  remaining_distance = max(0.1, float(d_rel - stop_distance))
  required_decel = (float(v_ego) ** 2) / (2.0 * remaining_distance)
  if required_decel < STATIONARY_APPROACH_MIN_DECEL:
    return 0.0
  return -float(clip(required_decel, STATIONARY_APPROACH_MIN_DECEL,
                     STATIONARY_APPROACH_MAX_DECEL))


class StationaryLeadApproach:
  """Keep a stable distance-based deceleration target for a stopped lead."""

  def __init__(self, dt=T_FOLLOW_DT):
    self.dt = dt
    self.reset()

  def reset(self):
    self.confirm_frames = 0
    self.dropout_frames = 0
    self.lead_latched = False
    self.target_accel = 0.0

  def update(self, v_ego, d_rel, v_lead, lead_status, stop_distance, enabled=True):
    if not enabled:
      self.reset()
      return self.target_accel

    raw_target = get_stationary_lead_target_accel(v_ego, d_rel, stop_distance)
    stationary_lead = bool(lead_status and v_lead <= STOPPED_LEAD_MAX_SPEED and raw_target < 0.0)

    if stationary_lead:
      self.confirm_frames = min(STATIONARY_APPROACH_CONFIRM_FRAMES, self.confirm_frames + 1)
      self.dropout_frames = 0
      if self.confirm_frames >= STATIONARY_APPROACH_CONFIRM_FRAMES:
        self.lead_latched = True
    else:
      self.confirm_frames = 0
      if self.lead_latched:
        self.dropout_frames += 1
        if self.dropout_frames > STATIONARY_APPROACH_DROPOUT_FRAMES:
          self.lead_latched = False

    if self.lead_latched and stationary_lead:
      desired_accel = raw_target
    elif self.lead_latched:
      # Hold through a short radar/vision dropout instead of pulsing the brake.
      desired_accel = self.target_accel
    else:
      desired_accel = 0.0

    jerk_limit = (STATIONARY_APPROACH_BRAKE_JERK
                  if desired_accel < self.target_accel
                  else STATIONARY_APPROACH_RELEASE_JERK)
    max_step = jerk_limit * self.dt
    self.target_accel += float(clip(desired_accel - self.target_accel, -max_step, max_step))
    if not self.lead_latched and abs(self.target_accel) < max_step:
      self.target_accel = 0.0
    return self.target_accel


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
