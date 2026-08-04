from cereal import car
from common.conversions import Conversions as CV
from common.numpy_fast import clip, interp
from common.params import Params
from common.realtime import DT_CTRL
from selfdrive.controls.lib.drive_helpers import CONTROL_N, apply_deadzone
from selfdrive.controls.lib.pid import PIDController
from selfdrive.modeld.constants import T_IDXS

LongCtrlState = car.CarControl.Actuators.LongControlState

# apilot-c2 style six-point acceleration table. Stored Params use 0.01 m/s^2.
ACCEL_BP = [0.0, 40.0 * CV.KPH_TO_MS, 60.0 * CV.KPH_TO_MS,
            80.0 * CV.KPH_TO_MS, 110.0 * CV.KPH_TO_MS, 140.0 * CV.KPH_TO_MS]
# Safe fallbacks preserve this branch's previous acceleration feel while adding
# the finer apilot-c2 speed breakpoints.
ACCEL_DEFAULTS = [1.80, 1.17, 1.03, 0.89, 0.74, 0.61]
DRIVING_MODE_ACCEL = {1: 0.80, 2: 0.64, 3: 1.00, 4: 1.00}


def long_control_state_trans(CP, active, long_control_state, v_ego, v_target,
                             v_target_1sec, brake_pressed, cruise_standstill,
                             a_target_now, starting_state):
  # apilot-c2 stopping transition: keep PID braking while the planned
  # acceleration is still strong, then hand over to the stopping ramp.
  cruise_standstill = cruise_standstill and not CP.enableGasInterceptor
  accelerating = v_target_1sec > (v_target + 0.01)
  planned_stop = (v_target < CP.vEgoStopping and
                  v_target_1sec < CP.vEgoStopping and
                  not accelerating)
  stay_stopped = (v_ego < CP.vEgoStopping and
                  (brake_pressed or cruise_standstill))
  stopping_condition = planned_stop or stay_stopped

  starting_condition = (v_target_1sec > CP.vEgoStarting and
                        accelerating and
                        not cruise_standstill and
                        not brake_pressed)
  started_condition = v_ego > CP.vEgoStarting

  if not active:
    long_control_state = LongCtrlState.off

  else:
    if long_control_state in (LongCtrlState.off, LongCtrlState.pid):
      long_control_state = LongCtrlState.pid
      if stopping_condition and a_target_now > -1.0:
        long_control_state = LongCtrlState.stopping

    elif long_control_state == LongCtrlState.stopping:
      if starting_condition:
        long_control_state = LongCtrlState.starting if starting_state else LongCtrlState.pid

    elif long_control_state == LongCtrlState.starting:
      if stopping_condition:
        long_control_state = LongCtrlState.stopping
      elif started_condition:
        long_control_state = LongCtrlState.pid

  return long_control_state


class LongControl:
  def __init__(self, CP):
    self.CP = CP
    self.long_control_state = LongCtrlState.off
    self.pid = PIDController((CP.longitudinalTuning.kpBP, CP.longitudinalTuning.kpV),
                             (CP.longitudinalTuning.kiBP, CP.longitudinalTuning.kiV),
                             k_f=CP.longitudinalTuning.kf, rate=1 / DT_CTRL)
    self.params = Params()
    self.read_param_count = 0
    self.stopping_accel = 0.0
    self.long_coast_band = 0.0
    self.v_pid = 0.0
    self.last_output_accel = 0.0

    # apilot-c2 launch control. A value of 25 means 0.50 m/s^2 because the
    # original branch applies StartAccelApply * 2. Use this conservative value
    # when no setting has been stored yet.
    self.start_accel_apply = 0.25
    self.start_accel = 0.50
    self.starting_state = True

    self.accel_max_vals = list(ACCEL_DEFAULTS)
    self.driving_mode = 3

    # apilot-c2 uses two actuator-delay predictions and selects the more
    # conservative target. Derive safe defaults around the configured delay.
    delay = float(clip(CP.longitudinalActuatorDelay, 0.1, 1.0))
    self.actuator_delay_lower = max(0.1, delay - 0.1)
    self.actuator_delay_upper = min(1.0, max(self.actuator_delay_lower + 0.05, delay + 0.1))

  def reset(self, v_pid=0.0):
    self.pid.reset()
    self.v_pid = v_pid

  def _read_params(self):
    self.read_param_count += 1
    if self.read_param_count >= 100:
      self.read_param_count = 0
      self.stopping_accel = self.params.get_float("StoppingAccel") * 0.01
      self.long_coast_band = clip(self.params.get_float("LongCoastBand") * 0.01, 0.0, 0.4)

      # Keep compatibility with apilot-c2 parameter names when present.
      lower = self.params.get_float("LongitudinalActuatorDelayLowerBound") * 0.01
      upper = self.params.get_float("LongitudinalActuatorDelayUpperBound") * 0.01
      if lower > 0.0:
        self.actuator_delay_lower = float(clip(lower, 0.1, 1.0))
      if upper > 0.0:
        self.actuator_delay_upper = float(clip(upper, self.actuator_delay_lower + 0.01, 1.0))
      elif self.actuator_delay_upper <= self.actuator_delay_lower:
        self.actuator_delay_upper = min(1.0, self.actuator_delay_lower + 0.05)

      start_raw = self.params.get_int("StartAccelApply")
      self.start_accel_apply = float(clip((start_raw if start_raw > 0 else 25) * 0.01, 0.0, 0.5))
      self.start_accel = float(clip(2.0 * self.start_accel_apply, 0.0, 1.0))
      self.starting_state = self.start_accel_apply > 0.0

      accel_vals = []
      for index, default in enumerate(ACCEL_DEFAULTS, start=1):
        raw = self.params.get_int("CruiseMaxVals%d" % index)
        value = raw * 0.01 if raw > 0 else default
        accel_vals.append(float(clip(value, 0.1, 2.5)))
      # Do not permit a higher-speed point to exceed the preceding point. This
      # prevents malformed settings or learned values from causing a surge.
      for index in range(1, len(accel_vals)):
        accel_vals[index] = min(accel_vals[index], accel_vals[index - 1])
      self.accel_max_vals = accel_vals

      mode = self.params.get_int("MyDrivingMode")
      self.driving_mode = mode if mode in DRIVING_MODE_ACCEL else 3

    elif self.read_param_count == 10:
      if len(self.CP.longitudinalTuning.kpBP) == 1 and len(self.CP.longitudinalTuning.kiBP) == 1:
        kp = self.params.get_float("LongTuningKpV") * 0.01
        ki = self.params.get_float("LongTuningKiV") * 0.001
        learned_kf = self.params.get_float("CarrotLongKf") if self.params.get_bool("CarrotLearningActive") else 0.0
        kf = learned_kf if learned_kf > 0.0 else self.params.get_float("LongTuningKf") * 0.01
        if kp > 0.0:
          self.pid._k_p = (self.CP.longitudinalTuning.kpBP, [kp])
        if ki > 0.0:
          self.pid._k_i = (self.CP.longitudinalTuning.kiBP, [ki])
        if kf > 0.0:
          self.pid.k_f = clip(kf, 0.7, 1.3)

  def update(self, active, CS, long_plan, accel_limits, t_since_plan, radar_state):
    self._read_params()

    if len(long_plan.speeds) == CONTROL_N:
      speeds = long_plan.speeds
      accels = long_plan.accels
      v_target_now = interp(t_since_plan, T_IDXS[:CONTROL_N], speeds)
      a_target_now = interp(t_since_plan, T_IDXS[:CONTROL_N], accels)

      # apilot-c2 dual-delay compensation. The lower and upper delay estimates
      # absorb vehicle brake-response variation; use the more braking target.
      v_target_lower = interp(self.actuator_delay_lower + t_since_plan,
                              T_IDXS[:CONTROL_N], speeds)
      v_target_upper = interp(self.actuator_delay_upper + t_since_plan,
                              T_IDXS[:CONTROL_N], speeds)
      a_target_lower = 2.0 * (v_target_lower - v_target_now) / self.actuator_delay_lower - a_target_now
      a_target_upper = 2.0 * (v_target_upper - v_target_now) / self.actuator_delay_upper - a_target_now
      v_target = min(v_target_lower, v_target_upper)
      a_target = min(a_target_lower, a_target_upper)

      v_target_1sec = interp(self.actuator_delay_lower + t_since_plan + 1.0,
                             T_IDXS[:CONTROL_N], speeds)
    else:
      v_target_now = 0.0
      a_target_now = 0.0
      v_target = 0.0
      v_target_1sec = 0.0
      a_target = 0.0

    # Apply the apilot-c2 six-point acceleration table at the final controller
    # output for both ACC and E2E. This keeps both modes consistent even when
    # the blended MPC is allowed to solve over the full acceleration range.
    mode_factor = DRIVING_MODE_ACCEL[self.driving_mode]
    table_accel_max = interp(CS.vEgo, ACCEL_BP, self.accel_max_vals) * mode_factor
    controller_pos_limit = min(accel_limits[1], table_accel_max)
    self.pid.neg_limit = accel_limits[0]
    self.pid.pos_limit = controller_pos_limit

    stop_accel = self.stopping_accel if self.stopping_accel < 0.0 else self.CP.stopAccel
    self.long_control_state = long_control_state_trans(
      self.CP, active, self.long_control_state, CS.vEgo, v_target, v_target_1sec,
      CS.brakePressed, CS.cruiseState.standstill, a_target_now, self.starting_state)

    if self.long_control_state == LongCtrlState.off:
      self.reset(CS.vEgo)
      output_accel = 0.0

    elif self.long_control_state == LongCtrlState.stopping:
      output_accel = self.last_output_accel
      if output_accel > stop_accel:
        output_accel = min(output_accel, 0.0)
        output_accel -= self.CP.stoppingDecelRate * DT_CTRL
      self.reset(CS.vEgo)

    elif self.long_control_state == LongCtrlState.starting:
      output_accel = min(self.start_accel, controller_pos_limit)
      self.reset(CS.vEgo)

    else:
      self.v_pid = v_target_now

      # apilot-c2 low-speed overshoot prevention. Near a planned stop, freeze
      # the integrator so it cannot build an acceleration correction while the
      # car is settling into the final brake hold.
      prevent_overshoot = (not self.CP.stoppingControl and CS.vEgo < 1.5 and
                           v_target_1sec < 0.7 and v_target_1sec < self.v_pid)
      deadzone = interp(CS.vEgo,
                        self.CP.longitudinalTuning.deadzoneBP,
                        self.CP.longitudinalTuning.deadzoneV)
      error = apply_deadzone(self.v_pid - CS.vEgo, deadzone)
      output_accel = self.pid.update(error, speed=CS.vEgo, feedforward=a_target,
                                     freeze_integrator=prevent_overshoot)

      if -self.long_coast_band < output_accel < 0.0:
        output_accel = 0.0

    self.last_output_accel = clip(output_accel, accel_limits[0], controller_pos_limit)
    return self.last_output_accel
