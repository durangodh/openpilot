from cereal import car
from common.numpy_fast import clip, interp
from common.params import Params
from common.realtime import DT_CTRL
from selfdrive.controls.lib.drive_helpers import CONTROL_N, apply_deadzone
from selfdrive.controls.lib.pid import PIDController
from selfdrive.modeld.constants import T_IDXS
from selfdrive.controls.lib.lead_departure import (LeadDepartureAssist,
                                                   departure_jerk_upper,
                                                   lead_departure_jerk,
                                                   lead_is_creeping,
                                                   lead_is_departing,
                                                   lead_raw_departing)

LongCtrlState = car.CarControl.Actuators.LongControlState
ButtonType = car.CarState.ButtonEvent.Type

STANDSTILL_LEAD_MAX_DISTANCE = 20.0
STANDSTILL_LEAD_MAX_SPEED = 0.3
LEAD_RELEASE_CONFIRM_SAMPLES = 2
LEAD_DROPOUT_FALLBACK_FRAMES = round(1.5 / DT_CTRL)

# ---- 앞차 출발 대기 중 빨리 출발하기 (2026-10-03 long_trace 분석) ----
# 앞차가 움직인 뒤 우리 차가 구르기까지 약 2초: 출발 판정 0.35초 + 정지유지
# 제동(-1.2) 풀기 0.33초 + 차량 SCC 해제 1.3초. 앞의 두 단계를 줄인다.
# A) EarlyHoldRelax: 앞차가 꿈틀하는 첫 신호부터 정지유지 제동을 정차 접근
#    수준(stopAccel)으로 미리 줄인다. StopReq 는 계속 1 이라 차는 서 있다.
#    신호가 0.5초 끊기거나 2초가 지나도 출발이 안 되면 원래 유지 제동으로 돌아간다.
# B) FastLeadRelease: 반응이 느린 필터 속도(vLeadK) 대신 레이더 원래 속도로도
#    앞차 출발을 확인한다(레이더 앞차·새 샘플 2회 연속은 그대로).
HOLD_RELAX_KEEP_FRAMES = round(0.5 / DT_CTRL)
HOLD_RELAX_MAX_FRAMES = round(2.0 / DT_CTRL)
HOLD_RELAX_TARGET_MAX = -0.5   # stopAccel 이 0(끔)이어도 이만큼은 제동을 남긴다
HOLD_RELAX_JERK = 2.5          # m/s^3, 유지 제동을 줄이는 속도(다시 늘릴 때는 stopping_decel_rate)
# 2026-10-03 사용자: 정지 시 브레이크가 조금씩 풀려 찔끔찔끔 나간다. 기록에서 차가
# 완전히 서기 직전(0.04 m/s)에 앞차가 꿈틀하자 유지 제동을 바로 풀어 더 굴러갔다.
#  - 완전히 선 뒤 1초가 지나야(유지 제동이 걸린 뒤에만) 미리 풀기를 허용한다.
#  - 정차 접근 수준(stopAccel)까지 다 풀지 않고 유지 제동과의 중간까지만 푼다.
HOLD_RELAX_SETTLE_FRAMES = round(1.0 / DT_CTRL)
HOLD_RELAX_FRACTION = 0.5
# C) EarlyStopReqRelease (실험, 기본 꺼짐): 앞차가 꿈틀하면 브레이크 명령(음수)은 그대로
#    두고 Hyundai StopReq 만 먼저 내린다. 2026-10-03 기록에서 정지유지(StopReq)가 걸린
#    상태의 출발은 명령 후 약 1.25초, 걸리기 전에는 0.8초였다. 0.8초 안에 실제 출발이
#    이어지지 않거나 차가 조금이라도 움직이면 StopReq 를 다시 올리고, 그 정지 동안은
#    다시 시도하지 않는다(움직였을 때). 완전히 선 뒤 1초가 지나야 동작한다.
STOPREQ_RELEASE_KEEP_FRAMES = round(0.8 / DT_CTRL)

# 줄인 동안 차가 조금이라도 움직이면(크립) 즉시 유지 제동으로 빠르게 돌아가고, 그 정지
# 동안에는 다시 풀지 않는다. 앞차 레이더 속도가 튈 때마다 풀렸다 잡혔다 하며 찔끔찔끔
# 나가는 것을 막는다.
HOLD_RESTORE_JERK = 3.0

# ---- 출발(정지 → 주행) ----
# 출발 가속은 전부 여기서만 다룬다(SCC14는 넉넉한 고정값만 보낸다).
# starting 상태(START ACCEL > 0)를 거치든 바로 PID로 가든 같은 규칙이다.
#  1) 출발 저크: 정지(또는 해제) 후 LAUNCH_TIME_BP 동안은 가속 요청이
#     오르는 속도를 START JERK LIMIT(JerkStartLimit)으로 제한하고, 그 뒤
#     LAUNCH_JERK_MAX까지 서서히 푼다. 정지유지 제동 해제부터 실제 가속까지
#     한 저크로 이어진다. 내려가는 쪽(새 제동 요청)에는 적용하지 않는다.
#  2) 출발 보조: 앞차 출발이 확인된 창(LeadDepartureAssist, 최대 1초) 동안
#     PID 출력에 작은 하한을 두고, 출발 저크를 departure_jerk_upper만큼
#     올려준다.
#  3) 출발 인계: 같은 창 안에서 양의 가속 요청이 줄어들 때만
#     START_HANDOFF_JERK로 천천히 줄인다(startAccel → PID 전환 시 구동력이
#     툭 빠지는 것 방지). 0 이하 요청은 제외.
LAUNCH_TIME_BP = [0.0, 1.5, 2.5]   # s after leaving stop/off
LAUNCH_JERK_MAX = 5.0              # m/s^3, same as the SCC14 ceiling
LEAD_JERK_RISE_RATE = 2.0          # m/s^4, make stronger launches progressively
LEAD_JERK_FALL_RATE = 4.0          # m/s^4, lift faster when the lead eases
# This remains a release ceiling for compatibility; confirmed lead launches
# are capped further from live lead speed, acceleration and available gap.
# Launches without a lead (green light, driver) keep START JERK LIMIT.
LEAD_LAUNCH_JERK = 2.5

# Ease only the final rolling approach. The original stop/hold targets and
# StopReq behavior remain unchanged once standstill is detected.
FINAL_STOP_TAPER_SPEED = 0.3
FINAL_STOP_TAPER_ACCEL = -0.6

# A lead can move just far enough to release standstill and then stop again.
# In that case the starting state has already stepped the request to
# startAccel, so the normal comfort stop ramp would keep positive drive torque
# for well over a second.  Drop propulsion immediately and use the normal PID
# braking response to rebuild the stop request.  This applies only to an
# aborted starting -> stopping transition; ordinary approaches keep the
# configured stopping_decel_rate.
LAUNCH_ABORT_DECEL_JERK = 3.5

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
    # START JERK LIMIT (JerkStartLimit, x0.1 m/s^3, 기본 1.0)
    self.jerk_start_limit = 1.0
    self.launch_time = 0.0
    self.launch_motion_started = False
    self.launch_limited = False
    self.lead_launch = False
    self.lead_launch_jerk = None
    self.launch_abort_active = False

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
    self.departure_assist = LeadDepartureAssist(DT_CTRL)
    self.early_hold_relax = True
    self.fast_lead_release = True
    self.hold_relax_left = 0
    self.hold_relax_used = 0
    self.standstill_frames = 0
    self.hold_relaxing = False
    self.hold_restore_fast = False
    self.early_stopreq_release = False
    self.stopreq_release_left = 0
    self.stopreq_release_blocked = False
    self.stopreq_release_active = False
    self._update_early_start()

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

  def _update_early_start(self):
    """EarlyHoldRelax / FastLeadRelease (기본 켜짐)."""
    try:
      self.early_hold_relax = self.params.get("EarlyHoldRelax", encoding="utf8") != "0"
      self.fast_lead_release = self.params.get("FastLeadRelease", encoding="utf8") != "0"
      self.early_stopreq_release = self.params.get("EarlyStopReqRelease", encoding="utf8") == "1"
    except Exception:
      pass

  def _reset_standstill_lead(self):
    self.standstill_lead_latched = False
    self.lead_release_samples = 0
    self.lead_measurement_available = False
    self.lead_missing_frames = 0
    self.hold_relax_left = 0
    self.hold_relax_used = 0
    self.stopreq_release_left = 0
    self.stopreq_release_blocked = False
    self.stopreq_release_active = False

  def _hold_relax_active(self):
    """A) 정지 앞차가 꿈틀하는 동안 정지유지 제동을 미리 줄일지."""
    return (getattr(self, 'early_hold_relax', False) and self.standstill_lead_latched and
            getattr(self, 'standstill_frames', 0) >= HOLD_RELAX_SETTLE_FRAMES and
            getattr(self, 'hold_relax_left', 0) > 0 and
            getattr(self, 'hold_relax_used', 0) < HOLD_RELAX_MAX_FRAMES)

  def _update_standstill_lead(self, radar_state, radar_state_valid, radar_state_updated,
                              ego_standstill=False):
    """Latch a stopped lead and release only after fresh samples confirm it is moving.

    A transient missing/stale radar sample never opens the gate. If radar remains
    unavailable for 1.5 seconds, the caller falls back to the sustained planner
    request so a permanent radar outage cannot disable automatic launch.
    """
    # A) 꿈틀 신호 유지 시간은 매 프레임 줄고, 줄인 상태로 머문 시간은 누적한다.
    if getattr(self, 'hold_relax_left', 0) > 0:
      self.hold_relax_left -= 1
      self.hold_relax_used = getattr(self, 'hold_relax_used', 0) + 1
    if getattr(self, 'stopreq_release_left', 0) > 0:
      self.stopreq_release_left -= 1
    if radar_state_updated:
      lead_valid = (radar_state is not None and radar_state_valid and
                    len(radar_state.radarErrors) == 0 and radar_state.leadOne.status)
      self.lead_measurement_available = lead_valid
      if not lead_valid:
        self.lead_release_samples = 0
      else:
        lead = radar_state.leadOne
        if not self.standstill_lead_latched:
          near = 0.0 < lead.dRel <= STANDSTILL_LEAD_MAX_DISTANCE
          stopped_lead = near and abs(lead.vLeadK) <= STANDSTILL_LEAD_MAX_SPEED and \
                         abs(lead.vRel) <= STANDSTILL_LEAD_MAX_SPEED
          # Once ego is actually stopped, any near lead that is not pulling
          # away is the car we wait for, even if its speed estimate jitters
          # above 0.3 m/s. Otherwise the planner's small gap-closing request
          # released the hold and re-stopped (brake "tok-tok" / creeping).
          stopped_lead = stopped_lead or (ego_standstill and near and not lead_is_departing(lead))
          if stopped_lead:
            self.standstill_lead_latched = True
        else:
          lead_moving = lead_is_departing(lead) or \
                        (getattr(self, 'fast_lead_release', False) and lead_raw_departing(lead))
          self.lead_release_samples = self.lead_release_samples + 1 if lead_moving else 0
          if lead_moving or lead_is_creeping(lead):
            self.hold_relax_left = HOLD_RELAX_KEEP_FRAMES
            # C) 완전히 선 뒤 1초가 지났고, 이번 정지에서 막히지 않았으면 StopReq 를 먼저 내린다.
            if (getattr(self, 'early_stopreq_release', False) and
                not getattr(self, 'stopreq_release_blocked', False) and
                getattr(self, 'standstill_frames', 0) >= HOLD_RELAX_SETTLE_FRAMES):
              self.stopreq_release_left = STOPREQ_RELEASE_KEEP_FRAMES
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

    self.standstill_hold_accel = -2.0 * float(clip(hold_apply * 0.01, 0.1, 1.0))

  def _update_launch_jerk(self):
    try:
      start_raw = self.params.get("JerkStartLimit", encoding="utf8")
      start_jerk = int(start_raw) * 0.1 if start_raw not in (None, "") else 0.0
    except (TypeError, ValueError):
      start_jerk = 0.0
    self.jerk_start_limit = float(clip(
      start_jerk if start_jerk > 0.0 else 1.0, 0.5, LAUNCH_JERK_MAX))

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
      self._update_early_start()
      self._update_stopping_decel_rate()
      self._update_standstill_hold()
    elif self.read_param_count == 60:
      self._update_launch_jerk()

  def _launch_jerk(self, assisted):
    """출발 1·2단계: 정지 후 가속 요청이 오를 수 있는 최대 저크."""
    releasing = self.lead_launch and not self.launch_motion_started
    start = max(self.jerk_start_limit, LEAD_LAUNCH_JERK) if releasing else self.jerk_start_limit
    limit = interp(self.launch_time, LAUNCH_TIME_BP, [start, start, LAUNCH_JERK_MAX])
    if self.lead_launch and self.launch_motion_started:
      result = limit
    else:
      result = departure_jerk_upper(limit, self.jerk_start_limit,
                                    2.0, assisted)
    if self.lead_launch and self.lead_launch_jerk is not None:
      result = min(result, self.lead_launch_jerk)
    return result

  def scc_launch_jerk(self):
    """SCC release follows live lead motion; other launches use START JERK LIMIT.

    The 2.5-second ramp begins with actual motion, not the release command.
    """
    if self.long_control_state == LongCtrlState.starting or \
       (self.long_control_state == LongCtrlState.pid and 0.0 < self.launch_time < LAUNCH_TIME_BP[-1]):
      releasing = self.lead_launch and not self.launch_motion_started
      start = max(self.jerk_start_limit, LEAD_LAUNCH_JERK) if releasing else self.jerk_start_limit
      limit = float(interp(self.launch_time, LAUNCH_TIME_BP, [start, start, LAUNCH_JERK_MAX]))
      if self.lead_launch and self.lead_launch_jerk is not None:
        limit = min(limit, self.lead_launch_jerk)
      return limit
    return None

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
      lead_release = self._update_standstill_lead(radar_state, radar_state_valid, radar_state_updated,
                                                  CS.standstill or CS.vEgo < 0.05)
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
      v_target=v_target, v_future=v_target_1sec, soft_hold=soft_hold,
      fast_raw=getattr(self, 'fast_lead_release', False))
    prev_state = self.long_control_state
    self.long_control_state, planned_stop = long_control_state_trans(
      self.CP, active, self.long_control_state, CS.vEgo, v_target, v_target_1sec,
      CS.brakePressed, CS.cruiseState.standstill, soft_hold, a_target_now, start_gate,
      assisted_departure)
    launch_abort = (prev_state == LongCtrlState.starting and
                    self.long_control_state == LongCtrlState.stopping)
    if launch_abort:
      self.launch_abort_active = True
    elif self.long_control_state != LongCtrlState.stopping:
      self.launch_abort_active = False
    if self.long_control_state in (LongCtrlState.off, LongCtrlState.stopping):
      self.launch_time = 0.0
      self.launch_motion_started = False
      self.launch_limited = False
      self.lead_launch = False
      self.lead_launch_jerk = None
    else:
      if prev_state == LongCtrlState.stopping:
        # Remember why this launch started: a confirmed departing lead.
        self.lead_launch = lead_release
      # SCC can take over a second to release its standstill latch. Do not
      # spend the launch protection window while the car is still stationary.
      if not CS.standstill and CS.vEgo > self.CP.vEgoStarting:
        self.launch_motion_started = True
      if self.launch_motion_started:
        self.launch_time += DT_CTRL
      else:
        self.launch_time = DT_CTRL

      # Match a human driver's launch pressure to what the lead is actually
      # doing. Rising response is deliberately gradual; reductions happen
      # faster so a lead that eases off never leaves stale launch aggression.
      if self.lead_launch:
        lead = radar_state.leadOne if (radar_state is not None and radar_state_valid and
                                       not radar_state.radarErrors) else None
        target_jerk = lead_departure_jerk(
          lead, self.jerk_start_limit, float(getattr(long_plan, 'desiredDistance', 0.0)))
        if target_jerk is None:
          target_jerk = min(self.jerk_start_limit, 1.0)
        if self.lead_launch_jerk is None:
          self.lead_launch_jerk = target_jerk
        else:
          self.lead_launch_jerk = float(clip(
            target_jerk,
            self.lead_launch_jerk - LEAD_JERK_FALL_RATE * DT_CTRL,
            self.lead_launch_jerk + LEAD_JERK_RISE_RATE * DT_CTRL))

    if self.long_control_state != LongCtrlState.stopping:
      self.standstill_hold_active = False
      self.standstill_frames = 0
      self.hold_relaxing = False
      self.hold_restore_fast = False
      self.stopreq_release_active = False

    if self.long_control_state == LongCtrlState.off:
      self.reset(CS.vEgo)
      output_accel = 0.

    elif self.long_control_state == LongCtrlState.stopping:
      # A blocked state transition must not advertise a launch to the CAN layer.
      self.departure_assist.reset()
      # If a departing lead immediately stops again, do not spend the comfort
      # stopping ramp continuing to request positive acceleration.  Remove
      # drive torque in this frame, then build braking at the regular low-speed
      # PID deceleration jerk.  SCC14 still applies its stopping jerk limits.
      if launch_abort:
        output_accel = min(output_accel, 0.0)
      # Arm only after an actual stop, then keep the stronger request latched
      # through tiny wheel-speed fluctuations.  This does not change braking
      # on the approach and it is cleared as soon as the state machine accepts
      # a genuine departure.
      if CS.standstill or CS.vEgo < 0.05:
        self.standstill_hold_active = True
      # 실제로 서 있는 시간(미리 풀기 허용 조건). 조금이라도 구르면 다시 센다.
      if CS.standstill or CS.vEgo < 0.01:
        self.standstill_frames = getattr(self, 'standstill_frames', 0) + 1
      else:
        if getattr(self, 'hold_relaxing', False):
          self.hold_relax_used = HOLD_RELAX_MAX_FRAMES   # 이번 정지 동안 미리 풀기 금지
          self.hold_restore_fast = True
        if getattr(self, 'stopreq_release_active', False):
          self.stopreq_release_blocked = True             # 움직였으면 이번 정지 동안 C 금지
          self.stopreq_release_left = 0
        self.standstill_frames = 0
      self.stopreq_release_active = (self.standstill_lead_latched and not CS.brakePressed and
                                     not soft_hold and getattr(self, 'stopreq_release_left', 0) > 0)

      # sunnypilot 의 저크제한 적분기를 참고: 접근 중엔 목표를 stopAccel로
      # 유지(예전과 동일 — 이 값이 제동을 계속 단단히 유지시켜 밀림을 막는
      # 목적이라 0으로 풀면 안 됨), 실제 정지 확정 후에만 더 강한
      # hold_target으로 바꾼다. 목표가 바뀌는 그 순간에도 같은 저크
      # 상한(stopping_decel_rate) 하나로 계속 이어서만 움직이므로,
      # 접근 램프가 덜 끝난 채로 서 버려도 standstill_hold_active 가
      # 켜지는 순간 추가로 한 번 더 밟는 계단현상이 생기지 않는다.
      # (기존 2단 구조: 접근램프 따로 + 정지 후 hold램프 따로 — 이 둘의
      # 속도/목표가 어긋나 있으면 경계에서 겹쳐 밟혔다.)
      hold_relax = False
      if self.standstill_hold_active and not CS.brakePressed:
        target = min(self.CP.stopAccel, self.standstill_hold_accel)
        # A) 앞차가 꿈틀하면 유지 제동을 정차 접근 수준으로 미리 줄인다(StopReq 유지).
        if self._hold_relax_active():
          relaxed = min(self.CP.stopAccel, HOLD_RELAX_TARGET_MAX)
          # 2026-10-06 long_trace: STOP ACCEL 이 유지 제동만큼 세면(둘 다 -1.4) 줄일 곳이
          # 없어 앞차가 꿈틀해도 -1.4 그대로였다. 그때는 HOLD_RELAX_TARGET_MAX 쪽으로 줄인다.
          if relaxed <= target + 0.05:
            relaxed = HOLD_RELAX_TARGET_MAX
          target = target + (relaxed - target) * HOLD_RELAX_FRACTION
          hold_relax = True
      else:
        target = self.CP.stopAccel
      if soft_hold:
        target = self.CP.stopAccel
        hold_relax = False
      elif (not self.standstill_hold_active and not CS.brakePressed and
            0.05 < CS.vEgo < FINAL_STOP_TAPER_SPEED and
            not self.launch_abort_active):
        # Avoid building stronger brake just before zero speed; the existing
        # standstill hold ramp takes over after the car actually stops.
        target = max(target, FINAL_STOP_TAPER_ACCEL)
      # Honor the configured stopping rate through both approach and hold.
      # The old low-speed multiplier turned a UI value of 1.2 into nearly
      # 3.0 m/s^3 precisely at the final stop. Normal PID braking and the
      # separate brake-release ramp retain their existing response.
      max_delta = self.stopping_decel_rate * DT_CTRL
      max_rise = max(max_delta, HOLD_RELAX_JERK * DT_CTRL) if hold_relax else max_delta
      launch_abort_active = self.launch_abort_active
      max_fall = max(max_delta, LAUNCH_ABORT_DECEL_JERK * DT_CTRL) if launch_abort_active else max_delta
      if getattr(self, 'hold_restore_fast', False):
        max_fall = max(max_fall, HOLD_RESTORE_JERK * DT_CTRL)
        if output_accel <= target + 1e-6:
          self.hold_restore_fast = False
      self.hold_relaxing = hold_relax
      output_accel = float(clip(target, output_accel - max_fall, output_accel + max_rise))
      if launch_abort_active and output_accel <= target + 1e-6:
        self.launch_abort_active = False
      self.reset(CS.vEgo)

    elif self.long_control_state == LongCtrlState.starting:
      start_target = float(clip(self.CP.startAccel, accel_limits[0], accel_limits[1]))
      if getattr(self.CP, 'hasScc14', False):
        # SCC14-capable cars receive the launch jerk separately, so release the
        # hold immediately and let the vehicle ECU shape positive acceleration.
        output_accel = start_target
        self.launch_limited = False
      else:
        # SCC12 has no jerk fields. Drop the braking request immediately to
        # release standstill, but rate-limit positive drive locally instead of
        # stepping straight to StartAccel in one control frame.
        launch_jerk = self._launch_jerk(assisted_departure)
        output_accel = min(start_target, max(0.0, output_accel) + launch_jerk * DT_CTRL)
        self.launch_limited = output_accel + 1e-6 < start_target
      self.reset(CS.vEgo)

    elif self.long_control_state == LongCtrlState.pid:
      self.v_pid = v_target_now

      # Freeze the integrator so we don't accelerate to compensate, and don't allow positive acceleration
      prevent_overshoot = not self.CP.stoppingControl and CS.vEgo < 1.5 and v_target_1sec < 0.7 and v_target_1sec < self.v_pid
      deadzone = interp(CS.vEgo, self.CP.longitudinalTuning.deadzoneBP, self.CP.longitudinalTuning.deadzoneV)
      # 출발 저크에 막혀 출력이 못 따라가는 동안 적분이 쌓이지 않게 한다.
      freeze_integrator = prevent_overshoot or self.launch_limited

      error = self.v_pid - CS.vEgo
      error_deadzone = apply_deadzone(error, deadzone)
      pid_output = self.pid.update(error_deadzone, speed=CS.vEgo,
                                   feedforward=a_target,
                                   freeze_integrator=freeze_integrator)


      # apilot-c2: execute the PID result directly. The MPC already prices
      # acceleration and jerk; SCC14 remains the single command-side jerk owner.
      self.launch_limited = False
      output_accel = pid_output

    self.last_output_accel = clip(output_accel, accel_limits[0], accel_limits[1])

    return self.last_output_accel, -0.5 if planned_stop else j_target
