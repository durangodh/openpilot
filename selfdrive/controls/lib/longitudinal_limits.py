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
NO_LEAD_CRUISE_ACCEL_FACTOR_DEFAULT = 0.65

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


def get_no_lead_cruise_accel_cap(cruise_max_accel, speed_error_kph,
                                  accel_factor=NO_LEAD_CRUISE_ACCEL_FACTOR_DEFAULT):
  """Return a gentler positive-acceleration cap when no lead is present.

  A large set-speed error may use the configured fraction of CruiseMax, while
  the allowance tapers further near the set speed. Braking is handled outside
  this helper and is never weakened by the no-lead policy.
  """
  error_scale = interp(clip(speed_error_kph, 0.0, 30.0),
                       [0.0, 5.0, 15.0, 30.0], [0.20, 0.40, 0.70, 1.0])
  factor = clip(accel_factor, 0.30, 1.0)
  return float(max(0.0, cruise_max_accel * factor * error_scale))


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
