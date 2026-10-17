from cereal import car, log
from common.conversions import Conversions as CV
from common.numpy_fast import clip, interp
from common.realtime import DT_MDL
from selfdrive.modeld.constants import T_IDXS
from selfdrive.controls.lib import live_tune
from selfdrive.controls.lib.longitudinal_limits import MAX_SET_SPEED_KPH

# WARNING: this value was determined based on the model's training distribution,
#          model predictions above this speed can be unpredictable
# kph
V_CRUISE_MAX = MAX_SET_SPEED_KPH
V_CRUISE_MIN = 30
V_CRUISE_DELTA_MI = 5 * CV.MPH_TO_KPH
V_CRUISE_DELTA_KM = 10
V_CRUISE_ENABLE_MIN = 30
V_CRUISE_INITIAL = 255  # kph

MIN_SPEED = 1.0
CONTROL_N = 17

# EU guidelines
MAX_LATERAL_JERK = 10.0
MAX_VEL_ERR = 5.0

ButtonType = car.CarState.ButtonEvent.Type


def get_speed_error(modelV2: log.ModelDataV2, v_ego: float) -> float:
  # Match aPilot C2: align model temporal velocity with measured ego speed.
  if len(modelV2.temporalPose.trans):
    vel_err = clip(modelV2.temporalPose.trans[0] - v_ego, -MAX_VEL_ERR, MAX_VEL_ERR)
    return float(vel_err)
  return 0.0


def apply_deadzone(error, deadzone):
  if error > deadzone:
    error -= deadzone
  elif error < - deadzone:
    error += deadzone
  else:
    error = 0.
  return error


def initialize_v_cruise(v_ego, buttonEvents, v_cruise_last, v_cruise_min=V_CRUISE_ENABLE_MIN):
  for b in buttonEvents:
    # 250kph or above probably means we never had a set speed
    if b.type in (ButtonType.accelCruise, ButtonType.resumeCruise) and v_cruise_last < 250:
      return v_cruise_last

  return int(round(clip(v_ego * CV.MS_TO_KPH, v_cruise_min, V_CRUISE_MAX)))


def get_lag_adjusted_curvature(CP, v_ego, psis, curvatures, curvature_rates):
  if len(psis) != CONTROL_N:
    psis = [0.0]*CONTROL_N
    curvatures = [0.0]*CONTROL_N
    curvature_rates = [0.0]*CONTROL_N
  v_ego = max(MIN_SPEED, v_ego)
  
  # TODO this needs more thought, use .2s extra for now to estimate other delays
  delay = live_tune.steer_actuator_delay() + .2

  # MPC can plan to turn the wheel and turn back before t_delay. This means
  # in high delay cases some corrections never even get commanded. So just use
  # psi to calculate a simple linearization of desired curvature
  current_curvature_desired = curvatures[0]
  psi = interp(delay, T_IDXS[:CONTROL_N], psis)
  average_curvature_desired = psi / (v_ego * delay)
  desired_curvature = 2 * average_curvature_desired - current_curvature_desired

  # This is the "desired rate of the setpoint" not an actual desired rate
  desired_curvature_rate = curvature_rates[0]
  max_curvature_rate = MAX_LATERAL_JERK / (v_ego**2)
  safe_desired_curvature_rate = clip(desired_curvature_rate,
                                          -max_curvature_rate,
                                          max_curvature_rate)
  safe_desired_curvature = clip(desired_curvature,
                                     current_curvature_desired - max_curvature_rate * DT_MDL,
                                     current_curvature_desired + max_curvature_rate * DT_MDL)

  return safe_desired_curvature, safe_desired_curvature_rate


# Average only on straight roads (|curvature| below this, R > 500 m), so curves
# and turns keep the raw response.
PLAN_PAIR_STRAIGHT_CURV = 0.002


class PlanPairAverage:
  """Average the desired curvature of the current and the previous lateralPlan
  while driving straight.

  2026-10-06 lat_trace: the plan path is smooth (curvatures[0]) but the
  lag-adjusted desired curvature alternated from one plan to the next (e.g.
  -0.00071, -0.00004, -0.00095, -0.00003 at 50 km/h, 18 spikes > 0.1 m/s^2 in
  148 s), which flipped the torque command within 0.05 s ("tok" in the wheel).
  psi at the delay differs slightly between consecutive MPC solutions and
  2*psi/(v*delay) amplifies it, more at low speed. Averaging two plans cancels
  the alternation with half a plan period (~0.025 s) of delay, instead of a
  low-pass filter that would also slow the response to real lane changes.
  2026-10-17 lat_trace: same pattern on straights (46 spikes in 147 s).
  """
  def __init__(self):
    self.prev = None   # (curvature, rate) of the previous plan
    self.last = None   # latest (curvature, rate) from the current plan

  def update(self, curvature, rate, plan_updated, active=True):
    if not active:
      self.prev = None
      self.last = (curvature, rate)
      return curvature, rate
    if plan_updated and self.last is not None:
      self.prev = self.last
    self.last = (curvature, rate)
    if self.prev is None or abs(curvature) >= PLAN_PAIR_STRAIGHT_CURV or \
       abs(self.prev[0]) >= PLAN_PAIR_STRAIGHT_CURV:
      return curvature, rate
    return 0.5 * (curvature + self.prev[0]), 0.5 * (rate + self.prev[1])
