from cereal import car
from common.numpy_fast import clip, interp
from common.params import Params
from common.realtime import DT_CTRL
from selfdrive.controls.lib.drive_helpers import CONTROL_N, apply_deadzone
from selfdrive.controls.lib.pid import PIDController
from selfdrive.modeld.constants import T_IDXS

LongCtrlState = car.CarControl.Actuators.LongControlState
ButtonType = car.CarState.ButtonEvent.Type

STANDSTILL_LEAD_MAX_DISTANCE = 20.0
STANDSTILL_LEAD_MAX_SPEED = 0.3
LEAD_RELEASE_MIN_SPEED = 0.25
LEAD_RELEASE_MIN_VREL = 0.1
LEAD_RELEASE_CONFIRM_SAMPLES = 2
LEAD_DROPOUT_FALLBACK_FRAMES = round(1.5 / DT_CTRL)


# apilot-c2 상태전이.
# planned_stop 조건인데 accel 이 이미 stopAccel 보다 낮은 상태로 stopping 에 들어가면 너무 급하게 서므로
# a_target_now 가 -1.0 보다 커질 때까지(=제동이 완만해질 때까지) PID 를 유지한다. (apilot 2023-09-11)
def long_control_state_trans(CP, active, long_control_state, v_ego, v_target,
                             v_target_1sec, brake_pressed, cruise_standstill, soft_hold, a_target_now,
                             start_gate=True):
  # Ignore cruise standstill if car has a gas interceptor
  cruise_standstill = cruise_standstill and not CP.enableGasInterceptor
  accelerating = v_target_1sec > (v_target + 0.01)
  # apilot: v_ego 대신 v_target 으로 보면 내리막/신호정지에서 질질 끌리지 않는다
  planned_stop = (v_target < CP.vEgoStopping and
                  v_target_1sec < CP.vEgoStopping and
                  not accelerating)
  stay_stopped = (v_ego < CP.vEgoStopping and
                  (brake_pressed or cruise_standstill))
  stopping_condition = planned_stop or stay_stopped

  # start_gate: 정체 가다서다 둔감화(StandstillReleaseSpeed/Ms). LongControl 이
  # 플래너 출발 요구의 세기·지속시간을 보고 넘겨준다. 가속페달·RES 는 게이트를 우회한다.
  starting_condition = (v_target_1sec > CP.vEgoStarting and
                        accelerating and
                        not cruise_standstill and
                        not brake_pressed and
                        start_gate)
  started_condition = v_ego > CP.vEgoStarting

  if not active:
    long_control_state = LongCtrlState.off

  else:
    if long_control_state in (LongCtrlState.off, LongCtrlState.pid):
      long_control_state = LongCtrlState.pid
      if stopping_condition and a_target_now > -1.0:
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

    if soft_hold:
      long_control_state = LongCtrlState.stopping

  return long_control_state, planned_stop


class LongControl:
  def __init__(self, CP):
    self.CP = CP
    self.long_control_state = LongCtrlState.off  # initialized to off
    self.pid = PIDController((CP.longitudinalTuning.kpBP, CP.longitudinalTuning.kpV),
                             (CP.longitudinalTuning.kiBP, CP.longitudinalTuning.kiV),
                             k_f=CP.longitudinalTuning.kf, rate=1 / DT_CTRL)
    self.params = Params()
    self.read_param_count = 0
    self.v_pid = 0.0
    self.last_output_accel = 0.0

    # apilot-c2: 지연보상 하한/상한 (x100 저장, 기본 0.5/0.5)
    self.actuator_delay_lower = 0.5
    self.actuator_delay_upper = 0.5
    self.start_accel_apply = 0.0
    self.stop_accel_apply = 0.3
    self.stopping_decel_rate = CP.stoppingDecelRate
    # StopAccelApply controls the approach to zero speed.  A separate target
    # is needed after the car is fully stopped so a comfortable stop does not
    # slowly lose hydraulic hold during a long wait.
    self.standstill_hold_accel = -1.1
    self.standstill_hold_rate = 1.2
    self.standstill_hold_active = False

    self._update_pid_gains()
    self._update_actuator_delays()
    self._update_start_stop_accel()
    self._update_stopping_decel_rate()
    self._update_standstill_hold()
    self._update_standstill_release()
    self.start_request_frames = 0
    self.standstill_lead_latched = False
    self.lead_release_samples = 0
    self.lead_measurement_available = False
    self.lead_missing_frames = 0

  # ---- 파라미터 (키 이름은 이 포크 것을 유지) ----
  def _update_pid_gains(self):
    # apilot-c2: longitudinalTuning 이 한 개(BP 1개)일 때만 UI 값으로 덮어쓴다
    if len(self.CP.longitudinalTuning.kpBP) != 1 or len(self.CP.longitudinalTuning.kiBP) != 1:
      return
    try:
      kp_raw = self.params.get("LongTuningKpV", encoding="utf8")
      ki_raw = self.params.get("LongTuningKiV", encoding="utf8")
      kf_raw = self.params.get("LongTuningKf", encoding="utf8")
      if kp_raw is not None:
        self.CP.longitudinalTuning.kpV = [float(int(kp_raw)) * 0.01]
        self.pid._k_p = (self.CP.longitudinalTuning.kpBP, self.CP.longitudinalTuning.kpV)
      if ki_raw is not None:
        self.CP.longitudinalTuning.kiV = [float(int(ki_raw)) * 0.001]
        self.pid._k_i = (self.CP.longitudinalTuning.kiBP, self.CP.longitudinalTuning.kiV)
      if kf_raw is not None:
        self.pid.k_f = float(int(kf_raw)) * 0.01
    except (TypeError, ValueError):
      pass

  def _update_actuator_delays(self):
    try:
      lower = float(int(self.params.get("LongitudinalActuatorDelayLowerBound", encoding="utf8") or 0)) * 0.01
      upper = float(int(self.params.get("LongitudinalActuatorDelayUpperBound", encoding="utf8") or 0)) * 0.01
    except (TypeError, ValueError):
      lower = upper = 0.0
    if lower <= 0.0:
      lower = self.CP.longitudinalActuatorDelayLowerBound if self.CP.longitudinalActuatorDelayLowerBound > 0.0 else 0.5
    if upper <= 0.0:
      upper = self.CP.longitudinalActuatorDelayUpperBound if self.CP.longitudinalActuatorDelayUpperBound > 0.0 else 0.5
    self.actuator_delay_lower = float(clip(lower, 0.1, 1.0))
    self.actuator_delay_upper = float(clip(upper, 0.1, 1.0))

  def _update_start_stop_accel(self):
    try:
      start_raw = self.params.get("StartAccelApply", encoding="utf8")
      stop_raw = self.params.get("StopAccelApply", encoding="utf8")
      if start_raw is not None:
        self.start_accel_apply = float(clip(int(start_raw) * 0.01, 0.0, 1.0))
      if stop_raw is not None:
        self.stop_accel_apply = float(clip(int(stop_raw) * 0.01, 0.0, 1.0))
    except (TypeError, ValueError):
      pass
    # apilot-c2: StartAccelApply > 0 일 때만 starting 상태 사용, startAccel = 2.0 x 비율, stopAccel = -2.0 x 비율
    self.CP.startingState = self.start_accel_apply > 0.0
    self.CP.startAccel = 2.0 * self.start_accel_apply
    self.CP.stopAccel = -2.0 * self.stop_accel_apply

  def _update_standstill_release(self):
    """정체 가다서다 출발 둔감화. StandstillReleaseSpeed (x0.1 m/s, 기본 2=0.2),
    StandstillReleaseMs (기본 100). 기본값이면 종전 동작과 같다."""
    try:
      raw = self.params.get("StandstillReleaseSpeed", encoding="utf8")
      speed = int(raw) * 0.1 if raw not in (None, "") else self.CP.vEgoStarting
    except (TypeError, ValueError):
      speed = self.CP.vEgoStarting
    try:
      raw = self.params.get("StandstillReleaseMs", encoding="utf8")
      ms = int(raw) if raw not in (None, "") else 100
    except (TypeError, ValueError):
      ms = 100
    self.standstill_release_speed = float(clip(speed, 0.0, 2.0))
    self.standstill_release_frames = int(clip(ms, 50, 2000) / 10)

  def _reset_standstill_lead(self):
    self.standstill_lead_latched = False
    self.lead_release_samples = 0
    self.lead_measurement_available = False
    self.lead_missing_frames = 0

  def _update_standstill_lead(self, radar_state, radar_state_valid, radar_state_updated):
    """Latch a stopped lead and release only after fresh samples confirm it is moving.

    A transient missing/stale radar sample never opens the gate. If radar remains
    unavailable for 1.5 seconds, the caller falls back to the sustained planner
    request so a permanent radar outage cannot disable automatic launch.
    """
    if radar_state_updated:
      lead_valid = (radar_state is not None and radar_state_valid and
                    len(radar_state.radarErrors) == 0 and radar_state.leadOne.status)
      self.lead_measurement_available = lead_valid
      if not lead_valid:
        self.lead_release_samples = 0
      else:
        lead = radar_state.leadOne
        if not self.standstill_lead_latched:
          stopped_lead = (0.0 < lead.dRel <= STANDSTILL_LEAD_MAX_DISTANCE and
                          abs(lead.vLeadK) <= STANDSTILL_LEAD_MAX_SPEED and
                          abs(lead.vRel) <= STANDSTILL_LEAD_MAX_SPEED)
          if stopped_lead:
            self.standstill_lead_latched = True
        else:
          lead_moving = (lead.vLeadK > LEAD_RELEASE_MIN_SPEED and
                         lead.vRel > LEAD_RELEASE_MIN_VREL)
          self.lead_release_samples = self.lead_release_samples + 1 if lead_moving else 0
    elif not radar_state_valid:
      self.lead_measurement_available = False
      self.lead_release_samples = 0

    if self.standstill_lead_latched:
      if self.lead_measurement_available:
        self.lead_missing_frames = 0
      else:
        self.lead_missing_frames += 1
    return self.lead_release_samples >= LEAD_RELEASE_CONFIRM_SAMPLES

  def _update_stopping_decel_rate(self):
    try:
      rate_raw = self.params.get("StoppingDecelRate", encoding="utf8")
      rate = int(rate_raw) * 0.01 if rate_raw is not None else 0.0
    except (TypeError, ValueError):
      rate = 0.0
    self.stopping_decel_rate = float(clip(rate, 0.2, 2.0)) if rate > 0.0 else self.CP.stoppingDecelRate

  def _update_standstill_hold(self):
    try:
      hold_raw = self.params.get("StandstillHoldApply", encoding="utf8")
      hold_apply = int(hold_raw) if hold_raw not in (None, "") else 55
    except (TypeError, ValueError):
      hold_apply = 55
    try:
      rate_raw = self.params.get("StandstillHoldRate", encoding="utf8")
      hold_rate = int(rate_raw) * 0.01 if rate_raw not in (None, "") else 1.2
    except (TypeError, ValueError):
      hold_rate = 1.2

    self.standstill_hold_accel = -2.0 * float(clip(hold_apply * 0.01, 0.1, 1.0))
    self.standstill_hold_rate = float(clip(hold_rate, 0.2, 2.0))

  def _read_params(self):
    self.read_param_count += 1
    if self.read_param_count >= 100:
      self.read_param_count = 0
      self._update_standstill_release()
    elif self.read_param_count == 10:
      self._update_pid_gains()
    elif self.read_param_count == 30:
      self._update_actuator_delays()
    elif self.read_param_count == 40:
      self._update_start_stop_accel()
      self._update_stopping_decel_rate()
      self._update_standstill_hold()

  def reset(self, v_pid=0.0):
    """Reset PID controller and change setpoint"""
    self.pid.reset()
    self.v_pid = v_pid

  def update(self, active, CS, long_plan, accel_limits, t_since_plan, soft_hold=False,
             radar_state=None, radar_state_valid=False, radar_state_updated=False):
    """Update longitudinal control. This updates the state machine and runs a PID loop"""
    self._read_params()

    # Interp control trajectory
    speeds = long_plan.speeds
    if len(speeds) == CONTROL_N:
      v_target_now = interp(t_since_plan, T_IDXS[:CONTROL_N], speeds)
      a_target_now = interp(t_since_plan, T_IDXS[:CONTROL_N], long_plan.accels)
      j_target = long_plan.jerks[0] if len(long_plan.jerks) else 0.0

      # apilot-c2 dual-delay compensation: 하한/상한 두 지연으로 예측한 목표 중 더 보수적인(작은) 쪽을 쓴다
      v_target_lower = interp(self.actuator_delay_lower + t_since_plan, T_IDXS[:CONTROL_N], speeds)
      a_target_lower = 2 * (v_target_lower - v_target_now) / self.actuator_delay_lower - a_target_now

      v_target_upper = interp(self.actuator_delay_upper + t_since_plan, T_IDXS[:CONTROL_N], speeds)
      a_target_upper = 2 * (v_target_upper - v_target_now) / self.actuator_delay_upper - a_target_now

      v_target = min(v_target_lower, v_target_upper)
      a_target = min(a_target_lower, a_target_upper)

      v_target_1sec = interp(self.actuator_delay_lower + t_since_plan + 1.0, T_IDXS[:CONTROL_N], speeds)
    else:
      v_target = 0.0
      v_target_now = 0.0
      v_target_1sec = 0.0
      a_target = 0.0
      a_target_now = 0.0
      j_target = 0.0

    self.pid.neg_limit = accel_limits[0]
    self.pid.pos_limit = accel_limits[1]

    output_accel = self.last_output_accel

    # 정차 중 멈춘 선행차를 확인했다면 플래너 속도만으로 출발하지 않는다.
    # 새 radarState 샘플에서 선행차 이동이 연속 확인될 때 빠르게 출발한다.
    # 선행차 없이 정차한 경우(신호 등)는 기존 속도/지연 설정을 사용한다.
    if self.long_control_state == LongCtrlState.stopping:
      resume_pressed = any(e.pressed and e.type in (ButtonType.accelCruise, ButtonType.resumeCruise)
                           for e in CS.buttonEvents)
      driver_override = CS.gasPressed or resume_pressed
      # ConditionalE2E already confirms the green/departure signal before it
      # publishes state 2. Do not apply StandstillReleaseMs a second time when
      # there is no confirmed stopped lead. A latched lead still owns release
      # above, so a green signal can never launch into a stationary vehicle.
      traffic_departure = int(getattr(long_plan, 'trafficState', 0)) % 100 == 2
      lead_release = self._update_standstill_lead(radar_state, radar_state_valid, radar_state_updated)
      radar_fallback = self.lead_missing_frames >= LEAD_DROPOUT_FALLBACK_FRAMES
      if self.standstill_lead_latched and not radar_fallback:
        self.start_request_frames = 0
        start_gate = driver_override or lead_release
      else:
        strong_request = v_target_1sec > max(self.CP.vEgoStarting, self.standstill_release_speed)
        self.start_request_frames = self.start_request_frames + 1 if strong_request else 0
        start_gate = (driver_override or lead_release or traffic_departure or
                      self.start_request_frames >= self.standstill_release_frames)
    else:
      self.start_request_frames = 0
      self._reset_standstill_lead()
      start_gate = True

    self.long_control_state, planned_stop = long_control_state_trans(
      self.CP, active, self.long_control_state, CS.vEgo, v_target, v_target_1sec,
      CS.brakePressed, CS.cruiseState.standstill, soft_hold, a_target_now, start_gate)

    if self.long_control_state != LongCtrlState.stopping:
      self.standstill_hold_active = False

    if self.long_control_state == LongCtrlState.off:
      self.reset(CS.vEgo)
      output_accel = 0.

    elif self.long_control_state == LongCtrlState.stopping:
      # Arm only after an actual stop, then keep the stronger request latched
      # through tiny wheel-speed fluctuations.  This does not change braking
      # on the approach and it is cleared as soon as the state machine accepts
      # a genuine departure.
      if CS.standstill or CS.vEgo < 0.05:
        self.standstill_hold_active = True

      # sunnypilot 의 저크제한 적분기를 참고: 접근 중엔 목표를 0으로 두고
      # 실제 정지 확정 후에만 hold_target으로 바꾼다. 목표가 바뀌는 그
      # 순간에도 같은 저크 상한(stopping_decel_rate) 하나로 계속 이어서만
      # 움직이므로, 접근 램프가 덜 끝난 채로 서 버려도 standstill_hold_active
      # 가 켜지는 순간 추가로 한 번 더 밟는 계단현상이 생기지 않는다.
      # (기존 2단 구조: 접근램프 따로 + 정지 후 hold램프 따로 — 이 둘의
      # 목표가 어긋나 있으면 경계에서 겹쳐 밟혔다.)
      if self.standstill_hold_active and not CS.brakePressed:
        target = min(self.CP.stopAccel, self.standstill_hold_accel)
      else:
        target = 0.0
      if soft_hold:
        target = self.CP.stopAccel
      max_delta = self.stopping_decel_rate * DT_CTRL
      output_accel = float(clip(target, output_accel - max_delta, output_accel + max_delta))
      self.reset(CS.vEgo)

    elif self.long_control_state == LongCtrlState.starting:
      # 예전엔 startAccel 로 한 사이클(0.01초) 만에 순간 점프했다 — 정지
      # 유지값(음수)에서 출발목표(양수, 최대 2.0)로 바로 튀는 구조라, 정지쪽
      # 계단현상보다 더 급격할 수 있었다. 같은 저크 상한(stopping_decel_rate)
      # 으로 여기도 부드럽게 이어지게 한다.
      max_delta = self.stopping_decel_rate * DT_CTRL
      output_accel = float(clip(self.CP.startAccel,
                                output_accel - max_delta, output_accel + max_delta))
      self.reset(CS.vEgo)

    elif self.long_control_state == LongCtrlState.pid:
      self.v_pid = v_target_now

      # Freeze the integrator so we don't accelerate to compensate, and don't allow positive acceleration
      prevent_overshoot = not self.CP.stoppingControl and CS.vEgo < 1.5 and v_target_1sec < 0.7 and v_target_1sec < self.v_pid
      deadzone = interp(CS.vEgo, self.CP.longitudinalTuning.deadzoneBP, self.CP.longitudinalTuning.deadzoneV)
      freeze_integrator = prevent_overshoot

      error = self.v_pid - CS.vEgo
      error_deadzone = apply_deadzone(error, deadzone)
      output_accel = self.pid.update(error_deadzone, speed=CS.vEgo,
                                     feedforward=a_target,
                                     freeze_integrator=freeze_integrator)

    self.last_output_accel = clip(output_accel, accel_limits[0], accel_limits[1])

    return self.last_output_accel, -0.5 if planned_stop else j_target
