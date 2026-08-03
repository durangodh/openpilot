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
from selfdrive.controls.lib.drive_helpers import V_CRUISE_MAX, CONTROL_N, get_accel_from_plan
from selfdrive.swaglog import cloudlog
from selfdrive.controls.lib.vision_turn_controller import VisionTurnController, VisionTurnControllerState
from selfdrive.controls.lib.events import Events
# ── CarrotPilot Auto-Tuner (commit 9dd5e2c port) ──
from selfdrive.controls.lib.carrot_learning import CarrotLearner, read_learned_accel_vals, read_learned_tfollow

GearShifter = car.CarState.GearShifter

LON_MPC_STEP = 0.2  # first step is 0.2s
AWARENESS_DECEL = -0.2  # car smoothly decel at .2m/s^2 when user is distracted
A_CRUISE_MIN = -1.0
A_CRUISE_MAX_VALS = [1.8, 1.2, 0.8, 0.6]
A_CRUISE_MAX_BP = [0., 10., 25., 40.]

# ── MyDrivingMode (1:ECO 2:SAFE 3:NORM 4:FAST) ────────────────────────────
# UI 의 모드 박스를 탭하면 1→2→3→4→1 로 순환한다 (onroad.cc).
# 갭버튼은 순정 SCC 갭 기능 그대로 두고, 모드는 그 위에 배율로만 얹는다.
#   ACCEL : 최대가속 배율 (감속 한계는 안전상 건드리지 않음)
#   TF    : GAP1~4 단계별 추종거리 위에 모드 배율 적용
MY_DRIVING_MODE_ACCEL = {1: 0.75, 2: 0.90, 3: 1.00, 4: 1.25}
MY_DRIVING_MODE_TF    = {1: 0.90, 2: 0.80, 3: 1.00, 4: 1.00}
# ──────────────────────────────────────────────────────────────────────────

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

    # ExperimentalMode enables automatic ACC/e2e selection.  Keep the mode in
    # ACC for normal cruising and temporarily use the blended cost when the
    # model is preparing to leave a model-predicted stop, when a distant stop
    # first needs to be planned, or when a vision-only lead is consistently
    # detected.  This mirrors apilot's TrafficStopMode switching without
    # depending on its vehicle-specific xState state machine.
    self.auto_e2e_enabled = False
    self.auto_e2e_stopping = False
    self.auto_e2e_prepare = False
    self.auto_e2e_vision_lead_count = 0

    # ── Auto-Tuner: 학습기 + 학습된 가속 테이블 ──
    self.carrot_learner = CarrotLearner()
    self.learned_accel_vals = list(A_CRUISE_MAX_VALS)

    # MyDrivingMode
    self.my_driving_mode = 3
    self.my_driving_mode_accel = 1.0

    self.read_param()

    self.fcw = False

    self.a_desired = init_a
    self.v_desired_filter = FirstOrderFilter(init_v, 2.0, DT_MDL)

    self.v_desired_trajectory = np.zeros(CONTROL_N)
    self.a_desired_trajectory = np.zeros(CONTROL_N)
    self.j_desired_trajectory = np.zeros(CONTROL_N)
    self.output_a_target = 0.0
    self.output_v_target_now = 0.0
    self.output_j_target_now = 0.0
    self.output_should_stop = False
    self.solverExecutionTime = 0.0

    self.use_cluster_speed = Params().get_bool('UseClusterSpeed')
    self.cruise_source = 'cruise'
    self.vision_turn_controller = VisionTurnController(CP)
    self.events = Events()

  def read_param(self):
    self.auto_e2e_enabled = self.params.get_bool('ExperimentalMode') and self.CP.openpilotLongitudinalControl
    if not self.auto_e2e_enabled:
      self.mpc.mode = 'acc'
    self.mpc.human_following = self.params.get_bool("HumanFollowing")

    # ── MyDrivingMode ──
    mode = self.params.get("MyDrivingMode", encoding='utf8')
    try:
      mode = int(mode)
    except (TypeError, ValueError):
      mode = 3
    if not 1 <= mode <= 4:
      mode = 3
    self.my_driving_mode = mode
    self.my_driving_mode_accel = MY_DRIVING_MODE_ACCEL[mode]
    self.mpc.driving_mode_tf = MY_DRIVING_MODE_TF[mode]
    gap_defaults = [110, 120, 140, 160]
    gap_values = []
    for key, default in zip(["TFollowGap1", "TFollowGap2", "TFollowGap3", "TFollowGap4"], gap_defaults):
      value = self.params.get_int(key)
      gap_values.append((value if value > 0 else default) * 0.01)
    self.mpc.tfollow_gaps = gap_values
    self.mpc.enable_speed_tf = self.params.get_int("EnableSpeedTF")
    decel_raw = self.params.get("TFollowDecelBoost", encoding='utf8')
    decel_boost = int(decel_raw) if decel_raw else 10
    self.mpc.t_follow_decel_boost = max(0, decel_boost) * 0.01
    # ───────────────────

    # ── Auto-Tuner: 학습된 파라미터를 planner/mpc에 반영 (5초 주기 갱신) ──
    if self.params.get_bool("CarrotLearningActive"):
      self.learned_accel_vals = read_learned_accel_vals(self.params)
      self.mpc.tfollow_gaps = read_learned_tfollow(self.params)
    else:
      self.learned_accel_vals = list(A_CRUISE_MAX_VALS)

  def update_auto_e2e_mode(self, car_state, radar_state, model_msg):
    if not self.auto_e2e_enabled:
      self.auto_e2e_stopping = False
      self.auto_e2e_prepare = False
      self.auto_e2e_vision_lead_count = 0
      return 'acc'

    model_valid = (len(model_msg.position.x) == 33 and
                   len(model_msg.position.y) == 33 and
                   len(model_msg.velocity.x) == 33)
    if not model_valid:
      self.auto_e2e_stopping = False
      self.auto_e2e_prepare = False
      self.auto_e2e_vision_lead_count = 0
      return 'acc'

    model_x = model_msg.position.x[-1]
    model_y = model_msg.position.y[-1]
    model_v0 = model_msg.velocity.x[0]
    model_v = model_msg.velocity.x[-1]
    v_ego_kph = car_state.vEgo * CV.MS_TO_KPH

    if v_ego_kph < 1.0:
      stop_sign = model_x < 20.0 and model_v < 10.0
    elif v_ego_kph < 80.0:
      stop_sign = model_x < 120.0 and (model_v < 3.0 or model_v < model_v0 * 0.7) and abs(model_y) < 5.0
    else:
      stop_sign = False
    start_sign = not stop_sign and (model_v > 5.0 or model_v > model_v0 + 2.0)

    if self.auto_e2e_stopping and (start_sign or car_state.gasPressed):
      self.auto_e2e_prepare = True
      self.auto_e2e_stopping = False
    if self.auto_e2e_prepare and (v_ego_kph > 5.0 and model_x > 60.0):
      self.auto_e2e_prepare = False
    if stop_sign:
      self.auto_e2e_stopping = True

    lead = radar_state.leadOne
    vision_lead = lead.status and lead.dRel < 90.0 and not lead.radar
    self.auto_e2e_vision_lead_count = self.auto_e2e_vision_lead_count + 1 if vision_lead else 0
    vision_lead_confirmed = self.auto_e2e_vision_lead_count * DT_MDL >= 0.5

    # apilot's default mode blends for a far model stop (>40 m) and while
    # preparing to depart; its experimental mix additionally blends for a
    # stable vision-only lead.
    far_model_stop = stop_sign and model_x > 40.0
    return 'blended' if self.auto_e2e_prepare or far_model_stop or vision_lead_confirmed else 'acc'

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

    self.mpc.mode = self.update_auto_e2e_mode(sm['carState'], sm['radarState'], sm['modelV2'])

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
      accel_limits = [A_CRUISE_MIN, self.get_max_accel_learned(v_ego) * self.my_driving_mode_accel]
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
    self.mpc.update(sm['carState'], sm['radarState'], v_cruise_sol, x, v, a, j,
                    prev_accel_constraint=prev_accel_constraint)

    self.v_desired_trajectory = np.interp(T_IDXS[:CONTROL_N], T_IDXS_MPC, self.mpc.v_solution)
    self.a_desired_trajectory = np.interp(T_IDXS[:CONTROL_N], T_IDXS_MPC, self.mpc.a_solution)
    self.j_desired_trajectory = np.interp(T_IDXS[:CONTROL_N], T_IDXS_MPC[:-1], self.mpc.j_solution)

    # TODO counter is only needed because radar is glitchy, remove once radar is gone
    self.fcw = self.mpc.crash_cnt > 2 and not sm['carState'].standstill and not reset_state
    if self.fcw:
      cloudlog.info("FCW triggered")

    # c3-wip output path: use the MPC trajectory directly.  The old extra
    # planner-side jerk/ease filters delayed legitimate acceleration and
    # braking and fought the MPC's own jerk cost.
    a_prev = self.a_desired
    self.a_desired = float(interp(DT_MDL, T_IDXS[:CONTROL_N], self.a_desired_trajectory))
    self.v_desired_filter.x = self.v_desired_filter.x + DT_MDL * (self.a_desired + a_prev) / 2.0

    actuator_delay = self.CP.longitudinalActuatorDelay
    configured_delay = self.params.get_float("LongActuatorDelay") * 0.01
    if configured_delay > 0.0:
      actuator_delay = configured_delay
    action_t = max(DT_MDL, actuator_delay + DT_MDL)
    self.output_a_target, self.output_should_stop, self.output_v_target_now, _ = get_accel_from_plan(
      self.v_desired_trajectory, self.a_desired_trajectory, T_IDXS[:CONTROL_N],
      action_t=action_t, v_ego_stopping=self.CP.vEgoStopping)
    self.output_j_target_now = float(self.j_desired_trajectory[0])

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
      # ── Auto-Tuner Phase 6: 비전 커브 감속 학습 입력 (VisionTurnController 상태) ──
      # cruise_solutions()에서 이미 매 프레임 vision_turn_controller.update()가
      # 호출되었으므로 여기서는 그 결과 상태만 읽는다.
      tvc = self.vision_turn_controller
      tvc_entering = tvc.state == VisionTurnControllerState.entering
      tvc_turning = tvc.state == VisionTurnControllerState.turning
      tvc_leaving = tvc.state == VisionTurnControllerState.leaving
      # ─────────────────────────────────────────────────────────────────────
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
        tvc_entering=tvc_entering,
        tvc_turning=tvc_turning,
        tvc_leaving=tvc_leaving,
        tvc_current_lat_acc=tvc.current_lat_acc,
        tvc_max_pred_lat_acc=tvc.max_pred_lat_acc,
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
    longitudinalPlan.tFollow = float(self.mpc.t_follow)
    longitudinalPlan.desiredDistance = float(self.mpc.desired_distance)
    longitudinalPlan.mpcMode = 1 if self.mpc.mode == 'blended' else 0
    longitudinalPlan.aTarget = float(self.output_a_target)
    longitudinalPlan.shouldStop = bool(self.output_should_stop)
    longitudinalPlan.vTargetNow = float(self.output_v_target_now)
    longitudinalPlan.jTargetNow = float(self.output_j_target_now)
    # Expose the automatic E2E stop/depart state to the onroad UI.
    # 0: inactive, 1: stopping/waiting, 2: preparing to depart.
    e2e_state_active = self.auto_e2e_enabled and sm['controlsState'].enabled
    longitudinalPlan.trafficState = (2 if self.auto_e2e_prepare else (1 if self.auto_e2e_stopping else 0)) if e2e_state_active else 0
    longitudinalPlan.onStop = bool(e2e_state_active and self.auto_e2e_stopping)
    longitudinalPlan.visionTurnControllerState = self.vision_turn_controller.state
    longitudinalPlan.visionTurnSpeed = float(self.vision_turn_controller.v_turn)   # m/s, UI vturn 표시용
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

