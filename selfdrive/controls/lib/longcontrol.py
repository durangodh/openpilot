from cereal import car
from common.numpy_fast import clip, interp
from common.params import Params
from common.realtime import DT_CTRL
from selfdrive.controls.lib.drive_helpers import CONTROL_N
from selfdrive.controls.lib.pid import PIDController
from selfdrive.modeld.constants import T_IDXS

LongCtrlState = car.CarControl.Actuators.LongControlState


def long_control_state_trans(CP, active, long_control_state, v_ego, should_stop,
                             brake_pressed, cruise_standstill, a_ego, stop_accel,
                             radar_state):
  cruise_standstill = cruise_standstill and not CP.enableGasInterceptor
  stopping_condition = should_stop or (v_ego < CP.vEgoStopping and (brake_pressed or cruise_standstill))
  starting_condition = not should_stop and not cruise_standstill and not brake_pressed
  started_condition = v_ego > CP.vEgoStarting

  if not active:
    return LongCtrlState.off

  if long_control_state == LongCtrlState.off:
    if stopping_condition:
      long_control_state = LongCtrlState.stopping
    else:
      long_control_state = LongCtrlState.starting if CP.startingState else LongCtrlState.pid

  elif long_control_state == LongCtrlState.stopping:
    if starting_condition:
      long_control_state = LongCtrlState.starting if CP.startingState else LongCtrlState.pid

  elif long_control_state in (LongCtrlState.starting, LongCtrlState.pid):
    if stopping_condition:
      lead = radar_state.leadOne
      close_lead = lead.status and lead.dRel < 4.0
      # Hand over to the stopping ramp only after actual deceleration has
      # relaxed near the configured hold value. This avoids a second brake
      # step while the MPC is still commanding stronger lead deceleration.
      if a_ego > stop_accel or close_lead or long_control_state == LongCtrlState.starting:
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
      should_stop = long_plan.shouldStop
    else:
      v_target_now = 0.0
      a_target = 0.0
      should_stop = False

    self.pid.neg_limit = accel_limits[0]
    self.pid.pos_limit = accel_limits[1]

    stop_accel = self.stopping_accel if self.stopping_accel < 0.0 else self.CP.stopAccel
    self.long_control_state = long_control_state_trans(
      self.CP, active, self.long_control_state, CS.vEgo, should_stop,
      CS.brakePressed, CS.cruiseState.standstill, CS.aEgo, stop_accel, radar_state)

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
      # Coast band is useful during normal cruising, but suppressing small
      # braking commands while following a lead creates coast/brake cycling.
      # Preserve the MPC's continuous negative command during lead approaches.
      has_lead = radar_state.leadOne.status
      if not should_stop and not has_lead and -self.long_coast_band < output_accel < 0.0:
        output_accel = 0.0

    self.last_output_accel = clip(output_accel, accel_limits[0], accel_limits[1])
    return self.last_output_accel
