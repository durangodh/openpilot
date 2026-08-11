import numpy as np
from common.realtime import sec_since_boot, DT_MDL
from common.numpy_fast import interp
from selfdrive.controls.lib.lane_planner import LanePlanner
from selfdrive.swaglog import cloudlog
from selfdrive.controls.lib.lateral_mpc_lib.lat_mpc import LateralMpc
from selfdrive.controls.lib.lateral_mpc_lib.lat_mpc import N as LAT_MPC_N
from selfdrive.controls.lib.drive_helpers import CONTROL_N, MIN_SPEED
from selfdrive.controls.lib.desire_helper import DesireHelper, AUTO_LCA_START_TIME
from selfdrive.controls.lib.dynamic_lane import (select_lateral_path,
                                                 update_dynamic_lane_profile,
                                                 update_low_speed_laneless)
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

    # Keep the hardware camera offset fixed and apply the cached user
    # OffsetTotal once to the final path.
    self.offset_total = DEFAULT_OFFSET_TOTAL

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

    self.dynamic_lane_profile = 0
    self.dynamic_lane_profile_status = True
    self.dynamic_lane_profile_status_buffer = True
    self.low_speed_laneless = True

    self.param_read_counter = 0
    self.read_param(force=True)

  def read_param(self, force=False):
    # modelV2 drives this planner at 20 Hz. Params storage only needs a 1 Hz
    # refresh; cached values are used for every MPC update in between.
    if force or self.param_read_counter % 20 == 0:
      try:
        self.dynamic_lane_profile = int(
          self.params.get("DynamicLaneProfile", encoding="utf8") or "0")
      except (TypeError, ValueError):
        self.dynamic_lane_profile = 0
      self.offset_total = self._read_offset_total()
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

    measured_curvature = sm['controlsState'].curvature
    v_ego_car = sm['carState'].vEgo

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

    # LanePlanner blends lane lines into its input array in place. Preserve an
    # untouched model/E2E path so Lane less and Auto-laneless are genuinely
    # independent from the explicit lane-line path.
    model_path_xyz = self.path_xyz.copy()
    self.d_path_w_lines_xyz = self.LP.get_d_path(
      v_ego_car, self.t_idxs, model_path_xyz.copy())

    self.low_speed_laneless = update_low_speed_laneless(
      v_ego_car, self.low_speed_laneless)
    use_laneless, profile_laneless = self.get_dynamic_lane_profile(
      self.low_speed_laneless)

    # OffsetTotal applies equally to both candidate paths. Keep the candidates
    # independent so selecting one cannot mutate the other or its telemetry.
    model_path_xyz[:, 1] += self.offset_total
    self.d_path_w_lines_xyz[:, 1] += self.offset_total
    # Keep explicit Lane-less and Auto-laneless on the untouched model path.
    # Lane-only and confident Auto use LanePlanner's own low-speed blend: pure
    # model below 5 km/h, progressively more lane guidance up to 10 km/h. The
    # published state still reports the legacy low-speed laneless mode.
    self.path_xyz = select_lateral_path(
      model_path_xyz, self.d_path_w_lines_xyz, profile_laneless)
    self.dynamic_lane_profile_status = use_laneless

    if not use_laneless:
      self.lat_mpc.set_weights(PATH_COST, LATERAL_MOTION_COST,
                               LATERAL_ACCEL_COST, LATERAL_JERK_COST,
                               STEERING_RATE_COST)
    else:
      lateral_motion_cost = interp(v_ego_car, [5.0, 10.0],
                                   [LATERAL_MOTION_COST * 1.5, LATERAL_MOTION_COST])
      self.lat_mpc.set_weights(PATH_COST, lateral_motion_cost,
                               LATERAL_ACCEL_COST, LATERAL_JERK_COST,
                               STEERING_RATE_COST)

    # Reuse the path-distance vector for both interpolations. The trajectory is
    # unchanged between them, so a second NumPy norm only wastes planner CPU.
    path_distance = np.linalg.norm(self.path_xyz, axis=1)
    y_pts = np.interp(self.v_ego * self.t_idxs[:LAT_MPC_N + 1],
                      path_distance, self.path_xyz[:, 1])
    heading_pts = np.interp(self.v_ego * self.t_idxs[:LAT_MPC_N + 1],
                            path_distance, self.plan_yaw)
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

  def get_dynamic_lane_profile(self, low_speed):
    """True = 레인리스 경로 사용, False = 레인모드(차선) 경로 사용.
    DynamicLaneProfile(0=레인모드, 1=레인리스, 2=오토)을 따르되,
    약 16 km/h 미만에서는 모든 프로필을 레인리스로 운용한다.
    """
    lane_change_active = self.DH.lane_change_state in (
      LaneChangeState.laneChangeStarting, LaneChangeState.laneChangeFinishing)
    lane_change_off = self.DH.lane_change_state == LaneChangeState.off
    use_laneless, profile_laneless, laneless_buffer = update_dynamic_lane_profile(
      self.dynamic_lane_profile, self.LP.lll_prob, self.LP.rll_prob,
      lane_change_active, lane_change_off, low_speed,
      self.dynamic_lane_profile_status_buffer)
    self.dynamic_lane_profile_status_buffer = laneless_buffer
    return use_laneless, profile_laneless

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

    # ── UI 하단 중앙 차선/오프셋 디버그 문자열 ──
    lane_mode = 'laneless' if self.dynamic_lane_profile_status else 'lanemode'
    offset_cm = self.offset_total * 100.0
    tail = f'offset={offset_cm:.1f}cm'
    if abs(self.LP.lane_offset) > 0.005:
      tail += f' lane={self.LP.lane_offset * 100.0:+.0f}cm'
    lateralPlan.latDebugText = (
      f"{lane_mode} | "
      f"{self.LP.lane_width_left:.1f}m | "
      f"{self.LP.lane_width:.1f}m | "
      f"{self.LP.lane_width_right:.1f}m | "
      f"{tail}"
    )
    lateralPlan.dynamicLaneProfileStatus = bool(self.dynamic_lane_profile_status)

    pm.send('lateralPlan', plan_send)
