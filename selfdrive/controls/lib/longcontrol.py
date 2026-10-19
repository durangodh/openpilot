from cereal import car
from common.numpy_fast import clip, interp
from common.realtime import DT_CTRL
from selfdrive.controls.lib.drive_helpers import CONTROL_N, apply_deadzone
from selfdrive.controls.lib.pid import PIDController
from selfdrive.modeld.constants import T_IDXS
from common.params import Params


MOVING_STOP_FOLLOW_MIN_GAP = 0.25
NEGATIVE_TARGET_CREEP_GUARD_SPEED = 0.35
NEGATIVE_TARGET_CREEP_GUARD_DECEL = 0.40


def _param_int(key, default):
  """aPilot reads int(Params().get(key)); use aPilot's manager default when the key is unset."""
  try:
    value = Params().get(key, encoding="utf8")
    return int(float(value)) if value is not None else default
  except Exception:
    return default

LongCtrlState = car.CarControl.Actuators.LongControlState


### apilot
# planned_stop조건인데..... accel은 이미 stoppingAccel보다 낮은상태... 이상태로 stopping으로 진입하면.. 너무 많은 -accel로 정지하게 됨.
# accel이 -값에서 0에 가까와질때까지 기다릴 필요가 있음.... 20230911
def long_control_state_trans(CP, active, long_control_state, v_ego, v_target,
                             v_target_1sec, brake_pressed, cruise_standstill, softHold, a_target_now,
                             planner_should_stop=None):
  # Ignore cruise standstill if car has a gas interceptor
  cruise_standstill = cruise_standstill and not CP.enableGasInterceptor
  accelerating = v_target_1sec > (v_target + 0.01)
  inferred_stop = (v_target < CP.vEgoStopping and ## apilot: 내리막, 신호정지시 질질 가는 현상... v_target으로 보면.. 급정지, v_ego를 보면 질질감..
                   v_target_1sec < CP.vEgoStopping and
                   not accelerating)
  planned_stop = inferred_stop if planner_should_stop is None else bool(planner_should_stop)
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
      if stopping_condition and a_target_now > -1.0:  ### pid출력이 급정지(-accel) 상태에서 stopping으로 들어가면... 차량이 너무 급하게 섬.. 기다려보자.... 시험 230911
        long_control_state = LongCtrlState.stopping

    elif long_control_state == LongCtrlState.stopping:
      if starting_condition and CP.startingState:
        long_control_state = LongCtrlState.starting
      elif starting_condition:
        long_control_state = LongCtrlState.pid

    elif long_control_state == LongCtrlState.starting:
      if stopping_condition:
        long_control_state = LongCtrlState.stopping
      elif started_condition:
        long_control_state = LongCtrlState.pid

    if softHold:
      long_control_state = LongCtrlState.stopping
  return long_control_state, planned_stop


class LongControl:
  def __init__(self, CP):
    self.CP = CP
    self.long_control_state = LongCtrlState.off  # initialized to off
    self.pid = PIDController((CP.longitudinalTuning.kpBP, CP.longitudinalTuning.kpV),
                             (CP.longitudinalTuning.kiBP, CP.longitudinalTuning.kiV),
                             k_f=CP.longitudinalTuning.kf, rate=1 / DT_CTRL)
    self.v_pid = 0.0
    self.last_output_accel = 0.0
    self.debugLoCText = ""
    self.readParamCount = 0
    self.longitudinalTuningKpV = 1.0
    self.longitudinalTuningKiV = 0.0
    self.longitudinalTuningKf = 1.0
    self.startAccelApply = 0.0
    self.stopAccelApply = 0.0
    self.longitudinalActuatorDelayLowerBound = float(_param_int("LongitudinalActuatorDelayLowerBound", 50)) * 0.01
    self.longitudinalActuatorDelayUpperBound = float(_param_int("LongitudinalActuatorDelayUpperBound", 50)) * 0.01

  def reset(self, v_pid):
    """Reset PID controller and change setpoint"""
    self.pid.reset()
    self.v_pid = v_pid

  def _trim_positive_overshoot_integrator(self, a_target, accel_error, CS):
    if self.pid.i <= 0.0 or a_target >= -0.05 or accel_error >= -0.25:
      return
    if CS.vEgo <= NEGATIVE_TARGET_CREEP_GUARD_SPEED and a_target > -NEGATIVE_TARGET_CREEP_GUARD_DECEL:
      return

    # The MPC has crossed into deceleration but the car is still accelerating.
    # Bleed stale positive I so the command can cross zero without a throttle tail.
    bleed = interp(abs(accel_error), [0.25, 0.75, 1.5], [0.55, 0.25, 0.0])
    self.pid.i *= bleed

  @staticmethod
  def _cap_positive_output_on_negative_target(output_accel, a_target, accel_error, CS):
    if output_accel <= 0.0 or a_target >= -0.10 or accel_error >= -0.35:
      return output_accel
    if CS.vEgo <= NEGATIVE_TARGET_CREEP_GUARD_SPEED and a_target > -NEGATIVE_TARGET_CREEP_GUARD_DECEL:
      return output_accel

    positive_cap = interp(a_target, [-1.5, -0.6, -0.1], [0.0, 0.0, 0.05])
    return min(output_accel, float(positive_cap))

  @staticmethod
  def _apply_moving_stop_target_follow(output_accel, a_target, should_stop, CS, v_ego_stopping):
    follow_min_speed = max(1.5, v_ego_stopping + 1.0)
    if not should_stop or CS.brakePressed or CS.vEgo <= follow_min_speed:
      return output_accel
    if a_target >= output_accel - MOVING_STOP_FOLLOW_MIN_GAP:
      return output_accel

    follow_step = interp(CS.vEgo, [follow_min_speed, 3.0, 6.0, 10.0], [0.02, 0.03, 0.05, 0.07])
    return max(float(a_target), output_accel - float(follow_step))

  def update(self, active, CS, long_plan, accel_limits, t_since_plan, CC):
    self.readParamCount += 1
    if self.readParamCount >= 100:
      self.readParamCount = 0
    elif self.readParamCount == 10:
      self.longitudinalTuningKpV = float(_param_int("LongitudinalTuningKpV", 100)) * 0.01
      self.longitudinalTuningKiV = float(_param_int("LongitudinalTuningKiV", 200)) * 0.001
      self.longitudinalTuningKf = float(_param_int("LongitudinalTuningKf", 100)) * 0.01

      ## longcontrolTuning이 한개일때만 적용
      if len(self.CP.longitudinalTuning.kpBP) == 1 and len(self.CP.longitudinalTuning.kiBP)==1:
        self.CP.longitudinalTuning.kpV = [self.longitudinalTuningKpV]
        self.CP.longitudinalTuning.kiV = [self.longitudinalTuningKiV]
        self.pid._k_p = (self.CP.longitudinalTuning.kpBP, self.CP.longitudinalTuning.kpV)
        self.pid._k_i = (self.CP.longitudinalTuning.kiBP, self.CP.longitudinalTuning.kiV)
        self.pid.k_f = self.longitudinalTuningKf
        #self.pid._k_i = ([0, 2.0, 200], [self.longitudinalTuningKiV, 0.0, 0.0]) # 정지때만.... i를 적용해보자... 시험..
    elif self.readParamCount == 30:
      self.longitudinalActuatorDelayLowerBound = float(_param_int("LongitudinalActuatorDelayLowerBound", 50)) * 0.01
      self.longitudinalActuatorDelayUpperBound = float(_param_int("LongitudinalActuatorDelayUpperBound", 50)) * 0.01
    elif self.readParamCount == 40:
      self.startAccelApply = float(_param_int("StartAccelApply", 0)) * 0.01
      self.stopAccelApply = float(_param_int("StopAccelApply", 30)) * 0.01
      
    """Update longitudinal control. This updates the state machine and runs a PID loop"""
    # Interp control trajectory
    speeds = long_plan.speeds
    a_target_now = 0.0
    if len(speeds) == CONTROL_N:
      v_target_now = interp(t_since_plan, T_IDXS[:CONTROL_N], speeds)
      a_target_now = interp(t_since_plan, T_IDXS[:CONTROL_N], long_plan.accels)
      j_target = long_plan.jerks[0]

      #v_target_lower = interp(self.CP.longitudinalActuatorDelayLowerBound + t_since_plan, T_IDXS[:CONTROL_N], speeds)
      #a_target_lower = 2 * (v_target_lower - v_target_now) / self.CP.longitudinalActuatorDelayLowerBound - a_target_now

      #v_target_upper = interp(self.CP.longitudinalActuatorDelayUpperBound + t_since_plan, T_IDXS[:CONTROL_N], speeds)      
      #a_target_upper = 2 * (v_target_upper - v_target_now) / self.CP.longitudinalActuatorDelayUpperBound - a_target_now

      v_target_lower = interp(self.longitudinalActuatorDelayLowerBound + t_since_plan, T_IDXS[:CONTROL_N], speeds)
      a_target_lower = 2 * (v_target_lower - v_target_now) / self.longitudinalActuatorDelayLowerBound - a_target_now

      v_target_upper = interp(self.longitudinalActuatorDelayUpperBound + t_since_plan, T_IDXS[:CONTROL_N], speeds)
      a_target_upper = 2 * (v_target_upper - v_target_now) / self.longitudinalActuatorDelayUpperBound - a_target_now

      v_target = min(v_target_lower, v_target_upper)
      a_target = min(a_target_lower, a_target_upper)


      #v_target_1sec = interp(self.CP.longitudinalActuatorDelayUpperBound + t_since_plan + 1.0, T_IDXS[:CONTROL_N], speeds)
      #v_target_1sec = interp(self.longitudinalActuatorDelayUpperBound + t_since_plan + 1.0, T_IDXS[:CONTROL_N], speeds)
      v_target_1sec = interp(self.longitudinalActuatorDelayLowerBound + t_since_plan + 1.0, T_IDXS[:CONTROL_N], speeds)

      planner_should_stop = bool(long_plan.shouldStop)
      a_target = float(long_plan.aTarget)
    else:
      v_target = 0.0
      v_target_now = 0.0
      v_target_1sec = 0.0
      a_target = 0.0
      j_target = 0.0
      a_target_lower = a_target_upper = 0.0
      # No valid planner trajectory: retain the legacy fail-safe inference from
      # the zero targets instead of turning a missing plan into a launch request.
      planner_should_stop = None

    self.pid.neg_limit = accel_limits[0]
    self.pid.pos_limit = accel_limits[1]

    self.CP.startingState = True if self.startAccelApply > 0.0 else False
    self.CP.startAccel = 2.0 * self.startAccelApply
    self.CP.stopAccel = -2.0 * self.stopAccelApply

    output_accel = self.last_output_accel

    self.long_control_state, planned_stop = long_control_state_trans(self.CP, active, self.long_control_state, CS.vEgo,
                                                       v_target, v_target_1sec, CS.brakePressed,
                                                       CS.cruiseState.standstill, CC.hudControl.softHold, a_target_now,
                                                       planner_should_stop=planner_should_stop)

    if self.long_control_state == LongCtrlState.off:
      self.reset(CS.vEgo)
      output_accel = 0.

    elif self.long_control_state == LongCtrlState.stopping:
      if output_accel > self.CP.stopAccel:
        output_accel = min(output_accel, 0.0)
        output_accel -= self.CP.stoppingDecelRate * DT_CTRL
        if CC.hudControl.softHold:
          output_accel = self.CP.stopAccel
      output_accel = self._apply_moving_stop_target_follow(
        output_accel, a_target, planned_stop, CS, self.CP.vEgoStopping,
      )
      self.reset(CS.vEgo)

    elif self.long_control_state == LongCtrlState.starting:
      output_accel = self.CP.startAccel
      self.reset(CS.vEgo)

    elif self.long_control_state == LongCtrlState.pid:
      self.v_pid = v_target_now

      # Toyota starts braking more when it thinks you want to stop
      # Freeze the integrator so we don't accelerate to compensate, and don't allow positive acceleration
      # TODO too complex, needs to be simplified and tested on toyotas
      prevent_overshoot = not self.CP.stoppingControl and CS.vEgo < 1.5 and v_target_1sec < 0.7 and v_target_1sec < self.v_pid
      deadzone = interp(CS.vEgo, self.CP.longitudinalTuning.deadzoneBP, self.CP.longitudinalTuning.deadzoneV)
      freeze_integrator = prevent_overshoot

      error = self.v_pid - CS.vEgo
      error_deadzone = apply_deadzone(error, deadzone)
      accel_error = a_target - CS.aEgo
      self._trim_positive_overshoot_integrator(a_target, accel_error, CS)
      output_accel = self.pid.update(error_deadzone, speed=CS.vEgo,
                                     feedforward=a_target,
                                     freeze_integrator=freeze_integrator)
      output_accel = self._cap_positive_output_on_negative_target(output_accel, a_target, accel_error, CS)

    self.last_output_accel = clip(output_accel, accel_limits[0], accel_limits[1])

    #self.debugLoCText = "T:{:.2f} V:{:.2f}={:.1f}-{:.1f} Aout:{:.2f}<{:.2f}".format(t_since_plan, (self.v_pid - CS.vEgo)*3.6, self.v_pid*3.6, CS.vEgo*3.3, self.last_output_accel, output_accel)
    #C2# self.debugLoCText = "pid={},vego={:.2f},vt={:.2f},{:.2f},vStop={:.2f}".format(self.long_control_state, CS.vEgo, v_target, v_target_1sec, self.CP.vEgoStopping)

    return self.last_output_accel, -0.5 if planned_stop else j_target
