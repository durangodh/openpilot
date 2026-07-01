#!/usr/bin/env python3
import math
import numpy as np
from common.numpy_fast import clip, interp

import cereal.messaging as messaging
from cereal import car
from common.conversions import Conversions as CV
from common.filter_simple import FirstOrderFilter
from common.params import Params
from common.realtime import DT_MDL
from selfdrive.modeld.constants import T_IDXS
from selfdrive.controls.lib.longcontrol import LongCtrlState
from selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import LongitudinalMpc, MIN_ACCEL, MAX_ACCEL, N
from selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import T_IDXS as T_IDXS_MPC
from selfdrive.controls.lib.drive_helpers import V_CRUISE_MAX, CONTROL_N
from selfdrive.swaglog import cloudlog
from selfdrive.controls.lib.vision_turn_controller import VisionTurnController
from selfdrive.controls.lib.events import Events
# ── CarrotPilot Auto-Tuner (commit 9dd5e2c port) ──
from selfdrive.controls.lib.carrot_learning import CarrotLearner, read_learned_accel_vals, read_learned_tfollow

GearShifter = car.CarState.GearShifter

LON_MPC_STEP = 0.2  # first step is 0.2s
AWARENESS_DECEL = -0.2  # car smoothly decel at .2m/s^2 when user is distracted
A_CRUISE_MIN = -1.0
A_CRUISE_MAX_VALS = [1.8, 1.2, 0.8, 0.6]
A_CRUISE_MAX_BP = [0., 10., 25., 40.]

# ── Jerk ease-in (commit d897f06): 가감속 onset에서 jerk를 점증시켜 S-curve로 만든다 ──
# 일정 jerk 상한은 onset에서 jerk가 0→상한으로 '계단'처럼 튀어(=jounce 스파이크) 시작
# jolt를 남긴다. 시작 직후 jerk를 점증시키면 가속도가 S자로 부드럽게 붙는다.
JERK_EASE_TIME  = 0.4   # 새 maneuver 시작 후 jerk를 100%로 키우는 시간(s)
JERK_EASE_FLOOR = 0.3   # 시작 시 jerk 비율 하한(감속 onset 등)
# (commit dff7287) 가속은 선행차 추종 재가속(거리 좁히기)이 느리지 않게 시작 jerk를
# 더 높게 둔다. (감속보다 높은 floor → 초중반 가속력↑, 단 0에서 시작하는 ease 자체는
# 유지해 급가속감 방지)
JERK_EASE_FLOOR_ACCEL = 0.55
# (commit dff7287) 가속 ease-out: 가속을 마무리하며(현재 가속 중, 양의 가속을 0
# 근처로 줄이는 구간) 목표 차간거리에 살며시 도달하도록 부드러운 jerk를 쓴다
# (끝부분은 더 부드럽게 — 실제 제동이 아니므로).
ACCEL_EASEOUT_JERK = 1.2
# 고속 제동 안전 우회: 고속에서 선행차 접근 중이면 ease를 풀어(=즉응) 감지 초기부터
# 충분한 제동이 미리 들어가게 한다(고속 늦은 감지로 인한 충돌 우려 대응).
HIGH_SPEED_BRAKE_KPH = 70.0
HIGH_SPEED_BRAKE_TTC = 8.0  # 이 TTC(초) 이내로 접근 중이면 제동 ease 해제

# Lookup table for turns
_A_TOTAL_MAX_V = [1.7, 3.2]
_A_TOTAL_MAX_BP = [20., 40.]


def get_max_accel(v_ego):
  return interp(v_ego, A_CRUISE_MAX_BP, A_CRUISE_MAX_VALS)


def limit_accel_in_turns(v_ego, angle_steers, a_target, CP):
  """
  This function returns a limited long acceleration allowed, depending on the existing lateral acceleration
  this should avoid accelerating when losing the target in turns
  """

  # FIXME: This function to calculate lateral accel is incorrect and should use the VehicleModel
  # The lookup table for turns should also be updated if we do this
  a_total_max = interp(v_ego, _A_TOTAL_MAX_BP, _A_TOTAL_MAX_V)
  a_y = v_ego ** 2 * angle_steers * CV.DEG_TO_RAD / (CP.steerRatio * CP.wheelbase)
  a_x_allowed = math.sqrt(max(a_total_max ** 2 - a_y ** 2, 0.))

  return [a_target[0], min(a_target[1], a_x_allowed)]


class LongitudinalPlanner:
  def __init__(self, CP, init_v=0.0, init_a=0.0):
    self.CP = CP
    self.params = Params()
    self.param_read_counter = 0

    self.mpc = LongitudinalMpc()

    # ── Auto-Tuner: 학습기 + 학습된 가속 테이블 ──
    self.carrot_learner = CarrotLearner()
    self.learned_accel_vals = list(A_CRUISE_MAX_VALS)

    self.read_param()

    self.fcw = False

    self.a_desired = init_a
    # ── Jerk ease-in 상태 (commit d897f06) ──
    self._jerk_ramp_t = 0.0   # jerk ease-in 경과시간(새 가감속 시작부터)
    self._jerk_dir = 0        # 직전 가감속 방향(+1 가속 / -1 감속 / 0)
    self.v_desired_filter = FirstOrderFilter(init_v, 2.0, DT_MDL)

    self.v_desired_trajectory = np.zeros(CONTROL_N)
    self.a_desired_trajectory = np.zeros(CONTROL_N)
    self.j_desired_trajectory = np.zeros(CONTROL_N)
    self.solverExecutionTime = 0.0

    self.use_cluster_speed = Params().get_bool('UseClusterSpeed')
    self.cruise_source = 'cruise'
    self.vision_turn_controller = VisionTurnController(CP)
    self.events = Events()

  def read_param(self):
    e2e = self.params.get_bool('ExperimentalMode') and self.CP.openpilotLongitudinalControl
    self.mpc.mode = 'blended' if e2e else 'acc'
    self.mpc.human_following = self.params.get_bool("HumanFollowing")

    # ── Auto-Tuner: 학습된 파라미터를 planner/mpc에 반영 (5초 주기 갱신) ──
    if self.params.get_bool("CarrotLearningActive"):
      self.learned_accel_vals = read_learned_accel_vals(self.params)
      self.mpc.tfollow_gaps = read_learned_tfollow(self.params)
    else:
      self.learned_accel_vals = list(A_CRUISE_MAX_VALS)
      self.mpc.tfollow_gaps = None

  def get_max_accel_learned(self, v_ego):
    return interp(v_ego, A_CRUISE_MAX_BP, self.learned_accel_vals)

  def parse_model(self, model_msg):
    if (len(model_msg.position.x) == 33 and
       len(model_msg.velocity.x) == 33 and
       len(model_msg.acceleration.x) == 33):
      x = np.interp(T_IDXS_MPC, T_IDXS, model_msg.position.x)
      v = np.interp(T_IDXS_MPC, T_IDXS, model_msg.velocity.x)
      a = np.interp(T_IDXS_MPC, T_IDXS, model_msg.acceleration.x)
      j = np.zeros(len(T_IDXS_MPC))
    else:
      x = np.zeros(len(T_IDXS_MPC))
      v = np.zeros(len(T_IDXS_MPC))
      a = np.zeros(len(T_IDXS_MPC))
      j = np.zeros(len(T_IDXS_MPC))
    return x, v, a, j

  def update(self, sm, read=True):
    if self.param_read_counter % 100 == 0 and read:
      self.read_param()
    self.param_read_counter += 1

    v_ego = sm['carState'].vEgo

    v_cruise_kph = sm['controlsState'].vCruise
    v_cruise_kph = min(v_cruise_kph, V_CRUISE_MAX)
    v_cruise = v_cruise_kph * CV.KPH_TO_MS

    # neokii
    if not self.use_cluster_speed:
      vCluRatio = sm['carState'].vCluRatio
      if vCluRatio > 0.5:
        v_cruise *= vCluRatio
        v_cruise = int(v_cruise * CV.MS_TO_KPH + 0.25) * CV.KPH_TO_MS

    long_control_off = sm['controlsState'].longControlState == LongCtrlState.off
    force_slow_decel = sm['controlsState'].forceDecel

    # Reset current state when not engaged, or user is controlling the speed
    reset_state = long_control_off if self.CP.openpilotLongitudinalControl else not sm['controlsState'].enabled

    # No change cost when user is controlling the speed, or when standstill
    prev_accel_constraint = not (reset_state or sm['carState'].standstill)

    if self.mpc.mode == 'acc':
      # ── Auto-Tuner: 학습된 속도대역별 최대가속 사용 ──
      accel_limits = [A_CRUISE_MIN, self.get_max_accel_learned(v_ego)]
      accel_limits_turns = limit_accel_in_turns(v_ego, sm['carState'].steeringAngleDeg, accel_limits, self.CP)
    else:
      accel_limits = [MIN_ACCEL, MAX_ACCEL]
      accel_limits_turns = [MIN_ACCEL, MAX_ACCEL]

    if reset_state:
      self.v_desired_filter.x = v_ego
      # Clip aEgo to cruise limits to prevent large accelerations when becoming active
      self.a_desired = clip(sm['carState'].aEgo, accel_limits[0], accel_limits[1])
      self.mpc.prev_a = np.full(N+1, self.a_desired)  # pid off→on 전환시 constraint 튀는 문제 방지
      accel_limits_turns[0] = 0.0  # 재활성화 시 급감속 방지

    # Prevent divergence, smooth in current v_ego
    self.v_desired_filter.x = max(0.0, self.v_desired_filter.update(v_ego))

    # Get acceleration and active solutions for custom long mpc.
    self.cruise_source, a_min_sol, v_cruise_sol = self.cruise_solutions(not reset_state, self.v_desired_filter.x,
                                                                        self.a_desired, v_cruise, sm)

    if force_slow_decel:
      # if required so, force a smooth deceleration
      accel_limits_turns[1] = min(accel_limits_turns[1], AWARENESS_DECEL)
      accel_limits_turns[0] = min(accel_limits_turns[0], accel_limits_turns[1])
    # clip limits, cannot init MPC outside of bounds
    accel_limits_turns[0] = min(accel_limits_turns[0], self.a_desired + 0.05, a_min_sol)
    accel_limits_turns[1] = max(accel_limits_turns[1], self.a_desired - 0.05)

    self.mpc.set_accel_limits(accel_limits_turns[0], accel_limits_turns[1])
    self.mpc.set_cur_state(self.v_desired_filter.x, self.a_desired)
    x, v, a, j = self.parse_model(sm['modelV2'])
    self.mpc.update(sm['carState'], sm['radarState'], v_cruise_sol, x, v, a, j)

    self.v_desired_trajectory = np.interp(T_IDXS[:CONTROL_N], T_IDXS_MPC, self.mpc.v_solution)
    self.a_desired_trajectory = np.interp(T_IDXS[:CONTROL_N], T_IDXS_MPC, self.mpc.a_solution)
    self.j_desired_trajectory = np.interp(T_IDXS[:CONTROL_N], T_IDXS_MPC[:-1], self.mpc.j_solution)

    # TODO counter is only needed because radar is glitchy, remove once radar is gone
    self.fcw = self.mpc.crash_cnt > 2 and not sm['carState'].standstill and not reset_state
    if self.fcw:
      cloudlog.info("FCW triggered")

    # Interpolate 0.05 seconds and save as starting point for next iteration
    a_prev = self.a_desired
    a_target = float(interp(DT_MDL, T_IDXS[:CONTROL_N], self.a_desired_trajectory))
    v_ego_kph = v_ego * CV.MS_TO_KPH

    # ── Jerk ease-in (commit d897f06): 가감속 시작 시 jerk를 점증(S-curve)시켜 onset jolt 완화 ──
    # maneuver phase를 a_target '부호'로 판정하고 데드밴드(±0.15)로 미세 진동을 무시한다.
    # 가속↔감속 '전환'에서만 ramp를 재시작하므로, 지속 가감속에서는 jerk가 100%까지 자라
    # 약해지지 않는다(추종 가속/정지 감속이 더뎌지던 문제 해결).
    if a_target > 0.15:
      phase = 1
    elif a_target < -0.15:
      phase = -1
    else:
      phase = self._jerk_dir   # 데드밴드: 직전 phase 유지
    if phase != self._jerk_dir:
      self._jerk_ramp_t = 0.0
    self._jerk_dir = phase
    self._jerk_ramp_t += DT_MDL
    ease = float(np.clip(self._jerk_ramp_t / JERK_EASE_TIME, JERK_EASE_FLOOR, 1.0))

    if a_target > a_prev:
      if a_prev < 0.0:
        # 감속 해제(brake release): 선행차가 다시 멀어질 때 계속 감속하다 정지 직전
        # 급가속하는 문제를 막기 위해, 음수 가속도를 0으로 푸는 구간은 jerk를 크게 허용한다.
        max_positive_jerk = 3.0
      else:
        # (commit dff7287) 진짜 가속 build-up. 선행차 추종 재가속(거리 좁히기)이 느리지
        # 않도록 저속 jerk를 올리고(0.6→0.85) 가속 전용 ease floor(0.55)로 초중반
        # 가속력을 높인다(급가속감은 ease로 방지). 감속용 공용 ease가 아니라 가속
        # 전용 ease_acc(하한 JERK_EASE_FLOOR_ACCEL)를 사용한다.
        jerk_speed = float(np.interp(v_ego_kph, [0.0, 30.0, 80.0], [0.85, 1.15, 1.4]))
        jerk_accel = float(np.interp(a_prev, [0.0, 1.0], [1.0, 0.7]))
        ease_acc = float(np.clip(self._jerk_ramp_t / JERK_EASE_TIME, JERK_EASE_FLOOR_ACCEL, 1.0))
        max_positive_jerk = jerk_speed * jerk_accel * ease_acc
      a_target = min(a_target, a_prev + max_positive_jerk * DT_MDL)
    elif a_target < a_prev:
      if a_prev > 0.1 and a_target > -0.4:
        # (commit dff7287) 가속 ease-out: 가속 중 목표 차간거리에 가까워져 가속을
        # 0 근처로 거두는 구간은 실제 제동이 아니므로 부드러운 jerk로 살며시 마무리.
        max_negative_jerk = ACCEL_EASEOUT_JERK
      else:
        # ── 제동 진입(braking build-up) jerk 제한 (commit 1e95637, dff7287) ──
        # 선행차 감지 등으로 a_target이 한 스텝에 급강하할 때 초기 제동을 부드럽게 하여
        # '브레이크를 탁 밟는' 이질감을 완화한다. 단, 목표 감속이 깊을수록(긴급) 한도를
        # 키워 안전 제동은 그대로 확보한다.
        #   -1.2m/s^2(완만): 2.0m/s^3 → 0~-1.2까지 0.6s에 부드럽게
        #   -2.5m/s^2(강함): 5.0m/s^3
        #   -4.0m/s^2(긴급): 12.0m/s^3 → 사실상 무제한(0~-4까지 0.33s)
        max_negative_jerk = float(np.interp(a_target, [-4.0, -2.5, -1.2], [12.0, 5.0, 2.0]))
        # 제동 ease-in 비율 (commit d897f06): 기본은 가속과 같이 점증하되, 위급하면 풀어(=1.0) 즉응.
        ease_dec = ease
        # (commit dff7287) 깊은 감속 ease 해제 — 단 '고속에서만'(속도게이트 25→50kph).
        # 저속(≤25km/h)은 운동에너지가 낮아 깊은 감속도 부드럽게(ease 유지)해
        # 급브레이킹을 완화한다(30↓ 떨어질 때 급제동 완화, 뒤차 추돌 우려↓).
        # 고속 접근·정지 즉응 우회는 아래에서 그대로 유지.
        deep = float(np.interp(a_target, [-3.0, -1.5], [1.0, 0.0]))
        deep *= float(np.interp(v_ego_kph, [25.0, 50.0], [0.0, 1.0]))
        ease_dec = max(ease_dec, deep)
        # (2) 고속 + 선행차 접근(TTC 낮음): 늦은 감지 대비, 감지 초기부터 충분 제동이
        #     미리 들어가도록 ease를 완전히 해제한다(고속 충돌 우려 대응).
        if v_ego_kph >= HIGH_SPEED_BRAKE_KPH:
          try:
            lead = sm['radarState'].leadOne
            if lead.status and lead.dRel > 0.0 and lead.vRel < 0.0:
              ttc = lead.dRel / -lead.vRel
              if ttc < HIGH_SPEED_BRAKE_TTC:
                ease_dec = 1.0
          except Exception:
            pass
        # (3) 모델 정지 접근 시 제동을 ease 없이 즉응시켜 정지선 초과를 막는다.
        #     (이 포크의 modelV2에 action.shouldStop 가 없으면 except로 무시되어 무동작.
        #      원본 커밋의 carrot.xState==3(e2eStop) 체크는 이 포크에 carrot 객체가
        #      update()로 전달되지 않아 대응 항목 없음 — 포팅 시 필요하면 update(sm, carrot)
        #      로 시그니처 변경 후 추가 가능)
        try:
          if sm['modelV2'].action.shouldStop:
            ease_dec = 1.0
        except Exception:
          pass
        max_negative_jerk *= ease_dec
      a_target = max(a_target, a_prev - max_negative_jerk * DT_MDL)

    self.a_desired = a_target
    self.v_desired_filter.x = self.v_desired_filter.x + DT_MDL * (self.a_desired + a_prev) / 2.0

    # ── Auto-Tuner: 학습 데이터 수집 (commit 9dd5e2c carrot_functions 통합부 포팅) ──
    # Auto-Tuner는 비핵심 학습 기능이므로, 여기서 예외가 나도 안전필수
    # 종방향 플래너(plannerd)가 죽지 않도록 반드시 격리한다. (commit e06a7dd robustness)
    try:
      cs = sm['carState']
      lead = sm['radarState'].leadOne
      gear_park = cs.gearShifter == GearShifter.park
      engaged = sm['controlsState'].enabled
      cruise_gap = int(clip(cs.cruiseGap, 1., 4.)) if cs.cruiseGap > 0 else 4
      self.carrot_learner.set_current_gap(cruise_gap)
      # liveParameters.steerRatio (paramsd 칼만 추정) — steerRatio 학습 입력.
      # plannerd SubMaster에 'liveParameters'가 구독돼 있어야 한다(미구독/미수신 시 무시).
      sr_live, sr_valid = 0.0, False
      try:
        lp = sm['liveParameters']
        sr_live = float(lp.steerRatio)
        sr_valid = bool(getattr(lp, 'valid', True)) and (10.0 <= sr_live <= 20.0)
      except Exception:
        pass
      self.carrot_learner.update(
        v_ego_kph=v_ego * CV.MS_TO_KPH,
        gas_pressed=cs.gasPressed,
        engaged=engaged,
        gear_park=gear_park,
        steer_deg=cs.steeringAngleDeg,
        steer_pressed=cs.steeringPressed,
        brake_pressed=cs.brakePressed,
        lead_drel=lead.dRel if lead.status else 0.0,
        lead_v_kph=lead.vLead * CV.MS_TO_KPH if lead.status else 0.0,
        a_ego=cs.aEgo,
        v_cruise_kph=v_cruise_kph,
        gas_val=cs.gas,
        blinker=(cs.leftBlinker or cs.rightBlinker),
        steer_torque=cs.steeringTorque,
        steer_deg_corr=sm['controlsState'].angleSteers,
        steer_ratio_live=sr_live,
        steer_ratio_valid=sr_valid,
      )
    except Exception:
      cloudlog.exception("CarrotLearner update failed")

  def publish(self, sm, pm):
    plan_send = messaging.new_message('longitudinalPlan')

    plan_send.valid = sm.all_checks(service_list=['carState', 'controlsState'])

    longitudinalPlan = plan_send.longitudinalPlan
    longitudinalPlan.modelMonoTime = sm.logMonoTime['modelV2']
    longitudinalPlan.processingDelay = (plan_send.logMonoTime / 1e9) - sm.logMonoTime['modelV2']

    longitudinalPlan.speeds = self.v_desired_trajectory.tolist()
    longitudinalPlan.accels = self.a_desired_trajectory.tolist()
    longitudinalPlan.jerks = self.j_desired_trajectory.tolist()

    longitudinalPlan.hasLead = sm['radarState'].leadOne.status
    longitudinalPlan.longitudinalPlanSource = self.mpc.source if self.mpc.source != 'cruise' else self.cruise_source
    longitudinalPlan.visionTurnControllerState = self.vision_turn_controller.state
    longitudinalPlan.visionCurrentLatAcc = float(self.vision_turn_controller.current_lat_acc)
    longitudinalPlan.visionMaxPredLatAcc = float(self.vision_turn_controller.max_pred_lat_acc)
    longitudinalPlan.eventsDEPRECATED = self.events.to_msg()
    longitudinalPlan.fcw = self.fcw

    longitudinalPlan.solverExecutionTime = self.mpc.solve_time

    pm.send('longitudinalPlan', plan_send)

  def cruise_solutions(self, enabled, v_ego, a_ego, v_cruise, sm):
    # Update controllers
    self.vision_turn_controller.update(enabled, v_ego, a_ego, v_cruise, sm)
    self.events = Events()

    # Pick solution with lowest velocity target.
    a_solutions = {'cruise': float("inf")}
    v_solutions = {'cruise': v_cruise}

    if self.vision_turn_controller.is_active:
      a_solutions['turn'] = self.vision_turn_controller.a_target
      v_solutions['turn'] = self.vision_turn_controller.v_turn

    source = min(v_solutions, key=v_solutions.get)

    return source, a_solutions[source], v_solutions[source]
