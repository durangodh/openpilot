#!/usr/bin/env python3
import math
import numpy as np
from common.numpy_fast import clip, interp

import cereal.messaging as messaging
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
from selfdrive.controls.lib.events import Events
from selfdrive.controls.lib.conditional_e2e import (ConditionalE2EController, E2E_VISION_LEAD_DISTANCE,
                                                    adjust_stop_distance_for_decel)

LON_MPC_STEP = 0.2  # first step is 0.2s
AWARENESS_DECEL = -0.2  # car smoothly decel at .2m/s^2 when user is distracted
A_CRUISE_MIN = -1.2

A_CRUISE_MAX_BP = [0.0, 40.0 * CV.KPH_TO_MS, 60.0 * CV.KPH_TO_MS,
                   80.0 * CV.KPH_TO_MS, 110.0 * CV.KPH_TO_MS, 140.0 * CV.KPH_TO_MS]
CRUISE_MAX_VAL_KEYS = ["CruiseMaxVals1", "CruiseMaxVals2", "CruiseMaxVals3",
                       "CruiseMaxVals4", "CruiseMaxVals5", "CruiseMaxVals6"]

# Limit only ACC re-acceleration after a brake release. Standstill/low-speed
# launches and E2E operation keep their existing acceleration behavior.
BRAKE_RESUME_ACCEL_TIME = 3.0
BRAKE_RESUME_MIN_SPEED_KPH = 20.0
BRAKE_RESUME_MIN_SPEED_GAP_KPH = 5.0
BRAKE_RESUME_ACCEL_BP = [20.0, 40.0, 60.0]
BRAKE_RESUME_ACCEL_V = [1.20, 1.00, 0.80]
CRUISE_MAX_VAL_DEFAULTS = [1.60, 1.20, 1.00, 0.80, 0.70, 0.60]

# ── MyDrivingMode (1:ECO 2:SAFE 3:NORM 4:FAST) ────────────────────────────
# UI 의 모드 박스를 탭하면 1→2→3→4→1 로 순환한다 (onroad.cc).
# 갭버튼은 순정 SCC 갭 기능 그대로 두고, 모드는 그 위에 배율로만 얹는다.
#   ACCEL : MyEcoModeFactor와 MySafeModeFactor로 계산 (감속 한계는 유지)
# ──────────────────────────────────────────────────────────────────────────

# carrot-wip future-curvature acceleration limiter
TURN_CURVATURE_LOOKAHEAD = 1.0
TURN_CURVATURE_MIN_SPEED = 3.0


def get_future_curvature(model_msg, fallback_curvature, lookahead=TURN_CURVATURE_LOOKAHEAD):
  if (len(model_msg.orientationRate.z) != len(T_IDXS) or
      len(model_msg.velocity.x) != len(T_IDXS)):
    return fallback_curvature

  yaw_rate_future = float(np.interp(lookahead, T_IDXS, model_msg.orientationRate.z))
  velocity_future = float(np.interp(lookahead, T_IDXS, model_msg.velocity.x))
  if not (np.isfinite(yaw_rate_future) and np.isfinite(velocity_future)):
    return fallback_curvature
  return yaw_rate_future / max(abs(velocity_future), TURN_CURVATURE_MIN_SPEED)


def limit_accel_in_turns(v_ego, curvature, a_target, a_lat_max,
                         safety_ratio=0.70, min_v=0.1):
  if v_ego < min_v or a_lat_max <= 0.0:
    return a_target

  a_lat_effective = abs(a_lat_max) * float(safety_ratio)
  lateral_accel = abs((v_ego ** 2) * curvature)
  if lateral_accel >= a_lat_effective:
    accel_allowed = 0.0
  else:
    accel_allowed = math.sqrt(a_lat_effective ** 2 - lateral_accel ** 2)
  return [a_target[0], min(a_target[1], accel_allowed)]


class LongitudinalPlanner:
  def __init__(self, CP, init_v=0.0, init_a=0.0):
    self.CP = CP
    self.params = Params()
    self.param_read_counter = 0

    self.mpc = LongitudinalMpc()

    # Match aPilot selection: ExperimentalMode forces E2E, while
    # TrafficStopMode selects ACC or conditional ACC/E2E operation.
    self.auto_e2e_enabled = False
    self.experimental_mode_enabled = False
    self.traffic_stop_mode = 2
    self.conditional_e2e = ConditionalE2EController(DT_MDL)
    self.auto_e2e_stopping = False
    self.auto_e2e_prepare = False
    self.e2e_stop_distance = 0.0
    self.traffic_stop_accel_factor = 0.8

    # MyDrivingMode
    self.my_driving_mode = 3
    self.my_driving_mode_accel = 1.0
    self.my_eco_mode_factor = 0.8
    self.cruise_max_vals = list(CRUISE_MAX_VAL_DEFAULTS)

    # ACC-only brake-release acceleration ramp.
    self.brake_resume_pending = False
    self.brake_resume_accel_time = 0.0
    self.brake_resume_accel_start = 0.0
    self.prev_reset_state = True

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
    self.events = Events()

  def read_param(self):
    self.mpc.applyLongDynamicCost = self.params.get_bool("ApplyLongDynamicCost")
    self.mpc.softHoldMode = int(clip(self.params.get_int("SoftHoldMode"), 0, 2))
    self.auto_e2e_enabled = self.CP.openpilotLongitudinalControl
    self.experimental_mode_enabled = self.params.get_bool('ExperimentalMode')
    traffic_mode_raw = self.params.get('TrafficStopMode', encoding='utf8')
    try:
      if traffic_mode_raw is None:
        legacy_mode = int(self.params.get('E2EAccMode', encoding='utf8') or 1)
        self.traffic_stop_mode = 0 if legacy_mode == 0 else 2
        self.experimental_mode_enabled = self.experimental_mode_enabled or legacy_mode == 2
      else:
        self.traffic_stop_mode = int(traffic_mode_raw)
    except (TypeError, ValueError):
      self.traffic_stop_mode = 2
    self.traffic_stop_mode = int(clip(self.traffic_stop_mode, 0, 2))
    traffic_stop_accel = self.params.get_int('TrafficStopAccel')
    self.traffic_stop_accel_factor = float(clip((traffic_stop_accel if traffic_stop_accel > 0 else 80) * 0.01,
                                                0.1, 1.2))
    if not self.auto_e2e_enabled:
      self.mpc.mode = 'acc'
    # aPilot uses one standstill distance for ACC and E2E. Params are stored
    # in centimetres to match its StopDistance setting (default 600 cm).
    stop_distance = self.params.get_int('StopDistance')
    self.mpc.stop_distance = float(clip((stop_distance if stop_distance > 0 else 600) * 0.01, 2.0, 10.0))

    # ── MyDrivingMode ──
    mode = self.params.get("MyDrivingMode", encoding='utf8')
    try:
      mode = int(mode)
    except (TypeError, ValueError):
      mode = 3
    if not 1 <= mode <= 4:
      mode = 3
    self.my_driving_mode = mode
    eco_factor = self.params.get_int("MyEcoModeFactor")
    self.my_eco_mode_factor = float(clip((eco_factor if eco_factor > 0 else 80) * 0.01, 0.1, 0.95))

    self.cruise_max_vals = []
    for key, default in zip(CRUISE_MAX_VAL_KEYS, CRUISE_MAX_VAL_DEFAULTS):
      raw = self.params.get_int(key)
      self.cruise_max_vals.append(float(raw * 0.01 if raw > 0 else default))

    gap_defaults = [110, 120, 140, 160]
    gap_values = []
    for key, default in zip(["TFollowGap1", "TFollowGap2", "TFollowGap3", "TFollowGap4"], gap_defaults):
      value = self.params.get_int(key)
      gap_values.append((value if value > 0 else default) * 0.01)
    self.mpc.tfollow_gaps = gap_values
    speed_ratio = self.params.get_int("TFollowSpeedRatio")
    self.mpc.t_follow_speed_ratio = (speed_ratio if speed_ratio >= 100 else 120) * 0.01
    # ───────────────────

  def get_max_accel(self, v_ego):
    return interp(v_ego, A_CRUISE_MAX_BP, self.cruise_max_vals)

  def reset_auto_e2e(self):
    self.conditional_e2e.reset()
    self.auto_e2e_stopping = False
    self.auto_e2e_prepare = False
    self.e2e_stop_distance = 0.0
    self.mpc.traffic_stop_active = False
    self.mpc.traffic_stop_distance = 0.0

  def update_auto_e2e_mode(self, car_state, radar_state, model_msg, active, driving_mode, safe_mode_factor):
    model_valid = (len(model_msg.position.x) == 33 and
                   len(model_msg.position.y) == 33 and
                   len(model_msg.velocity.x) == 33)
    lead_one = radar_state.leadOne
    lead_present = lead_one.status or radar_state.leadTwo.status
    radar_lead_present = lead_one.status and lead_one.radar
    vision_lead_present = (lead_one.status and lead_one.dRel < E2E_VISION_LEAD_DISTANCE and
                           not lead_one.radar)
    mode = self.conditional_e2e.update(
      available=active and self.auto_e2e_enabled,
      experimental_mode=self.experimental_mode_enabled,
      traffic_stop_mode=self.traffic_stop_mode,
      driving_mode=driving_mode,
      model_valid=model_valid,
      model_x=float(model_msg.position.x[-1]) if model_valid else 0.0,
      model_y=float(model_msg.position.y[-1]) if model_valid else 0.0,
      model_v0=float(model_msg.velocity.x[0]) if model_valid else 0.0,
      model_v_end=float(model_msg.velocity.x[-1]) if model_valid else 0.0,
      v_ego=car_state.vEgo,
      steering_angle_deg=car_state.steeringAngleDeg,
      gas_pressed=car_state.gasPressed,
      brake_pressed=car_state.brakePressed,
      right_blinker=car_state.rightBlinker,
      lead_present=lead_present,
      radar_lead_present=radar_lead_present,
      radar_lead_distance=float(lead_one.dRel) if lead_one.status else 0.0,
      vision_lead_present=vision_lead_present)
    self.auto_e2e_stopping = self.conditional_e2e.stopping
    self.auto_e2e_prepare = self.conditional_e2e.prepare
    self.e2e_stop_distance = self.conditional_e2e.stop_distance
    self.mpc.traffic_stop_active = self.auto_e2e_stopping
    # Match aPilot's TrafficStopAccel * MySafeModeFactor behavior. The target
    # MPC solver has comfort braking compiled in, so use the equivalent virtual
    # obstacle distance instead of changing the generated solver parameter set.
    stop_decel_factor = self.traffic_stop_accel_factor * float(clip(safe_mode_factor, 0.5, 1.0))
    self.mpc.traffic_stop_distance = adjust_stop_distance_for_decel(
      self.e2e_stop_distance, car_state.vEgo, stop_decel_factor)
    return mode

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

    driving_mode = int(clip(sm['controlsState'].myDrivingMode, 1, 4))
    self.my_driving_mode = driving_mode
    if driving_mode == 1:
      self.my_driving_mode_accel = self.my_eco_mode_factor
    elif driving_mode == 2:
      self.my_driving_mode_accel = self.my_eco_mode_factor * float(clip(sm['controlsState'].mySafeModeFactor, 0.5, 1.0))
    else:
      self.my_driving_mode_accel = 1.0
    self.mpc.mode = self.update_auto_e2e_mode(sm['carState'], sm['radarState'], sm['modelV2'],
                                              sm['controlsState'].enabled, driving_mode,
                                              sm['controlsState'].mySafeModeFactor)

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

    current_speed_kph = sm['carState'].vEgoCluster * CV.MS_TO_KPH
    if sm['carState'].brakePressed:
      self.brake_resume_pending = True
      self.brake_resume_accel_time = 0.0

    long_reactivated = self.prev_reset_state and not reset_state
    speed_gap_kph = v_cruise_kph - current_speed_kph
    if long_reactivated and self.brake_resume_pending:
      if (self.mpc.mode == 'acc' and
          current_speed_kph >= BRAKE_RESUME_MIN_SPEED_KPH and
          speed_gap_kph >= BRAKE_RESUME_MIN_SPEED_GAP_KPH):
        self.brake_resume_accel_start = float(interp(current_speed_kph,
                                                     BRAKE_RESUME_ACCEL_BP,
                                                     BRAKE_RESUME_ACCEL_V))
        self.brake_resume_accel_time = BRAKE_RESUME_ACCEL_TIME
      self.brake_resume_pending = False
    self.prev_reset_state = reset_state

    # No change cost when user is controlling the speed, or when standstill
    prev_accel_constraint = not (reset_state or sm['carState'].standstill)

    cruise_max_accel = float(clip(self.get_max_accel(v_ego) * self.my_driving_mode_accel,
                                  0.0, MAX_ACCEL))
    if self.mpc.mode == 'acc':
      accel_limits = [A_CRUISE_MIN, cruise_max_accel]
      curvature_future = get_future_curvature(sm['modelV2'], sm['controlsState'].desiredCurvature)
      accel_limits_turns = limit_accel_in_turns(v_ego, curvature_future, accel_limits, 3.0)

      if self.brake_resume_accel_time > 0.0:
        progress = float(clip(1.0 - self.brake_resume_accel_time / BRAKE_RESUME_ACCEL_TIME, 0.0, 1.0))
        resume_accel_limit = self.brake_resume_accel_start + \
                             (accel_limits_turns[1] - self.brake_resume_accel_start) * progress
        accel_limits_turns[1] = min(accel_limits_turns[1], resume_accel_limit)
        self.brake_resume_accel_time = max(0.0, self.brake_resume_accel_time - DT_MDL)
    else:
      accel_limits = [MIN_ACCEL, MAX_ACCEL]
      accel_limits_turns = [MIN_ACCEL, MAX_ACCEL]
      self.brake_resume_accel_time = 0.0

    # Driver pedal input always takes priority over the automatic ramp.
    if sm['carState'].gasPressed or sm['carState'].brakePressed:
      self.brake_resume_accel_time = 0.0

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
    self.mpc.update(sm['carState'], sm['radarState'], sm['controlsState'], v_cruise_sol, x, v, a, j,
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
      actuator_delay = float(clip(configured_delay, 0.1, 1.0))
    action_t = max(DT_MDL, actuator_delay + DT_MDL)
    self.output_a_target, self.output_should_stop, self.output_v_target_now, _ = get_accel_from_plan(
      self.v_desired_trajectory, self.a_desired_trajectory, T_IDXS[:CONTROL_N],
      action_t=action_t, v_ego_stopping=self.CP.vEgoStopping)
    self.output_j_target_now = float(self.j_desired_trajectory[0])

  def publish(self, sm, pm):
    plan_send = messaging.new_message('longitudinalPlan')

    plan_send.valid = sm.all_checks(service_list=['carState', 'controlsState'])

    longitudinalPlan = plan_send.longitudinalPlan
    longitudinalPlan.modelMonoTime = sm.logMonoTime['modelV2']
    longitudinalPlan.processingDelay = (plan_send.logMonoTime - sm.logMonoTime['modelV2']) / 1e9

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
    longitudinalPlan.xState = self.mpc.xState
    # Expose the automatic E2E stop/depart state to the onroad UI.
    # 0: inactive, 1: stopping/waiting, 2: preparing to depart.
    e2e_state_active = self.auto_e2e_enabled and sm['controlsState'].enabled
    longitudinalPlan.trafficState = (2 if self.auto_e2e_prepare else (1 if self.auto_e2e_stopping else 0)) if e2e_state_active else 0
    longitudinalPlan.onStop = bool(e2e_state_active and self.auto_e2e_stopping)
    longitudinalPlan.eventsDEPRECATED = self.events.to_msg()
    longitudinalPlan.fcw = self.fcw

    longitudinalPlan.solverExecutionTime = self.mpc.solve_time

    pm.send('longitudinalPlan', plan_send)

  def cruise_solutions(self, enabled, v_ego, a_ego, v_cruise, sm):
    # Vision curve speed is already folded into controlsState.vCruise by
    # CruiseHelper using the aPilot C2 curvature-to-speed table.
    self.events = Events()
    return 'cruise', float("inf"), v_cruise
