import numpy as np
from cereal import log
from common.filter_simple import FirstOrderFilter
from common.numpy_fast import interp, clip, mean
from common.realtime import DT_MDL
from selfdrive.swaglog import cloudlog
from selfdrive.controls.lib.lane_path_validation import (valid_samples, valid_lane_path,
                                                       valid_lane_times, lane_horizon_weights,
                                                       curve_centering_weight, repair_lane_times)

TRAJECTORY_SIZE = 33
# 커브 차선중앙 유지는 커브가 이어질 때만: 처음 1초는 모델 라인, 3초에 완전 적용.
# 짧은 커브는 모델의 자연스러운 라인을, 긴 커브는 차선 중앙을 따른다(2026-10-08).
CURVE_CENTER_DELAY_BP = [1.0, 3.0]

CAMERA_OFFSET = -0.06
PATH_OFFSET = 0.0

ENABLE_ZORROBYTE = True
ENABLE_INC_LANE_PROB = True
# 차선변경 중 d_prob 0<->1 에 걸리는 최소 시간. 차선변경 페이드가 양쪽 차선 0.5 경계를 지날 때
# d_prob 가 1.0 -> 0.45 로 한 프레임에 떨어져 시작 순간 핸들이 꺾였다(2026-10-08).
D_PROB_BLEND_TIME = 0.5

class LanePlanner:
  def __init__(self, wide_camera=False):
    self.ll_t = np.zeros((TRAJECTORY_SIZE,))
    self.ll_x = np.zeros((TRAJECTORY_SIZE,))
    self.lll_y = np.zeros((TRAJECTORY_SIZE,))
    self.rll_y = np.zeros((TRAJECTORY_SIZE,))
    self.lane_width_estimate = FirstOrderFilter(3.7, 9.95, DT_MDL)
    self.lane_width_certainty = FirstOrderFilter(1.0, 0.95, DT_MDL)
    self.lane_width = 3.7

    self.lll_prob = 0.
    self.rll_prob = 0.

    # UI 디버그용 : 차량 중심에서 좌/우 차선까지의 거리(m)
    self.lane_width_left = 0.
    self.lane_width_right = 0.
    self.d_prob = 0.

    self.lll_std = 0.
    self.rll_std = 0.

    self.l_lane_change_prob = 0.
    self.r_lane_change_prob = 0.

    self.wide_camera = wide_camera

    # aPilot: hardware camera calibration is fixed. User path correction is
    # applied once in lateral_planner, after lane/laneless path selection.
    self.camera_offset = -CAMERA_OFFSET if wide_camera else CAMERA_OFFSET
    self.path_offset = -PATH_OFFSET if wide_camera else PATH_OFFSET

    self.readings = []
    self.frame = 0

    # ── carrot lane_planner_2 이식 : 좌/우 여유공간 필터 + 경로 오프셋 ──
    #   lane_width_left/right : 차선 바깥쪽 여유폭(m). 캐롯은 모델의
    #   meta.laneWidthLeft 를 쓰지만 이 포크 모델에는 없어서
    #   도로경계(roadEdges) - 차선 거리로 근사한다.
    self.le_y = np.zeros(TRAJECTORY_SIZE)
    self.re_y = np.zeros(TRAJECTORY_SIZE)
    self.lane_width_left = 0.0
    self.lane_width_right = 0.0
    self.lane_width_left_filtered = FirstOrderFilter(1.0, 1.0, DT_MDL)
    self.lane_width_right_filtered = FirstOrderFilter(1.0, 1.0, DT_MDL)
    self.curve_time = 0.0
    self.curve_sign = 0.0
    self.curve_center_weight = 0.0
    # lateral_planner 가 차선변경 페이드(lll/rll_prob 축소) 중에만 켠다.
    self.lane_change_fade = False

  def parse_model(self, md):
    # Clear validity EVERY frame: malformed samples must not retain the previous
    # frame's confident lanes, road edges or lane-change probabilities.
    self.lll_prob = self.rll_prob = self.d_prob = 0.0
    self.l_lane_change_prob = self.r_lane_change_prob = 0.0
    self.lane_width_left = self.lane_width_right = 0.0
    self.ll_t.fill(0.0)
    self.ll_x = np.zeros(TRAJECTORY_SIZE)
    self.lll_y.fill(0.0)
    self.rll_y.fill(0.0)
    lanes_valid = False
    try:
      lanes = md.laneLines
      probs = np.asarray(md.laneLineProbs, dtype=float)
      stds = np.asarray(md.laneLineStds, dtype=float)
      if len(lanes) == 4 and len(probs) == 4 and len(stds) == 4:
        left, right = lanes[1], lanes[2]
        left_t, right_t = repair_lane_times(left.t), repair_lane_times(right.t)
        left_x, right_x = np.asarray(left.x), np.asarray(right.x)
        left_y, right_y = np.asarray(left.y), np.asarray(right.y)
        lanes_valid = (valid_lane_path(left_t, left_x, left_y, right_y) and
                       valid_lane_path(right_t, right_x, left_y, right_y) and
                       np.isfinite(probs[1:3]).all() and np.isfinite(stds[1:3]).all() and
                       np.all((probs[1:3] >= 0.0) & (probs[1:3] <= 1.0)) and
                       np.all(stds[1:3] >= 0.0))
        if lanes_valid:
          self.ll_t = (left_t + right_t) / 2.0
          self.ll_x = left_x.copy()
          self.lll_y = left_y + self.camera_offset
          self.rll_y = right_y + self.camera_offset
          self.lll_prob, self.rll_prob = probs[1:3]
          self.lll_std, self.rll_std = stds[1:3]
    except (AttributeError, IndexError, TypeError, ValueError):
      lanes_valid = False

    edges_valid = False
    try:
      edges = md.roadEdges
      stds = np.asarray(md.roadEdgeStds, dtype=float)
      if lanes_valid and len(edges) >= 2 and len(stds) >= 2:
        edges_valid = (valid_samples(edges[0].y, edges[1].y) and
                       valid_lane_times(repair_lane_times(edges[0].t)) and
                       valid_lane_times(repair_lane_times(edges[1].t)) and
                       np.isfinite(stds[:2]).all() and np.all(stds[:2] >= 0.0))
        if edges_valid:
          self.le_y = np.asarray(edges[0].y) + stds[0] * 0.4 + self.camera_offset
          self.re_y = np.asarray(edges[1].y) - stds[1] * 0.4 + self.camera_offset
          self.lane_width_left = float(max(0.0, abs(self.le_y[0]) - abs(self.lll_y[0])))
          self.lane_width_right = float(max(0.0, abs(self.re_y[0]) - abs(self.rll_y[0])))
    except (AttributeError, IndexError, TypeError, ValueError):
      edges_valid = False
    if not edges_valid:
      self.le_y.fill(0.0)
      self.re_y.fill(0.0)
      self.lane_width_left_filtered.x = self.lane_width_right_filtered.x = 0.0

    try:
      desire = md.meta.desireState
      left = float(desire[log.LateralPlan.Desire.laneChangeLeft])
      right = float(desire[log.LateralPlan.Desire.laneChangeRight])
      if np.isfinite([left, right]).all() and 0.0 <= left <= 1.0 and 0.0 <= right <= 1.0:
        self.l_lane_change_prob, self.r_lane_change_prob = left, right
    except (AttributeError, IndexError, TypeError, ValueError):
      pass

  def get_d_path(self, v_ego, path_t, path_xyz, lanelines_active, curve_speed=200.0):
    path_xyz = path_xyz.copy()
    lane_line_blend = float(clip(float(lanelines_active), 0.0, 1.0))
    # 같은 방향 커브가 이어진 시간. 직선이 되거나 방향이 바뀌면(S커브) 바로 0.
    curve_weight = curve_centering_weight(curve_speed)
    if curve_weight > 0.5 and np.sign(curve_speed) == self.curve_sign:
      self.curve_time += DT_MDL
    else:
      self.curve_time = 0.0
    self.curve_sign = np.sign(curve_speed) if curve_weight > 0.5 else 0.0
    self.curve_center_weight = curve_weight * interp(self.curve_time, CURVE_CENTER_DELAY_BP, [0.0, 1.0])

    lane_valid = (valid_lane_path(self.ll_t, self.ll_x, self.lll_y, self.rll_y) and
                  valid_samples(path_t) and np.all(np.diff(path_t) > 0.0))
    if not lane_valid:
      self.d_prob = 0.0
      # Preserve the model path with no stale lane-centering correction. Do not
      # feed NaNs to interpolation.
      return path_xyz
    l_prob, r_prob = self.lll_prob, self.rll_prob
    width_pts = self.rll_y - self.lll_y
    prob_mods = []
    for t_check in (0.0, 1.5, 3.0):
      width_at_t = interp(t_check * (v_ego + 7), self.ll_x, width_pts)
      prob_mods.append(interp(width_at_t, [4.5, 6.0], [1.0, 0.0]))
    mod = min(prob_mods)
    l_prob *= mod
    r_prob *= mod

    l_std_mod = interp(self.lll_std, [.15, .3], [1.0, 0.0])
    r_std_mod = interp(self.rll_std, [.15, .3], [1.0, 0.0])
    l_prob *= l_std_mod
    r_prob *= r_std_mod

    # Save validated confidence before the legacy width heuristic weakens only
    # the right line. A reliable pair defines its own centre, even if learned
    # lane width is slow to catch up on a ramp.
    center_strength = self.curve_center_weight * interp(min(l_prob, r_prob), [0.5, 0.7], [0.0, 1.0])

    if ENABLE_ZORROBYTE:
      if l_prob > 0.5 and r_prob > 0.5:
        self.frame += 1
        if self.frame > 20:
          self.frame = 0
          # 2.5m에서 잘라버리면 아래의 carrot c3 좁은 차로
          # 분기(self.lane_width < 2.5)가 실행될 수 없다. 비정상
          # 오인식은 2.0m 하한으로 방어하고 실제 좁은 차로는 유지한다.
          current_lane_width = clip(abs(self.rll_y[0] - self.lll_y[0]), 2.0, 3.5)
          self.readings.append(current_lane_width)
          self.lane_width = mean(self.readings)
          if len(self.readings) >= 30:
            self.readings.pop(0)

      if abs(self.rll_y[0] - self.lll_y[0]) > self.lane_width:
        r_prob = r_prob / interp(l_prob, [0, 1], [1, 3])

    else:
      self.lane_width_certainty.update(l_prob * r_prob)
      current_lane_width = abs(self.rll_y[0] - self.lll_y[0])
      self.lane_width_estimate.update(current_lane_width)
      speed_lane_width = interp(v_ego, [0., 31.], [2.8, 3.5])
      self.lane_width = self.lane_width_certainty.x * self.lane_width_estimate.x + \
                        (1 - self.lane_width_certainty.x) * speed_lane_width

    clipped_lane_width = min(4.0, self.lane_width)
    path_from_left_lane = self.lll_y + clipped_lane_width / 2.0
    path_from_right_lane = self.rll_y - clipped_lane_width / 2.0

    prev_d_prob = self.d_prob
    both_lane_available = l_prob > 0.5 and r_prob > 0.5
    self.d_prob = max(l_prob, r_prob) if not both_lane_available else 1.0

    if ENABLE_INC_LANE_PROB and self.d_prob > 0.65:
      self.d_prob = min(self.d_prob * 1.3, 1.0)
    if self.lane_change_fade:
      d_prob_step = DT_MDL / D_PROB_BLEND_TIME
      self.d_prob = float(clip(self.d_prob, prev_d_prob - d_prob_step, prev_d_prob + d_prob_step))

    # carrot c3: 좌/우 여유폭을 먼저 필터링한 뒤 좁은 차로의
    # 기준 차선과 오프셋 방향을 결정한다.
    if self.lane_width_left >= 0:
      self.lane_width_left_filtered.update(self.lane_width_left)
    if self.lane_width_right >= 0:
      self.lane_width_right_filtered.update(self.lane_width_right)

    # carrot c3: 좁아지는 차로에서는 도로 경계와 가까운 차선을
    # 기준으로 경로를 만들고, 일반 차로에서는 양쪽/신뢰도를 혼합한다.
    if self.lane_width < 2.5:
      if r_prob > 0.5 and self.lane_width_right_filtered.x < self.lane_width_left_filtered.x:
        lane_path_y = path_from_right_lane
      elif l_prob > 0.5 and self.lane_width_left_filtered.x < 2.0:
        lane_path_y = path_from_left_lane
      else:
        lane_path_y = path_from_left_lane if l_prob > 0.5 or l_prob > r_prob else path_from_right_lane
    elif l_prob > 0.7 and r_prob > 0.7:
      lane_path_y = (path_from_left_lane + path_from_right_lane) / 2.0
    else:
      lane_path_y = (l_prob * path_from_left_lane + r_prob * path_from_right_lane) / (l_prob + r_prob + 0.0001)

    lane_path_y = ((1.0 - center_strength) * lane_path_y +
                   center_strength * (self.lll_y + self.rll_y) / 2.0)

    safe_idxs = np.isfinite(self.ll_t)
    effective_d_prob = 0.0
    if safe_idxs[0] and lane_line_blend > 0.0:
      # Preserve NaN-padded lane times. Input lead stays on bends too: fading
      # it with measured curvature delayed turn-in on short curves (exit went
      # wide, 2026-10-08). Bend centring and the removed inward offset remain.
      # If lanes cover less than the full model horizon, fade their authority
      # over the last measured second rather than extending the endpoint.
      horizon_weights = lane_horizon_weights(self.ll_t, np.asarray(path_t))
      preview_t = np.minimum(path_t, self.ll_t[safe_idxs][-1])
      lane_path_y_interp = np.interp(preview_t,
                                     self.ll_t[safe_idxs], lane_path_y[safe_idxs])
      effective_d_prob = self.d_prob * lane_line_blend * horizon_weights
      path_xyz[:,1] = effective_d_prob * lane_path_y_interp + (1.0 - effective_d_prob) * path_xyz[:,1]
    elif not safe_idxs[0]:
      cloudlog.warning("Lateral mpc - NaNs in laneline times, ignoring")

    return path_xyz
