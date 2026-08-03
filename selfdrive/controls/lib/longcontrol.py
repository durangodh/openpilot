from cereal import car
from common.numpy_fast import clip, interp
from common.params import Params
from common.realtime import DT_CTRL
from selfdrive.controls.lib.drive_helpers import CONTROL_N
from selfdrive.controls.lib.pid import PIDController
from selfdrive.modeld.constants import T_IDXS

LongCtrlState = car.CarControl.Actuators.LongControlState


def long_control_state_trans(CP, active, long_control_state, v_ego, v_target,
                             v_target_1sec, brake_pressed, cruise_standstill, a_target_now):
  cruise_standstill = cruise_standstill and not CP.enableGasInterceptor
  accelerating = v_target_1sec > (v_target + 0.01)
  planned_stop = v_target < CP.vEgoStopping and v_target_1sec < CP.vEgoStopping and not accelerating
  stay_stopped = v_ego < CP.vEgoStopping and (brake_pressed or cruise_standstill)
  stopping_condition = planned_stop or stay_stopped
  starting_condition = v_target_1sec > CP.vEgoStarting and accelerating and not cruise_standstill and not brake_pressed
  started_condition = v_ego > CP.vEgoStarting

  if not active:
    return LongCtrlState.off

  if long_control_state in (LongCtrlState.off, LongCtrlState.pid):
    long_control_state = LongCtrlState.pid
    if stopping_condition and a_target_now > -1.0:
      long_control_state = LongCtrlState.stopping

  elif long_control_state == LongCtrlState.stopping:
    if starting_condition:
      long_control_state = LongCtrlState.starting if CP.startingState else LongCtrlState.pid

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

  def reset(self, v_pid=0.0):
    self.pid.reset()
    self.v_pid = v_pid

  def _read_params(self):
    self.read_param_count += 1
    if self.read_param_count >= 100:
      self.read_param_count = 0
      self.stopping_accel = self.params.get_float("StoppingAccel") * 0.01
      self.long_coast_band = clip(self.params.get_float("LongCoastBand") * 0.01, 0.0, 0.4)
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
      # Delay compensation is already included in the planner's targets. Only
      # advance velocity by the age of the received plan.
      plan_accel_now = interp(t_since_plan, T_IDXS[:CONTROL_N], long_plan.accels)
      v_target_now = long_plan.vTargetNow + plan_accel_now * t_since_plan
      a_target = long_plan.aTarget
      v_target = interp(self.CP.longitudinalActuatorDelay + t_since_plan,
                        T_IDXS[:CONTROL_N], long_plan.speeds)
      v_target_1sec = interp(self.CP.longitudinalActuatorDelay + t_since_plan + 1.0,
                             T_IDXS[:CONTROL_N], long_plan.speeds)
      a_target_now = plan_accel_now
    else:
      v_target_now = 0.0
      a_target = 0.0
      v_target = 0.0
      v_target_1sec = 0.0
      a_target_now = 0.0

    self.pid.neg_limit = accel_limits[0]
    self.pid.pos_limit = accel_limits[1]

    stop_accel = self.stopping_accel if self.stopping_accel < 0.0 else self.CP.stopAccel
    self.long_control_state = long_control_state_trans(
      self.CP, active, self.long_control_state, CS.vEgo, v_target, v_target_1sec,
      CS.brakePressed, CS.cruiseState.standstill, a_target_now)

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
      output_accel = self.CP.startAccel
      self.reset(CS.vEgo)

    else:
      self.v_pid = v_target_now
      error = self.v_pid - CS.vEgo
      output_accel = self.pid.update(error, speed=CS.vEgo, feedforward=a_target)
      if -self.long_coast_band < output_accel < 0.0:
        output_accel = 0.0

    self.last_output_accel = clip(output_accel, accel_limits[0], accel_limits[1])
    return self.last_output_accel
