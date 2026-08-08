import numpy as np
from common.conversions import Conversions as CV
from common.realtime import sec_since_boot, DT_MDL
from common.numpy_fast import interp
from selfdrive.controls.lib.lane_planner import LanePlanner
from selfdrive.swaglog import cloudlog
from selfdrive.controls.lib.lateral_mpc_lib.lat_mpc import LateralMpc
from selfdrive.controls.lib.lateral_mpc_lib.lat_mpc import N as LAT_MPC_N
from selfdrive.controls.lib.drive_helpers import CONTROL_N, MIN_SPEED
from selfdrive.controls.lib.desire_helper import DesireHelper, AUTO_LCA_START_TIME
import cereal.messaging as messaging
from cereal import log
from common.params import Params

LaneChangeState = log.LateralPlan.LaneChangeState

TRAJECTORY_SIZE = 33

PATH_COST = 1.0
LATERAL_MOTION_COST = 0.11
LATERAL_ACCEL_COST = 0.0
LATERAL_JERK_COST = 0.04
STEERING_RATE_COST = 700.0

# 기본값 상수
DEFAULT_CAMERA_OFFSET = -0.06
DEFAULT_OFFSET_TOTAL = 0.0


class LateralPlanner:
  def __init__(self, CP, wide_camera=False, debug=False):
    self.params = Params()
    self.wide_camera = wide_camera
    self.last_params_update = 0

    # carrot 의 offset_total 로 통합.
    #   카메라 오프셋은 하드웨어 기본값(DEFAULT_CAMERA_OFFSET)으로 고정하고,
    #   사용자/학습 조정은 최종 경로에 적용되는 offset_total 하나로만 한다.
    #   저장 키는 기존 OffsetTotal 을 그대로 쓴다 (Auto-Tuner Phase 2 학습 대상).
    self.offset_total = self._read_offset_total()

    self.LP = LanePlanner(wide_camera=wide_camera)

    self.DH = DesireHelper()

    self.factor1 = CP.wheelbase - CP.centerToFront
    self.factor2 = (CP.centerToFront * CP.mass) / (CP.wheelbase * CP.tireStiffnessRear)
    self.last_cloudlog_t = 0
    self.solution_invalid_cnt = 0

    self.path_xyz = np.zeros((TRAJECTORY_SIZE, 3))
    self.velocity_xyz = np.zeros((TRAJECTORY_SIZE, 3))
    self.plan_yaw = np.zeros((TRAJECTORY_SIZE,))
    self.plan_yaw_rate = np.zeros((TRAJECTORY_SIZE,))
    self.t_idxs = np.arange(TRAJECTORY_SIZE)
    self.y_pts = np.zeros((TRAJECTORY_SIZE,))
    self.v_plan = np.zeros((TRAJECTORY_SIZE,))
    self.v_ego = 0.0
    self.l_lane_change_prob = 0.0
    self.r_lane_change_prob = 0.0
    self.d_path_w_lines_xyz = np.zeros((TRAJECTORY_SIZE, 3))
    self.d_path_xyz = np.zeros((TRAJECTORY_SIZE, 3))  # 방어용 초기화

    self.debug_mode = debug

    self.lat_mpc = LateralMpc()
    self.reset_mpc(np.zeros(4))

    self.dynamic_lane_profile = int(self.params.get("DynamicLaneProfile", encoding="utf8") or "0")
    self.dynamic_lane_profile_status = True
    self.dynamic_lane_profile_status_buffer = False

    self.param_read_counter = 0
    self.read_param()

  def read_param(self):
    self.dynamic_lane_profile = int(self.params.get("DynamicLaneProfile", encoding="utf8") or "0")
    self.param_read_counter += 1

  def _read_offset_total(self):
    try:
      val = float(self.params.get("OffsetTotal", encoding="utf8") or str(DEFAULT_OFFSET_TOTAL))
    except (TypeError, ValueError):
      val = DEFAULT_OFFSET_TOTAL
    return max(-1.0, min(1.0, val))

  def reset_mpc(self, x0=np.zeros(4)):
    self.x0 = x0
    self.lat_mpc.reset(x0=self.x0)

  def update(self, sm):
    self.read_param()
    self.offset_total = self._read_offset_total()

    measured_curvature = sm['controlsState'].curvature

    md = sm['modelV2']
    self.LP.parse_model(md)
    if len(md.position.x) == TRAJECTORY_SIZE and len(md.orientation.x) == TRAJECTORY_SIZE:
      self.path_xyz = np.column_stack([md.position.x, md.position.y, md.position.z])
      self.t_idxs = np.array(md.position.t)
      self.plan_yaw = np.array(md.orientation.z)
      self.plan_yaw_rate = np.array(md.orientationRate.z)
      self.velocity_xyz = np.column_stack([md.velocity.x, md.velocity.y, md.velocity.z])
      car_speed = np.linalg.norm(self.velocity_xyz, axis=1)
      self.v_plan = np.clip(car_speed, MIN_SPEED, np.inf)
      self.v_ego = self.v_plan[0]

    lane_change_prob = self.LP.l_lane_change_prob + self.LP.r_lane_change_prob
    self.DH.update(sm['carState'], sm['carControl'].latActive, lane_change_prob, md)

    if self.DH.desire == log.LateralPlan.Desire.laneChangeRight or self.DH.desire == log.LateralPlan.Desire.laneChangeLeft:
      self.LP.lll_prob *= self.DH.lane_change_ll_prob
      self.LP.rll_prob *= self.DH.lane_change_ll_prob
    self.d_path_w_lines_xyz = self.LP.get_d_path(self.v_ego, self.t_idxs, self.path_xyz)

    low_speed = self.v_ego < 10 * CV.MPH_TO_MS

    if not self.get_dynamic_lane_profile(sm['longitudinalPlan']) and not low_speed:
      self.path_xyz = self.d_path_w_lines_xyz
      self.dynamic_lane_profile_status = False
      self.lat_mpc.set_weights(PATH_COST, LATERAL_MOTION_COST,
                               LATERAL_ACCEL_COST, LATERAL_JERK_COST,
                               STEERING_RATE_COST)
    else:
      self.dynamic_lane_profile_status = True
      lateral_motion_cost = interp(self.v_ego, [5.0, 10.0],
                                   [LATERAL_MOTION_COST * 1.5, LATERAL_MOTION_COST])
      self.lat_mpc.set_weights(PATH_COST, lateral_motion_cost,
                               LATERAL_ACCEL_COST, LATERAL_JERK_COST,
                               STEERING_RATE_COST)

    # offset_total 을 최종 결정된 path_xyz 에 적용 (레인모드/레인리스 공통)
    self.path_xyz[:, 1] += self.offset_total

    y_pts = np.interp(self.v_ego * self.t_idxs[:LAT_MPC_N + 1],
                      np.linalg.norm(self.path_xyz, axis=1),
                      self.path_xyz[:, 1])
    heading_pts = np.interp(self.v_ego * self.t_idxs[:LAT_MPC_N + 1],
                            np.linalg.norm(self.path_xyz, axis=1),
                            self.plan_yaw)
    yaw_rate_pts = self.plan_yaw_rate[:LAT_MPC_N + 1]
    self.y_pts = y_pts

    assert len(y_pts) == LAT_MPC_N + 1
    assert len(heading_pts) == LAT_MPC_N + 1
    assert len(yaw_rate_pts) == LAT_MPC_N + 1
    lateral_factor = np.clip(self.factor1 - (self.factor2 * self.v_plan**2), 0.0, np.inf)
    p = np.column_stack([self.v_plan, lateral_factor])
    self.lat_mpc.run(self.x0,
                     p,
                     y_pts,
                     heading_pts,
                     yaw_rate_pts)
    self.x0[3] = interp(DT_MDL, self.t_idxs[:LAT_MPC_N + 1], self.lat_mpc.x_sol[:, 3])

    mpc_nans = np.isnan(self.lat_mpc.x_sol[:, 3]).any()
    t = sec_since_boot()
    if mpc_nans or self.lat_mpc.solution_status != 0:
      self.reset_mpc()
      self.x0[3] = measured_curvature * self.v_ego
      if t > self.last_cloudlog_t + 5.0:
        self.last_cloudlog_t = t
        cloudlog.warning("Lateral mpc - nan: True")

    if self.lat_mpc.cost > 1e6 or mpc_nans:
      self.solution_invalid_cnt += 1
    else:
      self.solution_invalid_cnt = 0

  def get_dynamic_lane_profile(self, longitudinal_plan):
    """True = 레인리스 경로 사용, False = 레인모드(차선) 경로 사용.
    DynamicLaneProfile 하나로만 결정한다. (0=레인모드 1=레인리스 2=오토)
    """
    if self.dynamic_lane_profile == 1:
      return True
    elif self.dynamic_lane_profile == 0:
      return False
    elif self.dynamic_lane_profile == 2:
      # laneless while lane change in progress
      if self.DH.lane_change_state in (LaneChangeState.laneChangeStarting, LaneChangeState.laneChangeFinishing):
        return True
      elif self.DH.lane_change_state == LaneChangeState.off:
        if (self.LP.lll_prob + self.LP.rll_prob) / 2 < 0.3:
          self.dynamic_lane_profile_status_buffer = True
        if (self.LP.lll_prob + self.LP.rll_prob) / 2 > 0.5:
          self.dynamic_lane_profile_status_buffer = False
        if self.dynamic_lane_profile_status_buffer:
          return True
    return False

  def publish(self, sm, pm):
    plan_solution_valid = self.solution_invalid_cnt < 2
    plan_send = messaging.new_message('lateralPlan')
    plan_send.valid = sm.all_checks(service_list=['carState', 'controlsState', 'modelV2'])

    lateralPlan = plan_send.lateralPlan
    lateralPlan.modelMonoTime = sm.logMonoTime['modelV2']
    lateralPlan.laneWidth = float(self.LP.lane_width)
    lateralPlan.dPathPoints = self.y_pts.tolist()
    lateralPlan.psis = self.lat_mpc.x_sol[0:CONTROL_N, 2].tolist()
    lateralPlan.curvatures = (self.lat_mpc.x_sol[0:CONTROL_N, 3] / self.v_ego).tolist()
    lateralPlan.curvatureRates = [float(x / self.v_ego) for x in self.lat_mpc.u_sol[0:CONTROL_N - 1]] + [0.0]

    lateralPlan.lProb = float(self.LP.lll_prob)
    lateralPlan.rProb = float(self.LP.rll_prob)
    lateralPlan.dProb = float(self.LP.d_prob)

    lateralPlan.mpcSolutionValid = bool(plan_solution_valid)
    lateralPlan.solverExecutionTime = self.lat_mpc.solve_time
    if self.debug_mode:
      lateralPlan.solverCost = self.lat_mpc.cost
      lateralPlan.solverState = log.LateralPlan.SolverState.new_message()
      lateralPlan.solverState.x = self.lat_mpc.x_sol.tolist()
      lateralPlan.solverState.u = self.lat_mpc.u_sol.flatten().tolist()

    lateralPlan.desire = self.DH.desire
    lateralPlan.useLaneLines = not self.dynamic_lane_profile_status
    lateralPlan.laneChangeState = self.DH.lane_change_state
    lateralPlan.laneChangeDirection = self.DH.lane_change_direction

    plan_send.lateralPlan.dPathWLinesX = [float(x) for x in self.d_path_w_lines_xyz[:, 0]]
    plan_send.lateralPlan.dPathWLinesY = [float(y) for y in self.d_path_w_lines_xyz[:, 1]]

    lateralPlan.laneChangePrev = self.DH.prev_lane_change
    lateralPlan.laneChangeEdgeBlock = (self.DH.lane_change_state == LaneChangeState.preLaneChange) and self.DH.road_edge

    lateralPlan.autoLaneChangeEnabled = self.DH.auto_lane_change_enabled
    lateralPlan.autoLaneChangeTimer = int(AUTO_LCA_START_TIME) - int(self.DH.auto_lane_change_timer)

    lateralPlan.dynamicLaneProfile = int(self.dynamic_lane_profile)

    # ── UI 하단 중앙 디버그 문자열 (carrot latDebugText 이식) ──
    #   모드 | 좌차선거리 | 차로폭 | 우차선거리 | offset(cm) turn(km/h)
    #   offset : offset_total (캐롯과 동일)
    #   turn   : VisionTurnController 목표속도 (캐롯의 curve_speed 대체)
    lane_mode = 'laneless' if self.dynamic_lane_profile_status else 'lanemode'
    offset_cm = self.offset_total * 100.0
    #   turn 은 VisionTurnController 가 실제로 개입 중일 때만 표시한다.
    #   (비활성 상태에서는 v_turn 이 내부 기본값을 그대로 뱉어 의미가 없다)
    turn_kph = 0.0
    try:
      lp = sm['longitudinalPlan']
      # capnp enum 이 int 로도, 문자열로도 올 수 있어 둘 다 받는다
      # (controlsd.py 는 int, longitudinal_planner.py 는 enum 으로 비교 중)
      vtc_state = lp.visionTurnControllerState
      if vtc_state in (1, 2) or str(vtc_state) in ('entering', 'turning'):
        turn_kph = lp.visionTurnSpeed * 3.6
    except Exception:
      turn_kph = 0.0
    tail = f'offset={offset_cm:.1f}cm'
    if abs(self.LP.lane_offset) > 0.005:
      tail += f' lane={self.LP.lane_offset * 100.0:+.0f}cm'
    if turn_kph > 0.5:
      tail += f' turn={min(turn_kph, 200.0):.0f}km/h'
    lateralPlan.latDebugText = (
      f"{lane_mode} | "
      f"{self.LP.lane_width_left:.1f}m | "
      f"{self.LP.lane_width:.1f}m | "
      f"{self.LP.lane_width_right:.1f}m | "
      f"{tail}"
    )
    lateralPlan.dynamicLaneProfileStatus = bool(self.dynamic_lane_profile_status)

    pm.send('lateralPlan', plan_send)
