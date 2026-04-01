import numpy as np
from common.conversions import Conversions as CV
from common.realtime import sec_since_boot, DT_MDL
from common.numpy_fast import interp
from selfdrive.controls.lib.lane_planner import LanePlanner
from selfdrive.ntune import ntune_common_get
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
DEFAULT_PATH_OFFSET = 0.0


class LateralPlanner:
  def __init__(self, CP, use_lanelines=True, wide_camera=False, debug=False):
    self.params = Params()
    self.wide_camera = wide_camera
    self.use_lanelines = self.params.get_bool('UseLanelines')
    self.last_params_update = 0

    # UI에서 실시간 조절되는 오프셋 초기화
    self.camera_offset = self._read_camera_offset()
    self.path_offset = self._read_path_offset()

    self.LP = LanePlanner(wide_camera=wide_camera)
    # 초기 camera_offset 적용
    self.LP.camera_offset = self.camera_offset

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

    self.debug_mode = debug

    self.lat_mpc = LateralMpc()
    self.reset_mpc(np.zeros(4))

    self.param_s = Params()
    self.dynamic_lane_profile_enabled = self.params.get_bool("DynamicLaneProfileToggle")
    self.dynamic_lane_profile = int(self.params.get("DynamicLaneProfile", encoding="utf8") or "0")
    self.dynamic_lane_profile_status = True
    self.dynamic_lane_profile_status_buffer = False

    self.vision_curve_laneless = self.param_s.get_bool("VisionCurveLaneless")
    
    self.param_read_counter = 0
    self.read_param()

  def read_param(self):
    self.dynamic_lane_profile = int(self.params.get("DynamicLaneProfile", encoding="utf8") or "0")
    if self.param_read_counter % 50 == 0:
      self.dynamic_lane_profile_enabled = self.param_s.get_bool("DynamicLaneProfileToggle")
      self.vision_curve_laneless = self.param_s.get_bool("VisionCurveLaneless")
    self.param_read_counter += 1
  
  def _read_camera_offset(self):
    try:
      val = float(self.params.get("CameraOffset", encoding="utf8") or str(DEFAULT_CAMERA_OFFSET))
    except (TypeError, ValueError):
      val = DEFAULT_CAMERA_OFFSET
    return -val if self.wide_camera else val

  def _read_path_offset(self):
    try:
      val = float(self.params.get("PathOffset", encoding="utf8") or str(DEFAULT_PATH_OFFSET))
    except (TypeError, ValueError):
      val = DEFAULT_PATH_OFFSET
    return val

  def reset_mpc(self, x0=np.zeros(4)):
    self.x0 = x0
    self.lat_mpc.reset(x0=self.x0)

  def update(self, sm):
    self.read_param()
    self.use_lanelines = self.params.get_bool('UseLanelines')
    self.camera_offset = self._read_camera_offset()
    self.path_offset = self._read_path_offset()
    self.LP.camera_offset = self.camera_offset

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
    self.DH.update(sm['carState'], sm['carControl'].latActive, lane_change_prob)

    d_path_xyz = self.path_xyz
    if self.DH.desire == log.LateralPlan.Desire.laneChangeRight or self.DH.desire == log.LateralPlan.Desire.laneChangeLeft:
      self.LP.lll_prob *= self.DH.lane_change_ll_prob
      self.LP.rll_prob *= self.DH.lane_change_ll_prob

    low_speed = v_ego_car < 10 * CV.MPH_TO_MS
    
    if self.use_lanelines and not self.get_dynamic_lane_profile(sm['longitudinalPlan']) and not low_speed:
      d_path_xyz = self.LP.get_d_path(self.v_ego, self.t_idxs, self.path_xyz)
      self.dynamic_lane_profile_status = False
      self.lat_mpc.set_weights(PATH_COST, LATERAL_MOTION_COST,
                             LATERAL_ACCEL_COST, LATERAL_JERK_COST,
                             STEERING_RATE_COST)
    else:
      d_path_xyz = self.path_xyz
      self.dynamic_lane_profile_status = True
      lateral_motion_cost = interp(self.v_ego, [5.0, 10.0],
                                 [LATERAL_MOTION_COST * 1.5, LATERAL_MOTION_COST])
      self.lat_mpc.set_weights(PATH_COST, lateral_motion_cost,
                               LATERAL_ACCEL_COST, LATERAL_JERK_COST,
                               STEERING_RATE_COST)
      
    d_path_xyz[:, 1] += self.path_offset

    y_pts = np.interp(self.v_ego * self.t_idxs[:LAT_MPC_N + 1],
                  np.linalg.norm(d_path_xyz, axis=1),
                  d_path_xyz[:, 1])
    heading_pts = np.interp(self.v_ego * self.t_idxs[:LAT_MPC_N + 1],
                            np.linalg.norm(self.path_xyz, axis=1),
                            self.plan_yaw)
    yaw_rate_pts = self.plan_yaw_rate[:LAT_MPC_N+1]
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
    if not self.dynamic_lane_profile_enabled:
      return True
    elif self.dynamic_lane_profile == 1:
      return True
    elif self.dynamic_lane_profile == 0:
      return False
    elif self.dynamic_lane_profile == 2:
      # laneless while lane change in progress
      if self.DH.lane_change_state in (LaneChangeState.laneChangeStarting, LaneChangeState.laneChangeFinishing):
        return True
      elif self.DH.lane_change_state == LaneChangeState.off:
        if (self.LP.lll_prob + self.LP.rll_prob) / 2 < 0.3 \
          or ((longitudinal_plan.visionCurrentLatAcc > 1.0 or longitudinal_plan.visionMaxPredLatAcc > 1.4)
           and self.vision_curve_laneless):
          self.dynamic_lane_profile_status_buffer = True
        if (self.LP.lll_prob + self.LP.rll_prob) / 2 > 0.5 \
          and ((longitudinal_plan.visionCurrentLatAcc < 0.6 and longitudinal_plan.visionMaxPredLatAcc < 0.7)
           or not self.vision_curve_laneless):
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
    lateralPlan.curvatures = (self.lat_mpc.x_sol[0:CONTROL_N, 3]/self.v_ego).tolist()
    lateralPlan.curvatureRates = [float(x/self.v_ego) for x in self.lat_mpc.u_sol[0:CONTROL_N - 1]] + [0.0]

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
    lateralPlan.useLaneLines = self.use_lanelines
    lateralPlan.laneChangeState = self.DH.lane_change_state
    lateralPlan.laneChangeDirection = self.DH.lane_change_direction

    plan_send.lateralPlan.dPathWLinesX = [float(x) for x in self.d_path_w_lines_xyz[:, 0]]
    plan_send.lateralPlan.dPathWLinesY = [float(y) for y in self.d_path_w_lines_xyz[:, 1]]

    lateralPlan.laneChangePrev = self.DH.prev_lane_change

    lateralPlan.autoLaneChangeEnabled = self.DH.auto_lane_change_enabled
    lateralPlan.autoLaneChangeTimer = int(AUTO_LCA_START_TIME) - int(self.DH.auto_lane_change_timer)

    lateralPlan.dynamicLaneProfile = int(self.dynamic_lane_profile)
    lateralPlan.dynamicLaneProfileStatus = bool(self.dynamic_lane_profile_status)

    pm.send('lateralPlan', plan_send)
