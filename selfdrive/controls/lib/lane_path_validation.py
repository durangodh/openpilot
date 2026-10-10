"""Validate lane samples before they influence a model path."""
import numpy as np


def valid_samples(*arrays, size=33):
  try:
    return all(np.asarray(a).shape == (size,) and np.isfinite(a).all() for a in arrays)
  except (TypeError, ValueError):
    return False


def valid_lane_path(t, x, left, right):
  return (valid_samples(x, left, right) and valid_lane_times(t) and
          bool(np.all(np.diff(x) > 0.0)))


def valid_lane_times(t):
  # C2 modeld emits a finite prefix ending at 10s and NaN padding when the
  # position plan does not reach all 192m lane samples. This is NORMAL at low
  # speeds. Reject holes/reversed times/Inf, not this documented trailing pad.
  try:
    t = np.asarray(t, dtype=float)
    if t.shape != (33,) or np.isinf(t).any():
      return False
    count = int(np.isfinite(t).sum())
    return (count >= 2 and bool(np.isfinite(t[:count]).all()) and
            bool(np.isnan(t[count:]).all()) and t[0] >= 0.0 and
            bool(np.all(np.diff(t[:count]) > 0.0)))
  except (TypeError, ValueError):
    return False


def covers_lane_horizon(t, query_t):
  """C2's N=32 lateral MPC consumes all 33 points, including the terminal."""
  if not valid_lane_times(t) or not valid_samples(query_t) or not np.all(np.diff(query_t) > 0.0):
    return False
  finite_t = np.asarray(t)[np.isfinite(t)]
  return finite_t[0] <= query_t[0] + 1e-6 and finite_t[-1] + 1e-6 >= query_t[-1]


def lane_horizon_weights(t, query_t):
  """Use measured lanes nearby; smoothly return to the model beyond coverage."""
  if covers_lane_horizon(t, query_t):
    return np.ones(len(query_t))
  finite_t = np.asarray(t)[np.isfinite(t)]
  end = float(finite_t[-1])
  start = max(float(query_t[0]), end - 1.0)
  if end <= start:
    return np.zeros(len(query_t))
  weights = np.interp(query_t, [start, end], [1.0, 0.0])
  weights[np.asarray(query_t) < finite_t[0]] = 0.0
  return weights


def curve_centering_weight(curve_speed):
  """Signed curve speed is derived from measured curvature, in km/h.

  Retain input lead near straight travel; fade it away as curvature grows.
  The weight has no timer, so the same geometry works on short and long bends.
  """
  return float(np.interp(abs(curve_speed), [150.0, 200.0], [1.0, 0.0]))


def curve_lane_center_blend(base_blend, curve_weight, lane_prob, lane_std, lane_width_max):
  width_confidence = np.interp(lane_width_max, [4.5, 6.0], [1.0, 0.0])
  std_confidence = np.interp(lane_std, [.15, .3], [1.0, 0.0])
  confidence = np.interp(lane_prob * width_confidence * std_confidence,
                         [0.5, 0.7], [0.0, 1.0])
  # curve_weight: LanePlanner.curve_center_weight (커브가 이어질 때만 커진다)
  weight = curve_weight * confidence
  return float(base_blend + weight * (1.0 - base_blend))


# 차로변경 시작 직후, 모델 경로가 잠깐 반대쪽(현재 차로 쪽)으로 튀었다
# (2026-10-10 lat_trace 54.0 s, 레인리스, 오른쪽 변경: 0.2초에 왼쪽으로 0.6 m).
# 변경 방향 쪽 움직임은 그대로 두고, 반대쪽 움직임만 처음 1초는 아주 느리게,
# 그 뒤 0.5초 동안 서서히 풀어 준다(풀 때 경로가 툭 따라붙지 않게).
LC_START_COUNTER_RATE = 0.2       # m/s, 반대쪽으로 움직일 수 있는 속도(처음 1초)
LC_START_HOLD_TIME = 1.0          # s
LC_START_RELEASE_TIME = 1.5       # s, 이때까지 반대쪽 제한을 RELEASE_RATE 로 풀고 이후 해제
LC_START_RELEASE_RATE = 3.0       # m/s


def limit_lane_change_start(prev_y, new_y, timer, direction, dt):
  """direction: +1 왼쪽 변경(y 증가), -1 오른쪽 변경(y 감소), 0 이면 그대로."""
  if prev_y is None or direction == 0 or timer > LC_START_RELEASE_TIME:
    return new_y
  prev_y = np.asarray(prev_y, dtype=float)
  new_y = np.asarray(new_y, dtype=float)
  if prev_y.shape != new_y.shape or not (np.all(np.isfinite(prev_y)) and np.all(np.isfinite(new_y))):
    return new_y
  if timer <= LC_START_HOLD_TIME:
    rate = LC_START_COUNTER_RATE
  else:
    frac = (timer - LC_START_HOLD_TIME) / (LC_START_RELEASE_TIME - LC_START_HOLD_TIME)
    rate = LC_START_COUNTER_RATE + frac * (LC_START_RELEASE_RATE - LC_START_COUNTER_RATE)
  step = new_y - prev_y
  counter = step * direction < 0.0
  limited = np.where(counter, np.sign(step) * np.minimum(np.abs(step), rate * dt), step)
  return prev_y + limited

