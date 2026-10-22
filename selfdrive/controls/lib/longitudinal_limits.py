import math

from common.conversions import Conversions as CV
from common.numpy_fast import clip, interp


MAX_SET_SPEED_KPH = 145
AUTO_SPEED_UP_RATE_KPH_S = 5.0

# aPilot C2 CruiseMax table. This module is intentionally dependency-light so
# planner, controlsd policy and unit tests all share the exact same mapping.
CRUISE_MAX_ACCEL_BP = [0.0, 20.0 * CV.KPH_TO_MS, 40.0 * CV.KPH_TO_MS, 60.0 * CV.KPH_TO_MS,
                       80.0 * CV.KPH_TO_MS, 110.0 * CV.KPH_TO_MS,
                       140.0 * CV.KPH_TO_MS]
CRUISE_MAX_VAL_KEYS = ["CruiseMaxVals1", "CruiseMaxVals20", "CruiseMaxVals2", "CruiseMaxVals3",
                       "CruiseMaxVals4", "CruiseMaxVals5", "CruiseMaxVals6"]
# Keep the emergency fallback identical to manager.py and apilot.json. These
# values are used only when the persisted table is unavailable or malformed.
CRUISE_MAX_VAL_DEFAULTS = [1.60, 1.00, 1.20, 1.00, 0.80, 0.70, 0.60]
CRUISE_ACCEL_FACTOR_DEFAULT = 1.0

# aPilot C2 total-acceleration envelope. Longitudinal acceleration is reduced
# when the estimated lateral acceleration consumes the available tire force.
TURN_ACCEL_MAX_BP = [20.0, 40.0]
TURN_ACCEL_MAX_V = [2.5, 3.2]


A_CRUISE_MIN = -1.2
# Camera/section/curve limiters may plan a stronger decel than A_CRUISE_MIN.
# Widen the cruise bound to that rate (plus a small tracking margin) so the car
# reaches the target speed where the limiter expects it.
A_CRUISE_MIN_LIMITER = -2.5
CRUISE_DECEL_MARGIN = 0.2


def get_cruise_min_accel(decel_limit):
  if decel_limit <= 0.0:
    return A_CRUISE_MIN
  return float(clip(-(decel_limit + CRUISE_DECEL_MARGIN), A_CRUISE_MIN_LIMITER, A_CRUISE_MIN))


def limit_accel_in_turns(v_ego, steering_angle_deg, accel_limits, steer_ratio, wheelbase):
  """Apply the aPilot C2 steering-angle longitudinal acceleration limit."""
  if steer_ratio <= 0.0 or wheelbase <= 0.0:
    return [float(accel_limits[0]), float(accel_limits[1])]

  total_accel_max = interp(v_ego, TURN_ACCEL_MAX_BP, TURN_ACCEL_MAX_V)
  lateral_accel = (v_ego ** 2 * steering_angle_deg * CV.DEG_TO_RAD /
                   (steer_ratio * wheelbase))
  longitudinal_accel_max = math.sqrt(max(total_accel_max ** 2 - lateral_accel ** 2, 0.0))
  return [float(accel_limits[0]), float(min(accel_limits[1], longitudinal_accel_max))]


def get_cruise_max_accel(v_ego, cruise_max_vals, driving_mode,
                         eco_mode_factor=1.0, safe_mode_factor=1.0):
  """Return the shared CruiseMax upper bound for planner and final control."""
  values = cruise_max_vals if len(cruise_max_vals) == len(CRUISE_MAX_ACCEL_BP) \
    else CRUISE_MAX_VAL_DEFAULTS
  mode = int(clip(driving_mode, 1, 4))
  if mode == 1:  # SAFE = ECO multiplied by the SAFE factor
    mode_factor = eco_mode_factor * safe_mode_factor
  elif mode == 2:  # ECO
    mode_factor = eco_mode_factor
  else:  # NORMAL / FAST
    mode_factor = 1.0
  return float(max(0.0, interp(v_ego, CRUISE_MAX_ACCEL_BP, values) * mode_factor))


def scale_cruise_max_accel(cruise_max_accel, accel_factor=CRUISE_ACCEL_FACTOR_DEFAULT):
  """Positive-acceleration cap: CruiseMax x the LEAD / NO-LEAD CRUISE ACCEL ratio.

  100% keeps CruiseMax unchanged; the same ratio applies over the whole speed
  range (95% = 5% lower, 105% = 5% higher). Braking is never affected.
  """
  return float(max(0.0, cruise_max_accel * clip(accel_factor, 0.50, 1.50)))


def cap_pid_accel_limits(accel_limits, v_ego, cruise_max_vals, driving_mode,
                         eco_mode_factor=1.0, safe_mode_factor=1.0,
                         accel_factor=CRUISE_ACCEL_FACTOR_DEFAULT):
  """Apply the user-visible cruise acceleration limit to the final PID output.

  The MPC already uses the CruiseMax table, but the speed-error PID previously
  retained the Hyundai controller's 2.5 m/s^2 upper bound. A sufficiently large
  speed error could therefore command more acceleration than the configured
  table. Preserve the vehicle braking limit and cap only positive acceleration.
  """
  cruise_max = get_cruise_max_accel(v_ego, cruise_max_vals, driving_mode,
                                    eco_mode_factor, safe_mode_factor)
  cruise_max = scale_cruise_max_accel(cruise_max, accel_factor)
  return [float(accel_limits[0]), float(min(accel_limits[1], cruise_max))]


def select_auto_driving_mode(initial_mode, current_mode, driving_index):
  """Map AUTO to SAFE/NORMAL while preserving manually selected ECO/FAST."""
  if initial_mode != 5 or driving_index <= 0.0 or current_mode in (2, 4):
    return current_mode
  if driving_index < 20.0:
    return 3
  if driving_index > 80.0:
    return 1
  return current_mode


def get_auto_speed_up_target(set_speed_kph, road_limit_kph, dt=0.01):
  """Rate-limit automatic set-speed increases and enforce the global maximum."""
  bounded_limit = float(clip(road_limit_kph, 0.0, MAX_SET_SPEED_KPH))
  return min(set_speed_kph + AUTO_SPEED_UP_RATE_KPH_S * dt,
             bounded_limit, float(MAX_SET_SPEED_KPH))


# Cut-in gap relief. A car that cuts in closer than the following distance but is not
# closing on us (similar or faster) does not need a hard brake to restore the gap.
# The missing distance is lent back to the MPC as an obstacle offset that is returned
# at CUT_IN_RELIEF_DECAY m/s, so the gap reopens gradually and acceleration resumes as
# soon as the lead pulls away. 2026-10-20 long 857 s: 13 m cut-in at 48 km/h, -2.4 brake,
# then still -1.4 while the lead pulled away at +1.4 m/s.
CUT_IN_DREL_JUMP = 5.0       # m, lead distance drop in one plan step that marks a cut-in
CUT_IN_WINDOW = 3.0          # s, time after the cut-in in which relief may be armed
CUT_IN_MIN_VREL = -1.0       # m/s, lead at most this much slower than us
CUT_IN_MIN_ALEAD = -2.0      # m/s^2, no relief while the lead is braking harder
CUT_IN_MIN_GAP_T = 0.6       # s, never relieve below this time gap (+ CUT_IN_MIN_GAP_D)
CUT_IN_MIN_GAP_D = 3.0       # m
CUT_IN_RELIEF_DECAY = 1.0    # m/s


class CutInGapRelief:
  def __init__(self):
    self.reset()

  def reset(self):
    self.relief = 0.0
    self.since_cut_in = CUT_IN_WINDOW + 1.0
    self.prev_d_rel = None

  def update(self, enabled, lead_status, d_rel, v_rel, a_lead, v_ego, deficit, dt):
    """Return the obstacle offset (m) to add to the lead obstacle this plan step."""
    if not enabled or not lead_status:
      self.reset()
      return 0.0

    if self.prev_d_rel is None or self.prev_d_rel - d_rel > CUT_IN_DREL_JUMP:
      self.since_cut_in = 0.0
      self.relief = 0.0
    else:
      self.since_cut_in += dt
    self.prev_d_rel = d_rel

    safe = (v_rel > CUT_IN_MIN_VREL and a_lead > CUT_IN_MIN_ALEAD and
            d_rel > v_ego * CUT_IN_MIN_GAP_T + CUT_IN_MIN_GAP_D)
    if not safe:
      # Once a cut-in turned unsafe, wait for the next cut-in before lending distance again.
      if self.relief > 0.0:
        self.since_cut_in = CUT_IN_WINDOW + 1.0
      self.relief = 0.0
      return 0.0

    deficit = max(0.0, float(deficit))
    if self.relief <= 0.0 and self.since_cut_in <= CUT_IN_WINDOW:
      self.relief = deficit
    else:
      self.relief = max(0.0, self.relief - CUT_IN_RELIEF_DECAY * dt)
    self.relief = min(self.relief, deficit)
    return self.relief


# Weak camera lead. A queue or stopped car is often seen by the model well before its
# probability reaches the 0.5 that radard needs, and SCC radar ignores it until ~30-60 m.
# Meanwhile the plan kept accelerating and then had to brake hard (2026-10-22 11:44 log:
# 363 s 61 km/h -> -3.0, 1434 s -> -1.7, 1756 s -> -2.0; the model had the car at
# prob 0.3-0.46 for 1-1.6 s before). While such a lead is seen, stop accelerating and
# ease off at WEAK_LEAD_ACCEL; the planner's per-step accel clip turns that into a ramp.
# The 39 min log triggered exactly those three spots and nowhere else.
WEAK_LEAD_MIN_PROB = 0.3
WEAK_LEAD_HOLD_S = 0.5
WEAK_LEAD_MAX_D = 120.0       # m
WEAK_LEAD_MIN_CLOSING = 20.0 * CV.KPH_TO_MS
WEAK_LEAD_MIN_V_EGO = 5.0     # m/s
WEAK_LEAD_CONFIRMED_GAP = 5.0  # m, a confirmed lead this near the camera lead already handles it
WEAK_LEAD_ACCEL = -0.5        # m/s^2
WEAK_LEAD_RELEASE_S = 1.0     # keep easing off through brief camera dropouts


class WeakSlowLead:
  def __init__(self):
    self.held = 0.0
    self.release = 0.0

  def update(self, enabled, v_ego, prob, d_vis, v_vis, lead_status, d_lead, dt):
    """Return True while a likely slow lead is seen only weakly by the camera."""
    confirmed = lead_status and d_lead < d_vis + WEAK_LEAD_CONFIRMED_GAP
    seen = (enabled and v_ego > WEAK_LEAD_MIN_V_EGO and prob >= WEAK_LEAD_MIN_PROB and
            0.0 < d_vis < WEAK_LEAD_MAX_D and v_ego - v_vis >= WEAK_LEAD_MIN_CLOSING and
            not confirmed)
    # One missed model frame (prob or distance flicker) must not restart the wait.
    self.held = self.held + dt if seen else max(0.0, self.held - 2.0 * dt)
    if seen and self.held >= WEAK_LEAD_HOLD_S - 1e-6:
      self.release = WEAK_LEAD_RELEASE_S
    else:
      self.release = max(0.0, self.release - dt)
    if not enabled or v_ego <= WEAK_LEAD_MIN_V_EGO:
      self.held = 0.0
      self.release = 0.0
    # While the lead is confirmed the MPC follows it itself. Keep the release timer so a
    # lead that flickers back to unconfirmed is eased off again without a new wait.
    return self.release > 0.0 and not confirmed
