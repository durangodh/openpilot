from cereal import car
from common.numpy_fast import clip, interp
from common.params import Params
from common.realtime import DT_CTRL
from selfdrive.controls.lib.drive_helpers import CONTROL_N, apply_deadzone
from selfdrive.controls.lib.pid import PIDController
from selfdrive.modeld.constants import T_IDXS
from selfdrive.controls.lib.lead_departure import (LeadDepartureAssist,
                                                   lead_is_departing)

LongCtrlState = car.CarControl.Actuators.LongControlState
ButtonType = car.CarState.ButtonEvent.Type

STANDSTILL_LEAD_MAX_DISTANCE = 20.0
STANDSTILL_LEAD_MAX_SPEED = 0.3
LEAD_RELEASE_CONFIRM_SAMPLES = 2
LEAD_DROPOUT_FALLBACK_FRAMES = round(1.5 / DT_CTRL)

# 정상주행(PID) 중 속도별 저크상한, m/s^3 (sunnypilot 참고). 정지/출발 전환의
# stopping_decel_rate 와는 별도 값 — 그쪽은 부드러움이 목적이라 낮고, 여긴
# 반응성도 같이 필요해서 훨씬 크다. 감속(LOWER)을 가속(UPPER)보다 크게 둬서
# 급제동 상황도 약 1초 안에 최대제동에 도달하게 한다(즉시반응보다는 느리지만
# 완전 무제한이던 예전보다 부드럽다 — 2026-09-20 사용자 확인 후 반영).
PID_JERK_SPEED_BP = [0.0, 5.0, 20.0]
PID_JERK_UPPER_V = [2.0, 3.0, 2.0]
PID_JERK_LOWER_V = [3.5, 3.5, 3.0]

# ---- 출발(정지 → 주행) ----
# 출발은 아래 세 단계뿐이고, starting 상태(START ACCEL > 0)를 거치든 바로
# PID로 가든 같은 규칙을 쓴다. 어느 단계도 새 제동 요청을 늦추지 않는다.
#  1) 제동 해제: 정지유지 음수 요청을 START_RELEASE_JERK로 0까지 푼다
#     (-1.1 m/s^2 홀드 기준 약 0.08초). 플래너가 다시 감속을 원하면 즉시
#     일반 PID 경로로 돌아간다.
#  2) 출발 보조: 앞차 출발이 확인된 창(LeadDepartureAssist, 최대 1초) 동안
#     PID 출력에 작은 하한을 둔다.
#  3) 출발 인계: 같은 창 안에서 양의 가속 요청이 줄어들 때만
#     START_HANDOFF_JERK로 천천히 줄인다. starting의 startAccel에서 PID로
#     넘어가는 순간 구동력이 툭 빠지는 것을 막는다. 0 이하 요청은 제외.
# SCC 쪽 출발 저크는 carcontroller의 START JERK LIMIT 하나로만 조절한다.
START_RELEASE_JERK = 14.0
START_HANDOFF_JERK = 1.6

# 저속 앞차출발 추종 전용 저크 부스트 구간. long_mpc.py의 LEAD_DEPARTURE_*
# (18~30km/h에서 서서히 해제)와 같은 구간을 써서, "계획단계는 빨리 붙으라는데
# 실행단계가 못 따라가는" 문제를 이 저속 구간에서만 별도로 풀어준다 —
# CRUISE JERK ACCEL(정상주행 전반)과는 무관하게 독립 조절.
LOW_SPEED_JERK_BOOST_SPEED_BP = [0.0, 5.0, 30.0 / 3.6]  # 0, 18, 30 km/h

# apilot-c2 상태전이.
# planned_stop 조건인데 accel 이 이미 stopAccel 보다 낮은 상태로 stopping 에 들어가면 너무 급하게 서므로
# a_target_now 가 -1.0 보다 커질 때까지(=제동이 완만해질 때까지) PID 를 유지한다. (apilot 2023-09-11)
def long_control_state_trans(CP, active, long_control_state, v_ego, v_target,
                             v_target_1sec, brake_pressed, cruise_standstill, soft_hold, a_target_now,
                             start_gate=True, lead_departure=False):
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
  starting_condition = ((v_target_1sec > CP.vEgoStarting or lead_departure) and
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

    # 지연보상 (LongitudinalActuatorDelayLowerBound, x100 저장, 기본 0.5초)
    self.actuator_delay = 0.5
    self.start_accel_apply = 0.0
    self.stop_accel_apply = 0.3
    self.stopping_decel_rate = CP.stoppingDecelRate
    # StopAccelApply controls the approach to zero speed.  A separate target
    # is needed after the car is fully stopped so a comfortable stop does not
    # slowly lose hydraulic hold during a long wait.
    self.standstill_hold_accel = -1.1
    self.standstill_hold_active = False
    # 정상주행(PID) 저크상한 배율. 기본 1.0(=코드 기본 속도별 곡선 그대로).
    self.pid_jerk_accel_mult = 1.0
    self.pid_jerk_decel_mult = 1.0
    # 출발(정지→가속 시작) 전용 저크, m/s^3 — 고정 상수. UI로 따로 안 뺐다:
    # hyundai/carcontroller.py 의 JerkStartLimit("START JERK LIMIT")이 CAN
    # 최종값을 이미 더 아래층에서 제한하고 있어서, 여기 계획 단계에 또
    # UI를 두면 조절할 게 두 개로 갈려 헷갈리기만 한다. 여기 값은 넉넉하게
    # 둬서 그 아래층 제한이 실질적인 병목이 되게 하고, 출발 체감 조절은
    # START JERK LIMIT 하나로 통일한다.
    self.start_jerk = 5.0
    # 저속(0~30km/h) 앞차출발 추종 전용 저크 부스트 배율. 기본 1.0(=부스트 없음).
    self.low_speed_jerk_boost = 1.0

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
    self.departure_release_active = False
    self.departure_assist = LeadDepartureAssist(DT_CTRL)

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
    # 예전 apilot-c2 이중지연(하한/상한 두 예측 중 작은 쪽)은 기본값이 둘 다
    # 0.5라 같은 계산을 두 번 하고 있었다. 지연 하나로 통일한다.
    try:
      delay = float(int(self.params.get("LongitudinalActuatorDelayLowerBound", encoding="utf8") or 0)) * 0.01
    except (TypeError, ValueError):
      delay = 0.0
    if delay <= 0.0:
      delay = self.CP.longitudinalActuatorDelayLowerBound if self.CP.longitudinalActuatorDelayLowerBound > 0.0 else 0.5
    self.actuator_delay = float(clip(delay, 0.1, 1.0))

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
          lead_moving = lead_is_departing(lead)
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

  @staticmethod
  def _lead_is_departing(radar_state, radar_state_valid):
    """True only while a valid lead is measurably pulling away from ego."""
    if (radar_state is None or not radar_state_valid or
        len(radar_state.radarErrors) != 0 or not radar_state.leadOne.status):
      return False
    return lead_is_departing(radar_state.leadOne)

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

    self.standstill_hold_accel = -2.0 * float(clip(hold_apply * 0.01, 0.1, 1.0))

  def _update_pid_jerk(self):
    try:
      accel_raw = self.params.get("PidJerkAccel", encoding="utf8")
      accel_mult = int(accel_raw) * 0.01 if accel_raw not in (None, "") else 1.0
    except (TypeError, ValueError):
      accel_mult = 1.0
    try:
      decel_raw = self.params.get("PidJerkDecel", encoding="utf8")
      decel_mult = int(decel_raw) * 0.01 if decel_raw not in (None, "") else 1.0
    except (TypeError, ValueError):
      decel_mult = 1.0

    self.pid_jerk_accel_mult = float(clip(accel_mult, 0.3, 3.0))
    self.pid_jerk_decel_mult = float(clip(decel_mult, 0.3, 3.0))

    try:
      boost_raw = self.params.get("LowSpeedJerkBoost", encoding="utf8")
      boost_mult = int(boost_raw) * 0.01 if boost_raw not in (None, "") else 1.0
    except (TypeError, ValueError):
      boost_mult = 1.0
    self.low_speed_jerk_boost = float(clip(boost_mult, 1.0, 5.0))

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
    elif self.read_param_count == 60:
      self._update_pid_jerk()

  def _release_brake(self, output_accel):
    """출발 1단계: 정지유지 제동을 일정한 저크로 0까지 푼다."""
    output_accel = min(0.0, output_accel + START_RELEASE_JERK * DT_CTRL)
    self.departure_release_active = output_accel < 0.0
    return output_accel

  def reset(self, v_pid=0.0):
    """Reset PID controller and change setpoint"""
    self.pid.reset()
    self.v_pid = v_pid

  def update(self, active, CS, long_plan, accel_limits, t_since_plan, soft_hold=False,
             radar_state=None, radar_state_valid=False, radar_state_updated=False,
             plan_valid=True):
    """Update longitudinal control. This updates the state machine and runs a PID loop"""
    self._read_params()

    # Interp control trajectory
    speeds = long_plan.speeds
    trajectory_valid = len(speeds) == CONTROL_N and len(long_plan.accels) == CONTROL_N
    if trajectory_valid:
      v_target_now = interp(t_since_plan, T_IDXS[:CONTROL_N], speeds)
      a_target_now = interp(t_since_plan, T_IDXS[:CONTROL_N], long_plan.accels)
      j_target = long_plan.jerks[0] if len(long_plan.jerks) else 0.0

      # 액추에이터 지연만큼 앞의 계획을 목표로 삼는다(원본 openpilot 방식).
      v_target = interp(self.actuator_delay + t_since_plan, T_IDXS[:CONTROL_N], speeds)
      a_target = 2 * (v_target - v_target_now) / self.actuator_delay - a_target_now

      v_target_1sec = interp(self.actuator_delay + t_since_plan + 1.0, T_IDXS[:CONTROL_N], speeds)
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
    lead_release = False
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

    assisted_departure = self.departure_assist.update(
      enabled=active and self.CP.openpilotLongitudinalControl,
      stopping=self.long_control_state == LongCtrlState.stopping,
      confirmed=lead_release, cs=CS, plan=long_plan, radar=radar_state,
      radar_valid=radar_state_valid, plan_valid=plan_valid and trajectory_valid,
      plan_age=t_since_plan, a_now=a_target_now, a_target=a_target,
      v_target=v_target, v_future=v_target_1sec, soft_hold=soft_hold)
    prev_long_control_state = self.long_control_state
    self.long_control_state, planned_stop = long_control_state_trans(
      self.CP, active, self.long_control_state, CS.vEgo, v_target, v_target_1sec,
      CS.brakePressed, CS.cruiseState.standstill, soft_hold, a_target_now, start_gate,
      assisted_departure)
    departed_stopping = (prev_long_control_state == LongCtrlState.stopping and
                         self.long_control_state != LongCtrlState.stopping)

    if self.long_control_state in (LongCtrlState.off, LongCtrlState.stopping):
      self.departure_release_active = False
    elif departed_stopping and output_accel < 0.0:
      self.departure_release_active = True

    if self.long_control_state != LongCtrlState.stopping:
      self.standstill_hold_active = False

    if self.long_control_state == LongCtrlState.off:
      self.reset(CS.vEgo)
      output_accel = 0.

    elif self.long_control_state == LongCtrlState.stopping:
      # A blocked state transition must not advertise a launch to the CAN layer.
      self.departure_assist.reset()
      # Arm only after an actual stop, then keep the stronger request latched
      # through tiny wheel-speed fluctuations.  This does not change braking
      # on the approach and it is cleared as soon as the state machine accepts
      # a genuine departure.
      if CS.standstill or CS.vEgo < 0.05:
        self.standstill_hold_active = True

      # sunnypilot 의 저크제한 적분기를 참고: 접근 중엔 목표를 stopAccel로
      # 유지(예전과 동일 — 이 값이 제동을 계속 단단히 유지시켜 밀림을 막는
      # 목적이라 0으로 풀면 안 됨), 실제 정지 확정 후에만 더 강한
      # hold_target으로 바꾼다. 목표가 바뀌는 그 순간에도 같은 저크
      # 상한(stopping_decel_rate) 하나로 계속 이어서만 움직이므로,
      # 접근 램프가 덜 끝난 채로 서 버려도 standstill_hold_active 가
      # 켜지는 순간 추가로 한 번 더 밟는 계단현상이 생기지 않는다.
      # (기존 2단 구조: 접근램프 따로 + 정지 후 hold램프 따로 — 이 둘의
      # 속도/목표가 어긋나 있으면 경계에서 겹쳐 밟혔다.)
      if self.standstill_hold_active and not CS.brakePressed:
        target = min(self.CP.stopAccel, self.standstill_hold_accel)
      else:
        target = self.CP.stopAccel
      if soft_hold:
        target = self.CP.stopAccel
      # Honor the configured stopping rate through both approach and hold.
      # The old low-speed multiplier turned a UI value of 1.2 into nearly
      # 3.0 m/s^3 precisely at the final stop. Normal PID braking and the
      # separate brake-release ramp retain their existing response.
      max_delta = self.stopping_decel_rate * DT_CTRL
      output_accel = float(clip(target, output_accel - max_delta, output_accel + max_delta))
      self.reset(CS.vEgo)

    elif self.long_control_state == LongCtrlState.starting:
      # 제동 해제(1단계) 후 start_jerk로 startAccel까지 올린다.
      if output_accel < 0.0:
        output_accel = self._release_brake(output_accel)
      else:
        self.departure_release_active = False
        max_delta = self.start_jerk * DT_CTRL
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
      pid_output = self.pid.update(error_deadzone, speed=CS.vEgo,
                                   feedforward=a_target,
                                   freeze_integrator=freeze_integrator)


      # 출발 보조(2단계)
      if assisted_departure and not prevent_overshoot:
        pid_output = max(pid_output, self.departure_assist.accel_floor)

      # sunnypilot 참고, 정상주행 전용 저크상한(정지/출발용 stopping_decel_rate
      # 와는 별도). 감속(jerk_lower)을 가속(jerk_upper)보다 크게 열어둬서
      # 급제동에도 어느 정도는 빠르게 반응하되, 완전 무제한(한 사이클 순간
      # 점프)은 아니게 한다.
      jerk_upper = interp(CS.vEgo, PID_JERK_SPEED_BP, PID_JERK_UPPER_V) * self.pid_jerk_accel_mult
      # 저속 앞차출발 추종 전용 부스트(long_mpc.py의 LEAD_DEPARTURE_* 와 같은
      # 저속 구간). CRUISE JERK ACCEL과는 별개로, 이 구간에서만 추가로
      # 곱해진다 — 정상주행(중~고속) 가속 체감엔 영향 없음.
      # Do not change unrelated low-speed acceleration. The extra multiplier
      # is active only while a valid lead is actually pulling away.
      departure_boost = (self.low_speed_jerk_boost
                         if self._lead_is_departing(radar_state, radar_state_valid)
                         else 1.0)
      jerk_upper *= interp(CS.vEgo, LOW_SPEED_JERK_BOOST_SPEED_BP,
                           [departure_boost, departure_boost, 1.0])
      jerk_lower = interp(CS.vEgo, PID_JERK_SPEED_BP, PID_JERK_LOWER_V) * self.pid_jerk_decel_mult
      # 양의 상한(CruiseMax, 앞차 없을 때 상한, 접근 시 상한)이 내려가도
      # 출력을 한 번에 자르지 않고 jerk_lower로 따라 내려가게 한다. 그래서
      # cruise_helper에 따로 있던 '가속 놓기' 완화가 필요 없다.
      pid_output = min(pid_output, accel_limits[1])
      # 출발 인계(3단계): 양의 요청이 줄어들 때만 완만하게.
      if assisted_departure and not prevent_overshoot and 0.0 < pid_output < output_accel:
        jerk_lower = min(jerk_lower, START_HANDOFF_JERK)
      # 제동 해제(1단계): START ACCEL=0이면 starting을 건너뛰고 여기로 온다.
      release = (self.departure_release_active and output_accel < 0.0 and
                 pid_output > 0.0 and not prevent_overshoot)
      if release:
        output_accel = self._release_brake(output_accel)
        self.reset(CS.vEgo)
      else:
        self.departure_release_active = False
        output_accel = float(clip(pid_output,
                                  output_accel - jerk_lower * DT_CTRL,
                                  output_accel + jerk_upper * DT_CTRL))

    pos_limit = accel_limits[1]
    if self.long_control_state == LongCtrlState.pid:
      # PID는 위에서 이미 상한을 향해 저크제한으로 내려가는 중이다.
      pos_limit = max(pos_limit, output_accel)
    self.last_output_accel = clip(output_accel, accel_limits[0], pos_limit)

    return self.last_output_accel, -0.5 if planned_stop else j_target
