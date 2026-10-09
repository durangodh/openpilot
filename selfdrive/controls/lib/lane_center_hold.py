"""Hold the last reliable lane centre through a short gap in the lane lines.

차선 없는 긴 교차로를 레인리스로 직진하면 모델 경로가 건너편 출구 차선, 앞차,
연석을 보고 그때그때 다른 쪽으로 흘렀다(2026-10-09). 차선이 사라지기 직전의
차로 중앙선을 기억해 두고, 차량 이동(속도·곡률 적분)만큼 옮겨 가며 몇 초 동안
모델 경로를 그쪽으로 끌어준다. 직진 중에만 쓰고, 차선변경·깜빡이·회전 중에는
기억을 지운다.
"""
import math

import numpy as np

HOLD_TIME = 3.0             # s, 차선이 사라진 뒤 기억을 쓰는 시간 (마지막 1 s 동안 해제)
HOLD_BLEND = 0.5            # 모델 경로에 섞는 기억 중앙선의 최대 비중
HOLD_MAX_CURVATURE = 0.002  # 1/m, R 500 m 보다 완만한 직진에서만
STORE_PROB = 0.7            # 두 차선이 모두 이 이상이면 중앙선을 새로 기억
LOST_PROB = 0.5             # 한쪽이라도 이 아래면 차선이 사라진 것으로 본다
COVERAGE_FADE = 10.0        # m, 기억한 중앙선 끝 너머에서는 이 거리에 걸쳐 모델 경로로
BLEND_TIME = 0.5            # s, 비중 0 <-> HOLD_BLEND 전환 시간


class LaneCenterHold:
  def __init__(self):
    self.x = None
    self.y = None
    self.age = HOLD_TIME
    self.px = self.py = self.psi = 0.0
    self.enabled = False
    self.blend = 0.0
    self.dt = 0.05

  def update(self, enabled, lanes_good, v_ego, curvature, ll_x, center_y, dt):
    self.dt = dt
    self.enabled = enabled and abs(curvature) <= HOLD_MAX_CURVATURE
    if self.enabled and lanes_good:
      x = np.asarray(ll_x, dtype=float)
      y = np.asarray(center_y, dtype=float)
      keep = np.isfinite(x) & np.isfinite(y) & (x > 0.0)
      if np.count_nonzero(keep) >= 2 and np.all(np.diff(x[keep]) > 0.0):
        self.x, self.y = x[keep], y[keep]
        self.age = 0.0
        self.px = self.py = self.psi = 0.0
        return
    if self.x is not None:
      # 기억한 뒤 차량이 움직인 만큼(기억 시점 좌표계 기준) 적분한다.
      self.psi += v_ego * curvature * dt
      self.px += v_ego * math.cos(self.psi) * dt
      self.py += v_ego * math.sin(self.psi) * dt
      self.age += dt

  def apply(self, path_xyz, lanes_lost):
    target = 0.0
    if self.enabled and lanes_lost and self.x is not None and self.age < HOLD_TIME:
      target = HOLD_BLEND * float(np.interp(self.age, [HOLD_TIME - 1.0, HOLD_TIME], [1.0, 0.0]))
    # 켜고 끌 때(차선 재등장, 깜빡이, 커브) 경로가 튀지 않게 비중을 천천히 바꾼다.
    step = self.dt * HOLD_BLEND / BLEND_TIME
    self.blend += float(np.clip(target - self.blend, -step, step))
    if self.blend <= 0.0 or self.x is None:
      return path_xyz
    # 기억 시점 좌표 -> 현재 차량 좌표
    dx, dy = self.x - self.px, self.y - self.py
    c, s = math.cos(self.psi), math.sin(self.psi)
    xc = c * dx + s * dy
    yc = -s * dx + c * dy
    ahead = xc > 0.0
    if np.count_nonzero(ahead) < 2 or not np.all(np.diff(xc[ahead]) > 0.0):
      return path_xyz
    xc, yc = xc[ahead], yc[ahead]
    path_x = path_xyz[:, 0]
    w = self.blend * np.interp(path_x - xc[-1], [-COVERAGE_FADE, 0.0], [1.0, 0.0])
    out = path_xyz.copy()
    out[:, 1] = (1.0 - w) * path_xyz[:, 1] + w * np.interp(path_x, xc, yc)
    return out
