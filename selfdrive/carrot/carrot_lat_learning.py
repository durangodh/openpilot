#!/usr/bin/env python3
"""
CarrotLatLearner - steering-only auto-tune recommender.

selfdrive/common/params.cc already had 5 Params keys registered under a
comment "CarrotPilot Auto-Tuner (commit 9dd5e2c port)"
(CarrotLearningActive, CarrotLearningAutoApply, CarrotTunerApplyLat,
CarrotLearningData, CarrotLearningRecommend) but no python module in this
fork ever used them -- this file is that missing implementation.

It is a *scoped* reimplementation of the "Phase 2" (SteerActuatorDelay /
SteerRatioRate) part of ajouatom/openpilot's selfdrive/carrot/carrot_learning.py
(2524 lines in its current hoya/c3-atune form), not a line-for-line port:
that file interleaves four unrelated phases (cruise accel bands, steering,
braking, follow distance) through shared state, so extracting only the
steering piece cleanly means rewriting it against what THIS fork actually
has, rather than surgically cutting lines out of a much larger, differently
architected file.

Deliberately NOT included:
  - PathOffset recommendations. This fork has no PathOffset Params key and
    nothing reads one -- lane_planner.py/vehicle_model.py have no lane-center
    offset input. Wiring that in means changing how the desired path is
    computed, which is a bigger change than "steering learning" and wasn't
    attempted here.
  - Phase 1 (cruise accel bands), Phase 3 (brake response), Phase 4 (follow
    distance): longitudinal, out of scope for a steering backport.

What it does:
  - While engaged, moving, and entering a curve (|desired_curvature| above a
    threshold), track whether the driver overrides (steeringPressed).
  - A high override ratio over a curve-entry sample window suggests the car
    turns in too late -> recommend increasing SteerActuatorDelay.
  - A *very* high override ratio additionally suggests the steering ratio
    itself is off -> recommend nudging CustomSteerRatio.
  - Recommendations accumulate into Params("CarrotLearningData") /
    Params("CarrotLearningRecommend") as JSON.
  - On gearShifter -> park: if CarrotLearningAutoApply is on, the
    recommendation is applied immediately to the live SteerActuatorDelay /
    CustomSteerRatio Params (already read every ~1s by live_tune.py and
    latcontrol_torque.py's read_torque_params()); otherwise
    CarrotLearningPopupReady is set so a UI can prompt the driver to confirm.
  - CarrotTunerApplyLat gates whether lateral recommendations are generated
    at all; CarrotLearningActive is the master on/off switch.

Applying only happens at a stop (park), never while driving -- steering feel
should not change out from under the driver mid-curve.
"""
import json
import time

from cereal import car
from common.params import Params

GearShifter = car.CarState.GearShifter

_CURVE_CURVATURE_THRESHOLD = 0.0025  # 1/m, roughly R400m -- "entering a curve"
_MIN_ENGAGE_SPEED = 5.0  # m/s, below this steering feel is unreliable

_CURVE_MIN_SAMPLES = 100      # curve-entry samples (@10Hz sampling, ~10s) before a verdict
_CURVE_OVERRIDE_RATIO = 0.5   # >=50% override during curve entry -> SteerActuatorDelay recommend
_SR_OVERRIDE_RATIO = 0.7      # >=70% override -> also nudge CustomSteerRatio

_DELAY_STEP = 5                  # Genesis DH: +0.05s per recommendation (conservative)
_DELAY_MIN, _DELAY_MAX = 15, 40  # Genesis DH: clamp learning to 0.15s .. 0.40s
_DELAY_DEFAULT = 10

_SR_STEP = 30                  # CustomSteerRatio units are x100 -> +0.30 per recommendation
_SR_MIN, _SR_MAX = 1000, 2000  # 10.00 .. 20.00 sane bounds
_SR_DEFAULT = 1650

_SAMPLE_EVERY_N_TICKS = 10   # tick() is called at 100Hz from controlsd; sample at 10Hz
_PARAMS_REFRESH_TICKS = 100  # re-read the on/off switches from disk at ~1Hz, not 100Hz


class CarrotLatLearner:
  def __init__(self):
    self._params = Params()
    self._frame = 0
    self._was_park = True

    self._curve_samples = 0
    self._curve_overrides = 0
    self._recommend = {}  # key -> {"current": int, "recommend": int, "reason": str}

    self._active_cached = False
    self._apply_lat_cached = True

  def _refresh_switches(self):
    self._active_cached = self._params.get_bool("CarrotLearningActive")
    v = self._params.get("CarrotTunerApplyLat")
    self._apply_lat_cached = True if v is None else self._params.get_bool("CarrotTunerApplyLat")

  def tick(self, CS, latActive, desired_curvature):
    """Call once per controlsd cycle (100Hz) with the current CarState,
    CC.latActive, and the just-computed desired_curvature."""
    self._frame += 1

    if self._frame % _PARAMS_REFRESH_TICKS == 0 or self._frame == 1:
      self._refresh_switches()

    is_park = CS.gearShifter == GearShifter.park
    if is_park and not self._was_park:
      self._on_park()
    self._was_park = is_park

    if not self._active_cached or not self._apply_lat_cached:
      return
    if self._frame % _SAMPLE_EVERY_N_TICKS != 0:
      return
    if not latActive or CS.vEgo < _MIN_ENGAGE_SPEED:
      return

    if abs(desired_curvature) >= _CURVE_CURVATURE_THRESHOLD:
      self._curve_samples += 1
      if CS.steeringPressed:
        self._curve_overrides += 1

      if self._curve_samples >= _CURVE_MIN_SAMPLES:
        self._evaluate_curve_window()
        self._curve_samples = 0
        self._curve_overrides = 0

  def _evaluate_curve_window(self):
    ratio = self._curve_overrides / max(1, self._curve_samples)
    changed = False

    if ratio >= _CURVE_OVERRIDE_RATIO:
      current = self._params.get_int("SteerActuatorDelay", _DELAY_DEFAULT)
      target = min(_DELAY_MAX, current + _DELAY_STEP) if current < _DELAY_MAX else current
      if target != current:
        self._recommend["SteerActuatorDelay"] = {
          "current": current, "recommend": target,
          "reason": f"curve override ratio {ratio:.0%} >= {_CURVE_OVERRIDE_RATIO:.0%}",
        }
        changed = True

    if ratio >= _SR_OVERRIDE_RATIO:
      current = self._params.get_int("CustomSteerRatio", _SR_DEFAULT)
      target = min(_SR_MAX, current + _SR_STEP)
      if target != current:
        self._recommend["CustomSteerRatio"] = {
          "current": current, "recommend": target,
          "reason": f"curve override ratio {ratio:.0%} >= {_SR_OVERRIDE_RATIO:.0%}",
        }
        changed = True

    if changed:
      self._save()

  def _save(self):
    self._params.put("CarrotLearningData", json.dumps({
      "updated": time.time(),
      "curveOverrideSamples": self._curve_samples,
    }).encode('utf8'))
    self._params.put("CarrotLearningRecommend", json.dumps(self._recommend).encode('utf8'))

    if self._params.get_bool("CarrotLearningAutoApply"):
      # Still deferred to the next park stop (see _on_park) -- never change
      # steering feel while the driver is actively steering.
      return
    self._params.put_bool("CarrotLearningPopupReady", True)

  def _on_park(self):
    if self._recommend and self._params.get_bool("CarrotLearningAutoApply"):
      self.apply_recommendations()

  def apply_recommendations(self):
    """Write the current recommendation into the live tuning Params. Only
    call this when the car is stopped (park)."""
    for key, rec in list(self._recommend.items()):
      if key == "SteerActuatorDelay":
        v = max(_DELAY_MIN, min(_DELAY_MAX, int(rec["recommend"])))
        self._params.put("SteerActuatorDelay", str(v))
      elif key == "CustomSteerRatio":
        v = max(_SR_MIN, min(_SR_MAX, int(rec["recommend"])))
        self._params.put("CustomSteerRatio", str(v))
    self._recommend = {}
    self._params.remove("CarrotLearningRecommend")
    self._params.put_bool("CarrotLearningPopupReady", False)
