#!/usr/bin/env python3
"""CarrotLearning Phase 2 lateral auto-tuner for g_abcd.

Ported from ajouatom/openpilot hoya/c3-atune Phase 2 and adapted to
this older fork. Learns/recommends:
  PathOffset, SteerActuatorDelay, SteerRatioRate,
  LateralTorqueAccelFactor, LateralTorqueKf, LateralTorqueFriction,
  LateralTorqueKpV, LateralTorqueKiV.

Recommendations are generated from 10 Hz samples and are only applied
in PARK. Manual confirmation remains the default; auto-apply is opt-in.
"""
import json
import time

from cereal import car
from common.params import Params

GearShifter = car.CarState.GearShifter

_SAMPLE_EVERY_N_TICKS = 10
_PARAMS_REFRESH_TICKS = 100
_MIN_SPEED_KPH = 20.0
_STRAIGHT_DEG = 5.0
_CURVE_DEG = 8.0
_CURVE_CURVATURE = 0.0025
_LATERAL_MIN_SAMPLES = 200
_CURVE_MIN_SAMPLES = 100

_PATH_OFFSET_LC_MIN_M = 0.08
_PATH_OFFSET_LC_GAIN = 0.5
_PATH_OFFSET_STEP_MAX = 10
_PATH_OFFSET_ABS_MAX = 30

_DELAY_STEP = 10
_DELAY_MIN, _DELAY_MAX = 15, 60
_DELAY_AUTO_BASELINE = 20

_SR_RATE_STEP = 3
_SR_RATE_MIN, _SR_RATE_MAX = 90, 150

# LateralTorqueCustom(수동 토크튠)이 켜져 있으면 아래 키들은 사용자 값이므로
# 학습이 건드리지 않는다. 추천 생성·자동적용 양쪽에서 제외된다.
_MANUAL_TORQUE_KEYS = (
  "LateralTorqueAccelFactor", "LateralTorqueKf", "LateralTorqueFriction",
  "LateralTorqueKpV", "LateralTorqueKiV",
)

_FACTOR_MIN, _FACTOR_MAX = 1000, 6000
_KF_MIN, _KF_MAX = 0, 200
_FRICTION_MIN, _FRICTION_MAX = 10, 300

# friction 은 조향오차가 0 에 가까울 때도 상시 토크를 얹는 항이라, 누적 상승이
# 그대로 MDPS 부하가 된다. 실제로 50 -> 74 로 올라간 뒤 5 분 만에 MDPS 폴트가
# 났다(2026-08-18). 원본(ajouatom hoya/c3-atune)도 스텝은 +5/-2 로 같지만
# 상한이 300 이라 매 주행 +5 씩 무한 누적되는 것을 막지 못한다.
# 그래서 사용자의 수동값을 기준선으로 잡고 그 ±20% 밖으로는 학습이 못 나가게 한다.
_FRICTION_BASE_KEY = "CarrotLearnFrictionBase"
_FRICTION_BAND = 0.20      # 기준선 대비 허용 폭
_FRICTION_UP_STEP = 2      # 원본 +5 -> 하향 스텝(-2)과 대칭으로 맞춰 래칫 방지
_FRICTION_DOWN_STEP = 2
_KPV_MIN, _KPV_MAX = 30, 200
_KIV_MIN, _KIV_MAX = 0, 50

_TORQUE_OSC_MIN = 8
_TORQUE_STEADY_MIN = 25
_TORQUE_KPV_STEP = 5
_TORQUE_KF_FF_PREF = 100
_PINGPONG_MIN_KPH = 70.0
_PINGPONG_DEG = 0.4
_PINGPONG_MIN_COUNT = 15

# g_abcd / Genesis DH current defaults. Source Phase2 logic is retained,
# but stable-state decay returns toward this branch's own baseline.
_DEFAULTS = {
  "PathOffset": 0,
  "SteerActuatorDelay": 50,
  "SteerRatioRate": 100,
  "LateralTorqueAccelFactor": 2500,
  "LateralTorqueKf": 85,
  "LateralTorqueFriction": 10,
  "LateralTorqueKpV": 70,
  "LateralTorqueKiV": 20,
  "LateralTorqueCustom": 0,
}

_BOUNDS = {
  "PathOffset": (-_PATH_OFFSET_ABS_MAX, _PATH_OFFSET_ABS_MAX),
  "SteerActuatorDelay": (_DELAY_MIN, _DELAY_MAX),
  "SteerRatioRate": (_SR_RATE_MIN, _SR_RATE_MAX),
  "LateralTorqueAccelFactor": (_FACTOR_MIN, _FACTOR_MAX),
  "LateralTorqueKf": (_KF_MIN, _KF_MAX),
  "LateralTorqueFriction": (_FRICTION_MIN, _FRICTION_MAX),
  "LateralTorqueKpV": (_KPV_MIN, _KPV_MAX),
  "LateralTorqueKiV": (_KIV_MIN, _KIV_MAX),
}


def _clip(v, lo, hi):
  return max(lo, min(hi, int(v)))


def _decay_toward(current, target, step):
  if current < target:
    return min(target, current + step)
  if current > target:
    return max(target, current - step)
  return current


class CarrotLatLearner:
  def __init__(self):
    self._params = Params()
    self._frame = 0
    self._was_park = True
    self._active_cached = False
    self._apply_lat_cached = True
    self._manual_torque_cached = False
    self._recommend = {}
    self._reset_phase2()

  def _reset_phase2(self):
    self._lane_center_acc = 0.0
    self._lane_center_n = 0
    self._curve_entries = 0
    self._curve_overrides = 0
    self._curve_understeer = 0
    self._curve_inner_hugging = 0
    self._straight_entries = 0
    self._straight_overrides = 0
    self._torque_error_count = 0
    self._torque_osc_count = 0
    self._torque_steady_count = 0
    self._straight_reversal = 0
    self._prev_torque_err_sign = 0
    self._prev_straight_steer_sign = 0

  def _refresh_switches(self):
    self._active_cached = self._params.get_bool("CarrotLearningActive")
    raw = self._params.get("CarrotTunerApplyLat")
    self._apply_lat_cached = True if raw is None else self._params.get_bool("CarrotTunerApplyLat")
    self._manual_torque_cached = self._get_int("LateralTorqueCustom") > 0

  def _get_int(self, key):
    return self._params.get_int(key, _DEFAULTS.get(key, 0))

  def _friction_bounds(self):
    """기준선 ±_FRICTION_BAND 를 friction 학습 범위로 돌려준다.

    기준선은 사용자가 손으로 맞춘 값이다. 저장된 기준선이 없거나, 사용자가
    UI 에서 범위 밖으로 직접 바꾸면 그 값을 새 기준선으로 다시 잡는다.
    (수동으로 되돌린 값이 즉시 새 기준이 되므로 복구 순서를 신경쓸 필요가 없다)
    """
    current = self._get_int("LateralTorqueFriction")
    base = self._params.get_int(_FRICTION_BASE_KEY, 0)
    if base <= 0:
      base = current
      self._params.put_int(_FRICTION_BASE_KEY, base)

    lo = max(_FRICTION_MIN, int(round(base * (1.0 - _FRICTION_BAND))))
    hi = min(_FRICTION_MAX, int(round(base * (1.0 + _FRICTION_BAND))))
    if current < lo or current > hi:
      # 사용자가 직접 바꾼 값 -> 새 기준선
      base = current
      self._params.put_int(_FRICTION_BASE_KEY, base)
      lo = max(_FRICTION_MIN, int(round(base * (1.0 - _FRICTION_BAND))))
      hi = min(_FRICTION_MAX, int(round(base * (1.0 + _FRICTION_BAND))))
    return current, lo, hi

  def _add_recommendation(self, key, current, recommended, reason, **extra):
    if recommended == current:
      return False
    if self._manual_torque_cached and key in _MANUAL_TORQUE_KEYS:
      return False
    lo, hi = _BOUNDS[key]
    rec = {
      "current": int(current),
      "recommend": _clip(recommended, lo, hi),
      "reason": reason,
    }
    rec.update(extra)
    self._recommend[key] = rec
    return True

  def tick(self, CS, latActive, desired_curvature, desired_angle_deg=0.0, lane_center_y=None):
    self._frame += 1
    is_park = CS.gearShifter == GearShifter.park

    if self._frame % _PARAMS_REFRESH_TICKS == 0 or self._frame == 1:
      self._refresh_switches()
      # 2026-08-18: 팝업에서 수락해도 이 Python 카운터/추천 dict가 리셋되지
      # 않던 문제. onroad.cc가 Params 값만 직접 쓰고 apply_recommendations()를
      # 호출할 방법이 없어서, self._recommend에 남은 "이미 적용된" 항목의
      # current 스냅샷이 갱신되지 않은 채 다음 _save() 때 그대로 다시
      # 올라가 팝업이 중복/낡은 값으로 재표시될 수 있었다. 적용 여부와
      # 상관없이 리셋의 단일 소스를 Python으로 만든다: 팝업 수락 시
      # onroad.cc는 이 플래그만 세우고, 실제 적용+리셋은 여기서 처리.
      if self._params.get_bool("CarrotLearningApplyNow"):
        self._params.put_bool("CarrotLearningApplyNow", False)
        if is_park and self._recommend:
          self.apply_recommendations()

    if is_park and not self._was_park:
      self._on_park()
    self._was_park = is_park

    if not self._active_cached or not self._apply_lat_cached:
      return
    if self._frame % _SAMPLE_EVERY_N_TICKS != 0:
      return

    v_kph = float(CS.vEgo) * 3.6
    if not latActive or v_kph < _MIN_SPEED_KPH:
      return

    steer_deg = float(CS.steeringAngleDeg)
    desired_angle = float(desired_angle_deg)
    steer_err = desired_angle - steer_deg
    exclude_override = bool(CS.leftBlinker or CS.rightBlinker)

    # Straight/lane-center samples: same lane-center signal used by c3-atune.
    if abs(steer_deg) < _STRAIGHT_DEG:
      self._straight_entries += 1
      self._prev_torque_err_sign = 0
      if CS.steeringPressed and not exclude_override:
        self._straight_overrides += 1

      if lane_center_y is not None:
        try:
          y = float(lane_center_y)
          if abs(y) < 2.0:
            self._lane_center_acc += y
            self._lane_center_n += 1
        except (TypeError, ValueError):
          pass

      # High-speed straight ping-pong: repeated wheel-angle sign reversals
      # without driver input are treated as excessive feedback gain.
      if v_kph >= _PINGPONG_MIN_KPH and not CS.steeringPressed and abs(steer_deg) >= _PINGPONG_DEG:
        s = 1 if steer_deg > 0 else -1
        if self._prev_straight_steer_sign and s != self._prev_straight_steer_sign:
          self._straight_reversal += 1
        self._prev_straight_steer_sign = s
      elif abs(steer_deg) < _PINGPONG_DEG:
        self._prev_straight_steer_sign = 0

    # Curve samples. Use both actual wheel angle and requested curvature so
    # low-angle high-speed curves are not missed.
    in_curve = abs(steer_deg) >= _CURVE_DEG or abs(float(desired_curvature)) >= _CURVE_CURVATURE
    if in_curve:
      self._curve_entries += 1
      if CS.steeringPressed and not exclude_override:
        self._curve_overrides += 1
        direction = desired_angle * steer_err
        if direction < 0:
          self._curve_understeer += 1
        elif direction > 0:
          self._curve_inner_hugging += 1

      if abs(steer_err) >= 1.5:
        self._torque_error_count += 1
        s = 1 if steer_err > 0 else -1
        if self._prev_torque_err_sign:
          if s != self._prev_torque_err_sign:
            self._torque_osc_count += 1
          else:
            self._torque_steady_count += 1
        self._prev_torque_err_sign = s

    changed = False
    if self._lane_center_n >= _LATERAL_MIN_SAMPLES:
      changed |= self._evaluate_path_offset()
      self._lane_center_acc = 0.0
      self._lane_center_n = 0

    if self._curve_entries >= _CURVE_MIN_SAMPLES:
      changed |= self._evaluate_curve()
      self._curve_entries = 0
      self._curve_overrides = 0
      self._curve_understeer = 0
      self._curve_inner_hugging = 0
      self._torque_error_count = 0
      self._torque_osc_count = 0
      self._torque_steady_count = 0
      self._prev_torque_err_sign = 0

    if self._straight_entries >= _LATERAL_MIN_SAMPLES:
      changed |= self._evaluate_straight()
      self._straight_entries = 0
      self._straight_overrides = 0
      self._straight_reversal = 0
      self._prev_straight_steer_sign = 0

    if changed:
      self._save()

  def _evaluate_path_offset(self):
    avg_lc = self._lane_center_acc / max(1, self._lane_center_n)
    if abs(avg_lc) < _PATH_OFFSET_LC_MIN_M:
      return False
    current = self._get_int("PathOffset")
    delta = _clip(round(avg_lc * 100.0 * _PATH_OFFSET_LC_GAIN),
                  -_PATH_OFFSET_STEP_MAX, _PATH_OFFSET_STEP_MAX)
    target = _clip(current + delta, -_PATH_OFFSET_ABS_MAX, _PATH_OFFSET_ABS_MAX)
    return self._add_recommendation(
      "PathOffset", current, target,
      f"lane-center mean {avg_lc:+.3f}m",
      lane_center_m=round(avg_lc, 3))

  def _evaluate_curve(self):
    override_ratio = self._curve_overrides / max(1, self._curve_entries)
    under_ratio = self._curve_understeer / max(1, self._curve_overrides)
    inner_ratio = self._curve_inner_hugging / max(1, self._curve_overrides)
    changed = False

    # SteerActuatorDelay: c3-atune bi-directional logic.
    current_delay = self._get_int("SteerActuatorDelay")
    base_delay = current_delay if current_delay > 0 else _DELAY_AUTO_BASELINE
    target_delay = current_delay
    if override_ratio >= 0.30:
      if under_ratio >= 0.60:
        target_delay = min(_DELAY_MAX, base_delay + _DELAY_STEP)
      elif inner_ratio >= 0.60:
        target_delay = max(_DELAY_MIN, base_delay - _DELAY_STEP)
    changed |= self._add_recommendation(
      "SteerActuatorDelay", current_delay, target_delay,
      "curve understeer: delay up" if target_delay > current_delay else "curve inner-hugging: delay down",
      override_ratio=round(override_ratio * 100, 1))

    # SteerRatioRate: retain current live/custom SR source, learn only a multiplier.
    current_sr = self._get_int("SteerRatioRate")
    if current_sr <= 0:
      current_sr = 100
    target_sr = current_sr
    if override_ratio >= 0.40:
      if under_ratio >= 0.60:
        target_sr = min(_SR_RATE_MAX, current_sr + _SR_RATE_STEP)
      elif inner_ratio >= 0.60:
        target_sr = max(_SR_RATE_MIN, current_sr - _SR_RATE_STEP)
    elif override_ratio < 0.15 and current_sr > 100:
      target_sr = max(100, current_sr - 1)
    changed |= self._add_recommendation(
      "SteerRatioRate", current_sr, target_sr,
      "curve steering-ratio compensation",
      override_ratio=round(override_ratio * 100, 1))

    # Torque feed-forward pair: AccelFactor denominator + Kf gain.
    current_factor = self._get_int("LateralTorqueAccelFactor")
    current_kf = self._get_int("LateralTorqueKf")
    target_factor = current_factor
    target_kf = current_kf
    steer_dir = "stable"
    if override_ratio >= 0.40 and under_ratio >= 0.60:
      target_factor = _clip(current_factor - 100, _FACTOR_MIN, _FACTOR_MAX)
      target_kf = _clip(current_kf + 3, _KF_MIN, _KF_MAX)
      steer_dir = "understeer"
    elif override_ratio >= 0.40 and inner_ratio >= 0.60:
      target_factor = _clip(current_factor + 100, _FACTOR_MIN, _FACTOR_MAX)
      target_kf = _clip(current_kf - 3, _KF_MIN, _KF_MAX)
      steer_dir = "inner_hugging"
    elif override_ratio < 0.15:
      target_kf = _decay_toward(current_kf, _DEFAULTS["LateralTorqueKf"], 1)

    changed |= self._add_recommendation(
      "LateralTorqueAccelFactor", current_factor, target_factor,
      f"torque feed-forward {steer_dir}",
      override_ratio=round(override_ratio * 100, 1))
    changed |= self._add_recommendation(
      "LateralTorqueKf", current_kf, target_kf,
      f"torque feed-forward {steer_dir}",
      override_ratio=round(override_ratio * 100, 1))

    # Feedback gains: oscillation first; steady lag only after FF is sufficient.
    current_kpv = self._get_int("LateralTorqueKpV")
    current_kiv = self._get_int("LateralTorqueKiV")
    target_kpv = current_kpv
    target_kiv = current_kiv
    kp_dir = ""
    curve_osc = self._torque_osc_count >= _TORQUE_OSC_MIN and self._torque_osc_count > self._torque_steady_count
    straight_pingpong = self._straight_reversal >= _PINGPONG_MIN_COUNT
    if straight_pingpong or curve_osc:
      target_kpv = max(_KPV_MIN, current_kpv - _TORQUE_KPV_STEP)
      target_kiv = max(_KIV_MIN, current_kiv - 1)
      kp_dir = "oscillation"
    elif self._torque_steady_count >= _TORQUE_STEADY_MIN and self._torque_osc_count < _TORQUE_OSC_MIN:
      if current_kf >= _TORQUE_KF_FF_PREF:
        target_kpv = min(_KPV_MAX, current_kpv + _TORQUE_KPV_STEP)
        target_kiv = min(_KIV_MAX, current_kiv + 1)
      kp_dir = "steady_lag"
    elif self._torque_error_count < 20:
      target_kpv = _decay_toward(current_kpv, _DEFAULTS["LateralTorqueKpV"], 2)
      target_kiv = _decay_toward(current_kiv, _DEFAULTS["LateralTorqueKiV"], 1)
      kp_dir = "good"

    changed |= self._add_recommendation(
      "LateralTorqueKpV", current_kpv, target_kpv,
      f"feedback {kp_dir}", error_ticks=self._torque_error_count,
      osc_ticks=self._torque_osc_count + self._straight_reversal)
    changed |= self._add_recommendation(
      "LateralTorqueKiV", current_kiv, target_kiv,
      f"feedback {kp_dir}", error_ticks=self._torque_error_count,
      osc_ticks=self._torque_osc_count + self._straight_reversal)
    return changed

  def _evaluate_straight(self):
    ratio = self._straight_overrides / max(1, self._straight_entries)
    current, lo, hi = self._friction_bounds()
    target = current
    if ratio >= 0.35:
      target = min(hi, current + _FRICTION_UP_STEP)
    elif ratio < 0.08:
      target = max(lo, current - _FRICTION_DOWN_STEP)
    return self._add_recommendation(
      "LateralTorqueFriction", current, target,
      "straight micro-correction friction compensation",
      override_ratio=round(ratio * 100, 1))

  def _save(self):
    data = {
      "updated": time.time(),
      "phase": 2,
      "recommendations": list(self._recommend.keys()),
    }
    self._params.put("CarrotLearningData", json.dumps(data).encode("utf8"))
    self._params.put("CarrotLearningRecommend", json.dumps(self._recommend).encode("utf8"))
    if not self._params.get_bool("CarrotLearningAutoApply"):
      self._params.put_bool("CarrotLearningPopupReady", True)

  def _on_park(self):
    if self._recommend and self._params.get_bool("CarrotLearningAutoApply"):
      self.apply_recommendations()

  def apply_recommendations(self):
    manual_torque = self._get_int("LateralTorqueCustom") > 0
    _, friction_lo, friction_hi = self._friction_bounds()
    for key, rec in list(self._recommend.items()):
      if key not in _BOUNDS:
        continue
      if manual_torque and key in _MANUAL_TORQUE_KEYS:
        continue
      lo, hi = _BOUNDS[key]
      if key == "LateralTorqueFriction":
        # 추천을 만든 뒤 사용자가 값을 바꿨을 수 있으므로 적용 직전에 다시 막는다
        lo, hi = friction_lo, friction_hi
      value = _clip(rec.get("recommend", self._get_int(key)), lo, hi)
      self._params.put(key, str(value))
    self._recommend = {}
    self._params.remove("CarrotLearningRecommend")
    self._params.put_bool("CarrotLearningPopupReady", False)
