from cereal import car
from common.numpy_fast import clip, interp
from common.params import Params
from common.realtime import DT_CTRL
from selfdrive.controls.lib.drive_helpers import CONTROL_N, apply_deadzone
from selfdrive.controls.lib.longitudinal_transition import (ACCEL_MODE_TRANSITION_TIME,
                                                           limit_accel_increase)
from selfdrive.controls.lib.lead_departure import LeadDepartureController
from selfdrive.controls.lib.pid import PIDController
from selfdrive.modeld.constants import T_IDXS

LongCtrlState = car.CarControl.Actuators.LongControlState

def long_control_state_trans(CP, active, long_control_state, v_ego, v_target,
                             v_target_1sec, brake_pressed, cruise_standstill,
                             soft_hold, a_target_now, starting_state, lead_departing=False):
  # apilot-c2 stopping transition: keep PID braking while the planned
  # acceleration is still strong, then hand over to the stopping ramp.
  # With openpilot longitudinal control the stock SCC standstill flag can
  # remain latched until an acceleration command is sent. Using it here would
  # create a deadlock: starting is blocked while the flag is set, but the flag
  # cannot clear until starting begins. Let the MPC trajectory decide stop and
  # launch for openpilot long; retain the stock flag for PCM cruise control.
  cruise_standstill = (cruise_standstill and not CP.enableGasInterceptor and
                       not CP.openpilotLongitudinalControl)
  accelerating = v_target_1sec > (v_target + 0.01)
  planned_stop = (v_target < CP.vEgoStopping and
                  v_target_1sec < CP.vEgoStopping and
                  not accelerating)
  stay_stopped = (v_ego < CP.vEgoStopping and
                  (brake_pressed or cruise_standstill))
  stopping_condition = planned_stop or stay_stopped

  planned_start = (v_target_1sec > CP.vEgoStarting and
                   accelerating and
                   not cruise_standstill and
                   not brake_pressed)
  starting_condition = planned_start or (lead_departing and not brake_pressed)
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
      if lead_departing:
        pass
      elif stopping_condition:
        long_control_state = LongCtrlState.stopping
      elif started_condition:
        long_control_state = LongCtrlState.pid

    if soft_hold:
      long_control_state = LongCtrlState.stopping

  return long_control_state, planned_stop and not lead_departing


class LongControl:
  def __init__(self, CP):
    self.CP = CP
    self.long_control_state = LongCtrlState.off
    self.pid = PIDController((CP.longitudinalTuning.kpBP, CP.longitudinalTuning.kpV),
                             (CP.longitudinalTuning.kiBP, CP.longitudinalTuning.kiV),
                             k_f=CP.longitudinalTuning.kf, rate=1 / DT_CTRL)
    self.params = Params()
    self.read_param_count = 0
    self.stop_accel = CP.stopAccel
    self.long_coast_band = 0.0
    self.v_pid = 0.0
    self.last_output_accel = 0.0
    self.accel_transition_time = 0.0
    self.prev_mpc_mode = None
    self.lead_departure = LeadDepartureController()

    # Read launch control immediately so StartAccelApply=0 disables the
    # starting state from the first control cycle.
    self._update_start_accel()
    self._update_stop_accel()

    # apilot-c2 uses two actuator-delay predictions and selects the more
    # conservative target. Derive safe defaults around the configured delay.
    delay = float(clip(CP.longitudinalActuatorDelay, 0.1, 1.0))
    schema_lower = CP.longitudinalActuatorDelayLowerBound
    schema_upper = CP.longitudinalActuatorDelayUpperBound
    self.actuator_delay_lower = float(clip(schema_lower if schema_lower > 0.0 else delay - 0.1, 0.1, 0.99))
    self.actuator_delay_upper = float(clip(schema_upper if schema_upper > 0.0 else delay + 0.1,
                                           self.actuator_delay_lower, 1.0))
    self._update_actuator_delays()

  def _update_start_accel(self):
    start_raw = self.params.get_int("StartAccelApply", 0)
    self.start_accel_apply = float(clip(start_raw * 0.01, 0.0, 1.0))
    self.start_accel = float(clip(2.0 * self.start_accel_apply, 0.0, 2.0))
    self.starting_state = start_raw > 0

  def _update_stop_accel(self):
    stop_raw = self.params.get("StopAccelApply", encoding="utf8")
    if stop_raw is not None:
      try:
        stop_accel_apply = float(clip(int(stop_raw) * 0.01, 0.0, 1.0))
      except (TypeError, ValueError):
        stop_accel_apply = 0.3
      self.stop_accel = -2.0 * stop_accel_apply
    else:
      # Preserve an existing StoppingAccel value until StopAccelApply is
      # changed in the UI. With neither value set, use the car default.
      legacy_stop_accel = self.params.get_float("StoppingAccel") * 0.01
      self.stop_accel = legacy_stop_accel if legacy_stop_accel < 0.0 else self.CP.stopAccel

  def _update_actuator_delays(self):
    lower = self.params.get_float("LongitudinalActuatorDelayLowerBound") * 0.01
    upper = self.params.get_float("LongitudinalActuatorDelayUpperBound") * 0.01
    if lower > 0.0:
      self.actuator_delay_lower = float(clip(lower, 0.1, 0.99))
    if upper > 0.0:
      self.actuator_delay_upper = float(clip(upper, self.actuator_delay_lower, 1.0))
    elif self.actuator_delay_upper < self.actuator_delay_lower:
      self.actuator_delay_upper = self.actuator_delay_lower

  def reset(self, v_pid=0.0):
    self.pid.reset()
    self.v_pid = v_pid

  def _read_params(self):
    self.read_param_count += 1
    if self.read_param_count >= 100:
      self.read_param_count = 0
      self._update_stop_accel()
      self.long_coast_band = clip(self.params.get_float("LongCoastBand") * 0.01, 0.0, 0.4)
      self._update_actuator_delays()

      self._update_start_accel()

    elif self.read_param_count == 10:
      if len(self.CP.longitudinalTuning.kpBP) == 1 and len(self.CP.longitudinalTuning.kiBP) == 1:
        kp = self.params.get_float("LongTuningKpV") * 0.01
        ki = self.params.get_float("LongTuningKiV") * 0.001
        kf = self.params.get_float("LongTuningKf") * 0.01
        if kp > 0.0:
          self.pid._k_p = (self.CP.longitudinalTuning.kpBP, [kp])
        if ki > 0.0:
          self.pid._k_i = (self.CP.longitudinalTuning.kiBP, [ki])
        if kf > 0.0:
          self.pid.k_f = clip(kf, 0.7, 1.3)

  def update(self, active, CS, long_plan, accel_limits, t_since_plan, soft_hold=False, radar_state=None):
    self._read_params()

    if len(long_plan.speeds) == CONTROL_N:
      speeds = long_plan.speeds
      accels = long_plan.accels
      v_target_now = interp(t_since_plan, T_IDXS[:CONTROL_N], speeds)
      a_target_now = interp(t_since_plan, T_IDXS[:CONTROL_N], accels)
      j_target = long_plan.jerks[0] if len(long_plan.jerks) else 0.0

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
      j_target = 0.0

    self.pid.neg_limit = accel_limits[0]
    self.pid.pos_limit = accel_limits[1]

    lead = radar_state.leadOne if radar_state is not None else None
    plan_released = (v_target_1sec > self.CP.vEgoStarting and
                     v_target_1sec > v_target + 0.01)
    lead_departing = self.lead_departure.update(
      active=active,
      standstill=CS.standstill,
      plan_released=plan_released,
      brake_pressed=CS.brakePressed,
      gas_pressed=CS.gasPressed,
      lead_status=bool(lead is not None and lead.status),
      lead_distance=float(lead.dRel) if lead is not None else 0.0,
      lead_speed=float(max(lead.vRel, lead.vLeadK)) if lead is not None else 0.0,
      dt=DT_CTRL,
    )

    previous_long_control_state = self.long_control_state
    self.long_control_state, planned_stop = long_control_state_trans(
      self.CP, active, self.long_control_state, CS.vEgo, v_target, v_target_1sec,
      CS.brakePressed, CS.cruiseState.standstill, soft_hold, a_target_now,
      self.starting_state, lead_departing)

    mpc_mode = int(getattr(long_plan, "mpcMode", 0))
    mpc_mode_changed = self.prev_mpc_mode is not None and mpc_mode != self.prev_mpc_mode
    if mpc_mode_changed:
      self.accel_transition_time = max(self.accel_transition_time,
                                       ACCEL_MODE_TRANSITION_TIME)
    self.prev_mpc_mode = mpc_mode

    if self.long_control_state == LongCtrlState.off:
      self.reset(CS.vEgo)
      output_accel = 0.0

    elif self.long_control_state == LongCtrlState.stopping:
      output_accel = self.last_output_accel
      if output_accel > self.stop_accel:
        output_accel = min(output_accel, 0.0)
        output_accel -= self.CP.stoppingDecelRate * DT_CTRL
        if soft_hold:
          output_accel = self.stop_accel
      self.reset(CS.vEgo)

    elif self.long_control_state == LongCtrlState.starting:
      output_accel = min(self.start_accel, accel_limits[1])
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

    if not active or CS.gasPressed:
      self.accel_transition_time = 0.0
    else:
      output_accel, self.accel_transition_time = limit_accel_increase(
        output_accel, self.last_output_accel, self.accel_transition_time, DT_CTRL)

    self.last_output_accel = clip(output_accel, accel_limits[0], accel_limits[1])
    return self.last_output_accel, -0.5 if planned_stop else j_target
