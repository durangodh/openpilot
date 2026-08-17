#!/usr/bin/env python3
"""Carrot Phase 2 lateral auto-tuner adapted for g_abcd / Genesis DH.

hoya/c3-atune Phase 2 equivalent mapping for this older fork:
  SteerActuatorDelay -> SteerActuatorDelay
  SteerRatioRate     -> CustomSteerRatio (this fork has no SteerRatioRate consumer)
  PathOffset         -> OffsetTotal (this fork's live lane/path offset input)
  torque tuning      -> LateralTorqueAccelFactor/Kf/Friction/KpV/KiV

Recommendations are collected while driving and only applied in PARK.  When
CarrotLearningAutoApply is off the existing onroad confirmation popup applies
them instead.  LateralTorqueCustom remains the highest-priority manual override.
"""
import json
import math
import time

from cereal import car
from common.params import Params

GearShifter = car.CarState.GearShifter

_SAMPLE_EVERY_N_TICKS = 10       # controlsd 100 Hz -> learner 10 Hz
_PARAMS_REFRESH_TICKS = 100
_MIN_ENGAGE_SPEED = 5.0          # m/s
_CURVE_MIN_KPH = 40.0
_CURVE_MIN_SAMPLES = 100         # ~10 s
_STRAIGHT_MIN_SAMPLES = 200      # ~20 s
_CURVE_CURVATURE_THRESHOLD = 0.0025
_STRAIGHT_CURVATURE_THRESHOLD = 0.0015
_STRAIGHT_STEER_DEG = 5.0

# Genesis DH conservative delay limits (units x100 s)
_DELAY_STEP = 5
_DELAY_MIN, _DELAY_MAX = 15, 40
_DELAY_DEFAULT = 25

# g_abcd equivalent of atune SteerRatioRate: directly tune fixed ratio x100.
_SR_STEP = 20                     # 0.20 per recommendation
_SR_MIN, _SR_MAX = 1200, 1900
_SR_DEFAULT = 1650

# OffsetTotal is metres in this fork.
_OFFSET_MIN_M, _OFFSET_MAX_M = -0.30, 0.30
_OFFSET_LC_MIN_M = 0.08
_OFFSET_GAIN = 0.5
_OFFSET_STEP_MAX_M = 0.10

# Torque Phase-2 bounds / steps, following hoya/c3-atune intent.
_FACTOR_MIN, _FACTOR_MAX, _FACTOR_STEP = 1000, 6000, 100
_KF_MIN, _KF_MAX, _KF_STEP = 0, 200, 3
_FRICTION_MIN, _FRICTION_MAX = 10, 300
_KPV_MIN, _KPV_MAX, _KPV_STEP = 30, 200, 5
_KIV_MIN, _KIV_MAX, _KIV_STEP = 0, 50, 1
_TORQUE_OSC_MIN = 8
_TORQUE_STEADY_MIN = 25
_TORQUE_PINGPONG_MIN_KPH = 70.0
_TORQUE_PINGPONG_DEG = 0.4
_TORQUE_PINGPONG_MIN_COUNT = 15


def _clip(v, lo, hi):
  return max(lo, min(hi, v))


class CarrotLatLearner:
  def __init__(self, CP=None):
    self._params = Params()
    self._frame = 0
    self._was_park = True
    self._active_cached = False
    self._apply_lat_cached = True

    self._wheelbase = float(getattr(CP, 'wheelbase', 2.85) or 2.85)
    self._cp_steer_ratio = float(getattr(CP, 'steerRatio', 16.5) or 16.5)

    self._recommend = {}
    self._reset_window()

  def _reset_window(self):
    self._curve_samples = 0
    self._curve_overrides = 0
    self._understeer = 0
    self._inner_hugging = 0
    self._torque_osc_count = 0
    self._torque_steady_count = 0
    self._prev_err_sign = 0

    self._straight_samples = 0
    self._straight_overrides = 0
    self._straight_reversals = 0
    self._prev_straight_sign = 0
    self._lane_center_sum = 0.0
    self._lane_center_n = 0

  def _refresh_switches(self):
    self._active_cached = self._params.get_bool("CarrotLearningActive")
    raw = self._params.get("CarrotTunerApplyLat")
    self._apply_lat_cached = True if raw is None else self._params.get_bool("CarrotTunerApplyLat")

  def _get_int(self, key, default):
    try:
      v = self._params.get(key, encoding="utf8")
      return int(float(v)) if v not in (None, "") else default
    except (TypeError, ValueError):
      return default

  def _get_float(self, key, default):
    try:
      v = self._params.get(key, encoding="utf8")
      return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
      return default

  def _desired_angle_deg(self, desired_curvature):
    # Bicycle-model steering-wheel angle approximation.  This is only used to
    # classify correction direction/oscillation, never to command steering.
    ratio = self._get_float("CustomSteerRatio", self._cp_steer_ratio * 100.0) * 0.01
    return math.degrees(math.atan(self._wheelbase * float(desired_curvature))) * ratio

  def _sample_lane_center(self, model_data):
    if model_data is None:
      return
    try:
      ll = model_data.laneLines
      lp = model_data.laneLineProbs
      if len(ll) < 4 or len(lp) < 4 or lp[1] <= 0.5 or lp[2] <= 0.5:
        return
      # Inner lane lines; 5 is far enough ahead to reject near-field noise.
      idx = 5
      if len(ll[1].y) <= idx or len(ll[2].y) <= idx:
        return
      center_y = (float(ll[1].y[idx]) + float(ll[2].y[idx])) * 0.5
      if math.isfinite(center_y) and abs(center_y) < 1.5:
        self._lane_center_sum += center_y
        self._lane_center_n += 1
    except (AttributeError, IndexError, TypeError, ValueError):
      pass

  def tick(self, CS, latActive, desired_curvature, model_data=None):
    self._frame += 1
    if self._frame % _PARAMS_REFRESH_TICKS == 0 or self._frame == 1:
      self._refresh_switches()

    is_park = CS.gearShifter == GearShifter.park
    if is_park and not self._was_park:
      self._evaluate_all(force=True)
      self._on_park()
    self._was_park = is_park

    if not self._active_cached or not self._apply_lat_cached:
      return
    if self._frame % _SAMPLE_EVERY_N_TICKS != 0:
      return
    if not latActive or CS.vEgo < _MIN_ENGAGE_SPEED:
      return

    desired_angle = self._desired_angle_deg(desired_curvature)
    steer_deg = float(CS.steeringAngleDeg)
    steer_err = desired_angle - steer_deg
    v_kph = float(CS.vEgo) * 3.6

    # Straight / gentle-road data: OffsetTotal, friction and high-speed ping-pong.
    if abs(desired_curvature) < _STRAIGHT_CURVATURE_THRESHOLD and abs(steer_deg) < _STRAIGHT_STEER_DEG:
      self._straight_samples += 1
      if CS.steeringPressed:
        self._straight_overrides += 1
      else:
        self._sample_lane_center(model_data)

      if v_kph >= _TORQUE_PINGPONG_MIN_KPH and abs(steer_deg) >= _TORQUE_PINGPONG_DEG:
        s = 1 if steer_deg > 0 else -1
        if self._prev_straight_sign and s != self._prev_straight_sign:
          self._straight_reversals += 1
        self._prev_straight_sign = s

    # Curve data: delay/ratio, feed-forward and feedback gains.
    if v_kph >= _CURVE_MIN_KPH and abs(desired_curvature) >= _CURVE_CURVATURE_THRESHOLD:
      self._curve_samples += 1
      if CS.steeringPressed:
        self._curve_overrides += 1
        # Preserve c3-atune direction test semantics.
        if desired_angle * steer_err > 0:
          self._inner_hugging += 1
        elif desired_angle * steer_err < 0:
          self._understeer += 1

      if abs(steer_err) >= 1.5:
        s = 1 if steer_err > 0 else -1
        if self._prev_err_sign:
          if s != self._prev_err_sign:
            self._torque_osc_count += 1
          else:
            self._torque_steady_count += 1
        self._prev_err_sign = s

    if self._curve_samples >= _CURVE_MIN_SAMPLES or self._straight_samples >= _STRAIGHT_MIN_SAMPLES:
      self._evaluate_all()
      self._reset_window()

  def _put_rec(self, key, current, recommended, reason):
    if recommended == current:
      return False
    self._recommend[key] = {
      "current": current,
      "recommend": recommended,
      "reason": reason,
    }
    return True

  def _evaluate_all(self, force=False):
    changed = False

    # PathOffset equivalent: OffsetTotal from measured lane-center error.
    if self._lane_center_n >= (_STRAIGHT_MIN_SAMPLES if not force else 40):
      avg_lc = self._lane_center_sum / max(1, self._lane_center_n)
      if abs(avg_lc) >= _OFFSET_LC_MIN_M:
        cur = self._get_float("OffsetTotal", 0.0)
        delta = _clip(avg_lc * _OFFSET_GAIN, -_OFFSET_STEP_MAX_M, _OFFSET_STEP_MAX_M)
        target = round(_clip(cur + delta, _OFFSET_MIN_M, _OFFSET_MAX_M), 2)
        changed |= self._put_rec("OffsetTotal", round(cur, 2), target,
                                 f"lane center error {avg_lc:+.2f}m")

    if self._curve_samples >= (_CURVE_MIN_SAMPLES if not force else 30):
      override_ratio = self._curve_overrides / max(1, self._curve_samples)
      under_ratio = self._understeer / max(1, self._curve_overrides)
      inner_ratio = self._inner_hugging / max(1, self._curve_overrides)

      # Delay: bidirectional, unlike the old g_abcd learner.
      cur_delay = self._get_int("SteerActuatorDelay", _DELAY_DEFAULT)
      target_delay = cur_delay
      if override_ratio >= 0.30 and under_ratio >= 0.60:
        target_delay = min(_DELAY_MAX, cur_delay + _DELAY_STEP)
      elif override_ratio >= 0.30 and inner_ratio >= 0.60:
        target_delay = max(_DELAY_MIN, cur_delay - _DELAY_STEP)
      changed |= self._put_rec("SteerActuatorDelay", cur_delay, target_delay,
                               f"curve override={override_ratio:.0%}, under={under_ratio:.0%}, inner={inner_ratio:.0%}")

      # SteerRatioRate equivalent in this fork: CustomSteerRatio.
      cur_sr = self._get_int("CustomSteerRatio", _SR_DEFAULT)
      target_sr = cur_sr
      if override_ratio >= 0.45 and under_ratio >= 0.65:
        target_sr = min(_SR_MAX, cur_sr + _SR_STEP)
      elif override_ratio >= 0.45 and inner_ratio >= 0.65:
        target_sr = max(_SR_MIN, cur_sr - _SR_STEP)
      changed |= self._put_rec("CustomSteerRatio", cur_sr, target_sr,
                               f"steer-ratio correction under={under_ratio:.0%}, inner={inner_ratio:.0%}")

      # Feed-forward torque pair: factor is a denominator, Kf is direct gain.
      factor = self._get_int("LateralTorqueAccelFactor", 2500)
      kf = self._get_int("LateralTorqueKf", 100)
      target_factor, target_kf = factor, kf
      if override_ratio >= 0.40 and under_ratio >= 0.60:
        target_factor = max(_FACTOR_MIN, factor - _FACTOR_STEP)
        target_kf = min(_KF_MAX, kf + _KF_STEP)
      elif override_ratio >= 0.40 and inner_ratio >= 0.60:
        target_factor = min(_FACTOR_MAX, factor + _FACTOR_STEP)
        target_kf = max(_KF_MIN, kf - _KF_STEP)
      elif override_ratio < 0.15:
        # Stable: only ease Kf toward 100; do not disturb factor without evidence.
        target_kf = kf - 1 if kf > 100 else (kf + 1 if kf < 100 else kf)
      changed |= self._put_rec("LateralTorqueAccelFactor", factor, target_factor,
                               f"FF correction under={under_ratio:.0%}, inner={inner_ratio:.0%}")
      changed |= self._put_rec("LateralTorqueKf", kf, target_kf,
                               f"FF gain correction under={under_ratio:.0%}, inner={inner_ratio:.0%}")

      # Feedback gains: zero-crossing/straight ping-pong means gain too high;
      # same-sign persistent error means lag.  Kf gets first priority.
      kpv = self._get_int("LateralTorqueKpV", 100)
      kiv = self._get_int("LateralTorqueKiV", 10)
      target_kpv, target_kiv = kpv, kiv
      oscillating = ((self._torque_osc_count >= _TORQUE_OSC_MIN and
                      self._torque_osc_count > self._torque_steady_count) or
                     self._straight_reversals >= _TORQUE_PINGPONG_MIN_COUNT)
      if oscillating:
        target_kpv = max(_KPV_MIN, kpv - _KPV_STEP)
        target_kiv = max(_KIV_MIN, kiv - _KIV_STEP)
      elif self._torque_steady_count >= _TORQUE_STEADY_MIN and self._torque_osc_count < _TORQUE_OSC_MIN:
        if kf >= 100:
          target_kpv = min(_KPV_MAX, kpv + _KPV_STEP)
          target_kiv = min(_KIV_MAX, kiv + _KIV_STEP)
      changed |= self._put_rec("LateralTorqueKpV", kpv, target_kpv,
                               f"osc={self._torque_osc_count}, steady={self._torque_steady_count}, pingpong={self._straight_reversals}")
      changed |= self._put_rec("LateralTorqueKiV", kiv, target_kiv,
                               f"osc={self._torque_osc_count}, steady={self._torque_steady_count}")

    # Friction is intentionally based on straight/gentle override ratio.
    if self._straight_samples >= (_STRAIGHT_MIN_SAMPLES if not force else 40):
      ratio = self._straight_overrides / max(1, self._straight_samples)
      friction = self._get_int("LateralTorqueFriction", 100)
      target = friction
      if ratio >= 0.35:
        target = min(_FRICTION_MAX, friction + 5)
      elif ratio < 0.08:
        target = max(_FRICTION_MIN, friction - 2)
      changed |= self._put_rec("LateralTorqueFriction", friction, target,
                               f"straight override ratio={ratio:.0%}")

    if changed:
      self._save()

  def _save(self):
    self._params.put("CarrotLearningData", json.dumps({
      "updated": time.time(),
      "phase": 2,
      "curveSamples": self._curve_samples,
      "curveOverrides": self._curve_overrides,
      "understeer": self._understeer,
      "innerHugging": self._inner_hugging,
      "oscillation": self._torque_osc_count,
      "steadyLag": self._torque_steady_count,
      "straightSamples": self._straight_samples,
      "straightOverrides": self._straight_overrides,
      "straightReversals": self._straight_reversals,
      "laneCenterSamples": self._lane_center_n,
    }).encode('utf8'))
    self._params.put("CarrotLearningRecommend", json.dumps(self._recommend).encode('utf8'))
    if not self._params.get_bool("CarrotLearningAutoApply"):
      self._params.put_bool("CarrotLearningPopupReady", True)

  def _on_park(self):
    if self._recommend and self._params.get_bool("CarrotLearningAutoApply"):
      self.apply_recommendations()

  def apply_recommendations(self):
    bounds = {
      "SteerActuatorDelay": (_DELAY_MIN, _DELAY_MAX),
      "CustomSteerRatio": (_SR_MIN, _SR_MAX),
      "LateralTorqueAccelFactor": (_FACTOR_MIN, _FACTOR_MAX),
      "LateralTorqueKf": (_KF_MIN, _KF_MAX),
      "LateralTorqueFriction": (_FRICTION_MIN, _FRICTION_MAX),
      "LateralTorqueKpV": (_KPV_MIN, _KPV_MAX),
      "LateralTorqueKiV": (_KIV_MIN, _KIV_MAX),
    }
    for key, rec in list(self._recommend.items()):
      if key == "OffsetTotal":
        v = round(_clip(float(rec["recommend"]), _OFFSET_MIN_M, _OFFSET_MAX_M), 2)
        self._params.put(key, f"{v:.2f}")
      elif key in bounds:
        lo, hi = bounds[key]
        v = int(_clip(int(rec["recommend"]), lo, hi))
        self._params.put(key, str(v))
    self._recommend = {}
    self._params.remove("CarrotLearningRecommend")
    self._params.put_bool("CarrotLearningPopupReady", False)
