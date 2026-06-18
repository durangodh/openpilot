from cereal import car
from common.numpy_fast import clip, interp
from common.realtime import DT_CTRL
from selfdrive.controls.lib.drive_helpers import CONTROL_N, apply_deadzone
from selfdrive.controls.lib.pid import PIDController
from selfdrive.modeld.constants import T_IDXS

LongCtrlState = car.CarControl.Actuators.LongControlState


def long_control_state_trans(CP, active, long_control_state, v_ego, v_target,
                             v_target_1sec, brake_pressed, cruise_standstill, radar_state):
  # Ignore cruise standstill if car has a gas interceptor
  cruise_standstill = cruise_standstill and not CP.enableGasInterceptor                             
  accelerating = v_target_1sec > v_target
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

  # neokii
  if radar_state is not None and radar_state.leadOne is not None and radar_state.leadOne.status:
    starting_condition = starting_condition and radar_state.leadOne.vLead > CP.vEgoStarting

  if not active:
    long_control_state = LongCtrlState.off

  else:
    if long_control_state == LongCtrlState.off:
      if not starting_condition:
        long_control_state = LongCtrlState.stopping
      else:
        if starting_condition and CP.startingState:
          long_control_state = LongCtrlState.starting
        else:
          long_control_state = LongCtrlState.pid 

    elif long_control_state == LongCtrlState.stopping:
      if starting_condition and CP.startingState:
        long_control_state = LongCtrlState.starting
      elif starting_condition:
        long_control_state = LongCtrlState.pid

    elif long_control_state in [LongCtrlState.starting, LongCtrlState.pid]:
      if stopping_condition:
        long_control_state = LongCtrlState.stopping
      elif started_condition:
        long_control_state = LongCtrlState.pid




                               
  return long_control_state


class LongControl:
  def __init__(self, CP):
    self.CP = CP
    self.long_control_state = LongCtrlState.off  # initialized to off
    self.pid = PIDController((CP.longitudinalTuning.kpBP, CP.longitudinalTuning.kpV),
                             (CP.longitudinalTuning.kiBP, CP.longitudinalTuning.kiV),
                             k_f=CP.longitudinalTuning.kf, rate=1 / DT_CTRL)
    self.v_pid = 0.0
    self.last_output_accel = 0.0

    # ── Auto-Tuner 라이브 반영 (경로 A: 개입 기반 추천값을 Params에서 주기적으로 로드) ──
    # CP는 부팅 시 고정이라, longcontrol이 매 프레임 읽는 값을 별도 멤버로 분리해
    # carrot_learning이 기록한 추천값(있을 때만)으로 1초마다 갱신한다.
    from common.params import Params
    self._params = Params()
    self._frame = 0
    self._base_actuator_delay = CP.longitudinalActuatorDelay
    self._base_kf = CP.longitudinalTuning.kf
    self.long_actuator_delay = self._base_actuator_delay
    self._reload_learned()

  def _get_float(self, key, default):
    try:
      raw = self._params.get(key, encoding='utf8')
      return float(raw) if raw else default
    except (TypeError, ValueError):
      return default

  def _reload_learned(self):
    # 학습 비활성 시에는 항상 기본값 사용 (안전)
    active = False
    try:
      active = self._params.get_bool("CarrotLearningActive")
    except Exception:
      active = False
    if not active:
      self.long_actuator_delay = self._base_actuator_delay
      self.pid.k_f = self._base_kf
      return
    # actuatorDelay: 보수적 clip 0.1~1.0초
    d = self._get_float("CarrotLongActuatorDelay", self._base_actuator_delay)
    self.long_actuator_delay = float(clip(d, 0.1, 1.0))
    # kf(피드포워드): 기본값 대비 좁은 clip 0.7~1.3 (급가감속 방지)
    kf = self._get_float("CarrotLongKf", self._base_kf)
    self.pid.k_f = float(clip(kf, 0.7, 1.3))

  def reset(self, v_pid):
    """Reset PID controller and change setpoint"""
    self.pid.reset()
    self.v_pid = v_pid

  def update(self, active, CS, long_plan, accel_limits, t_since_plan, radar_state):
    """Update longitudinal control. This updates the state machine and runs a PID loop"""
    # Auto-Tuner: 학습 추천값 1초마다 라이브 반영 (재시작 불필요)
    self._frame += 1
    if self._frame % 100 == 0:
      try:
        self._reload_learned()
      except Exception:
        pass

    # Interp control trajectory
    speeds = long_plan.speeds
    if len(speeds) == CONTROL_N:
      v_target_now = interp(t_since_plan, T_IDXS[:CONTROL_N], speeds)
      a_target_now = interp(t_since_plan, T_IDXS[:CONTROL_N], long_plan.accels)

      v_target = interp(self.long_actuator_delay + t_since_plan, T_IDXS[:CONTROL_N], speeds)
      a_target = 2 * (v_target - v_target_now) / self.long_actuator_delay - a_target_now

      v_target_1sec = interp(self.long_actuator_delay + t_since_plan + 1.0, T_IDXS[:CONTROL_N], speeds)
    else:
      v_target = 0.0
      v_target_now = 0.0
      v_target_1sec = 0.0
      a_target = 0.0

    self.pid.neg_limit = accel_limits[0]
    self.pid.pos_limit = accel_limits[1]

    output_accel = self.last_output_accel
    self.long_control_state = long_control_state_trans(self.CP, active, self.long_control_state, CS.vEgo,
                                                       v_target, v_target_1sec, CS.brakePressed,
                                                       CS.cruiseState.standstill, radar_state)

    if self.long_control_state == LongCtrlState.off:
      self.reset(CS.vEgo)
      output_accel = 0.

    elif self.long_control_state == LongCtrlState.stopping:
      if output_accel > self.CP.stopAccel:
        output_accel = min(output_accel, 0.0)
        output_accel -= interp(output_accel, [-1.5, -0.5], [self.CP.stoppingDecelRate / 2., self.CP.stoppingDecelRate]) * DT_CTRL
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
      output_accel = self.pid.update(error_deadzone, speed=CS.vEgo,
                                     feedforward=a_target,
                                     freeze_integrator=freeze_integrator)

    
    self.last_output_accel = clip(output_accel, accel_limits[0], accel_limits[1])

    return self.last_output_accel
