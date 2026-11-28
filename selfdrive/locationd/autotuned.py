#!/usr/bin/env python3
"""Closed-loop lateral auto-tune (selfdrive/controls/lib/lateral_autotune.py).

Learns while LateralAutoTune or LateralAutoTuneDelay is on and writes LateralAutoTuneValues
(JSON): factor / friction replace the LiveTorque values with LateralAutoTune, sad replaces
SteerActuatorDelay with LateralAutoTuneDelay (live_tune.steer_actuator_delay). Status for the HUD / SSH:
  cat /data/params/d/LateralAutoTuneValues
It also records the driver's own longitudinal habits while LongLearn is on (long_learn.py,
LongLearnData); selfdrive/debug/long_learn.py shows the suggested settings.
"""
import json
import time

import cereal.messaging as messaging
from cereal import car, log
from common.params import Params
from selfdrive.controls.lib import live_tune
from selfdrive.controls.lib.lateral_autotune import DELAY_MIN_WINDOWS, SIDE_MIN, LateralAutoTune
from selfdrive.controls.lib.long_learn import (LONG_DELAY_MIN_WINDOWS, MIN_APPROACHES, MIN_BENDS, MIN_GAP_SAMPLES,
                                               MIN_STOPS, LongDelay, LongLearn, apply_step)

LaneChangeState = log.LateralPlan.LaneChangeState
LongCtrlState = car.CarControl.Actuators.LongControlState
GearShifter = car.CarState.GearShifter
SAVE_S = 60.0


def load(params, base_factor, base_friction, sad, kf=1.0):
  try:
    saved = json.loads(params.get("LateralAutoTuneValues", encoding="utf8") or "{}")
  except (TypeError, ValueError):
    saved = {}
  # Learned values belong to one car tune; a different base starts over.
  if saved.get("base_factor") != round(base_factor, 4) or saved.get("base_friction") != round(base_friction, 4):
    return LateralAutoTune(base_factor, base_friction, sad=saved.get("sad") or sad,
                           delay_estimate=saved.get("delay"), delay_windows=saved.get("delay_windows"), kf=kf)
  return LateralAutoTune(base_factor, base_friction, saved.get("factor"), saved.get("friction"),
                         saved.get("sad") or sad, bool(saved.get("factor_learned")),
                         delay_estimate=saved.get("delay"), delay_windows=saved.get("delay_windows"), kf=kf)


def save(params, tuner):
  params.put("LateralAutoTuneValues", json.dumps(dict(tuner.status(), wall=int(time.time()))))


def load_delay(params):
  try:
    applied = json.loads(params.get("LongLearnApplied", encoding="utf8") or "{}")
  except (TypeError, ValueError):
    applied = {}
  delay = LongDelay(applied.get("long_delay"))
  delay.estimate = applied.get("long_delay_est")
  delay.windows = int(applied.get("long_delay_windows") or 0)
  return delay


def load_long(params):
  try:
    return LongLearn(json.loads(params.get("LongLearnData", encoding="utf8") or "{}"))
  except (TypeError, ValueError, AttributeError):
    return LongLearn()


def _int(params, key, default):
  try:
    v = int(params.get(key, encoding="utf8") or 0)
  except (TypeError, ValueError):
    v = 0
  return v if v > 0 else default


def long_delay_now(params):
  """The long actuator delay longcontrol uses without LongAutoDelay (setting, 0 = car 0.5 s)."""
  return _int(params, "LongitudinalActuatorDelayLowerBound", 50) / 100.0


def long_apply(params, learner, delay):
  """LongLearnApply / LongAutoDelay: LongLearnApplied (read by planner, cruise helper and
  longcontrol) moves toward the driver's habits, and carries the measured long delay."""
  gap = _int(params, "PrevCruiseGap", 4)
  gap = gap if 1 <= gap <= 4 else 4
  stop_now = _int(params, "StopDistance", 600) / 100.0
  tf_now = _int(params, f"TFollowGap{gap}", [110, 120, 140, 160][gap - 1]) / 100.0
  ratio = _int(params, "TFollowSpeedRatio", 120) / 100.0
  try:
    applied = json.loads(params.get("LongLearnApplied", encoding="utf8") or "{}")
  except (TypeError, ValueError):
    applied = {}
  if params.get_bool("LongLearnApply"):
    new = apply_step(applied, learner.recommend(stop_now, gap, tf_now, ratio), gap, tf_now, stop_now,
                     _int(params, "AutoCurveSpeedFactor", 120) / 100.0, _int(params, "ComfortBrake", 250) / 100.0)
  else:
    new = {"gap": gap}
  new["long_delay_est"] = None if delay.estimate is None else round(delay.estimate, 3)
  new["long_delay_windows"] = delay.windows
  if delay.applied is not None:
    new["long_delay"] = delay.applied
  params.put("LongLearnApplied", json.dumps(new))


def status_text(params, tuner, learner):
  """Two short lines for the EON screen (onroad.cc drawLearnStatus): LAT ... / LONG ..."""
  lines = []
  lat_tune = params.get_bool("LateralAutoTune")
  lat_delay = params.get_bool("LateralAutoTuneDelay")
  center = params.get_bool("LateralAutoCenter")
  if tuner is not None and (lat_tune or lat_delay or center):
    st = tuner.status()
    parts = ["LAT " + ("auto" if lat_tune else "learn")]
    if lat_tune or lat_delay:
      # →: where the factor is heading from the current curve samples
      target = st.get("factor_target")
      head = "" if target is None else f" ->{target:.2f}"
      parts.append(f"F {st['factor']:.2f}/{st['base_factor']:.2f}{head}  FR {st['friction']:.3f}")
      sad = st.get("sad")
      # progress only while something is still missing; once enough, the counts mean nothing more
      windows = st['delay_windows']
      wait = f" ({windows}/{DELAY_MIN_WINDOWS})" if windows < DELAY_MIN_WINDOWS else ""
      parts.append(f"SAD {'-' if sad is None else f'{sad:.2f}'}{'*' if lat_delay else ''}{wait}")
      if not st['factor_learned']:
        parts.append(f"CURVE {st['samples_left']}/{st['samples_right']} ({SIDE_MIN})")
      else:
        # learned: what drives the next step instead of counts. LAG > 0: small moves late
        # (friction goes up), < 0: early / overshoot (friction goes down); UPD: adjustments made.
        lag = st.get("last_lag")
        parts.append(f"LAG {'-' if lag is None else f'{lag:+.2f}s'}")
        parts.append(f"UPD {st['updates']}")
    if center:
      # nothing learned yet: the planner still uses the manual PathOffset (cm)
      raw = params.get("LateralAutoCenterValue", encoding="utf8")
      try:
        offset = float(raw) * 100 if raw else float(params.get("PathOffset", encoding="utf8") or 0)
        parts.append(f"OFS {offset:+.0f}cm{'' if raw else ' (set)'}")
      except (TypeError, ValueError):
        parts.append("OFS -")
    lines.append("  ".join(parts))
  long_on = params.get_bool("LongLearn")
  long_apply_on = params.get_bool("LongLearnApply")
  delay_on = params.get_bool("LongAutoDelay")
  try:
    applied = json.loads(params.get("LongLearnApplied", encoding="utf8") or "{}")
  except (TypeError, ValueError):
    applied = {}
  if long_on or long_apply_on or delay_on:
    gap = _int(params, "PrevCruiseGap", 4)
    gap = gap if 1 <= gap <= 4 else 4
    stop_now = _int(params, "StopDistance", 600) / 100.0
    tf_now = _int(params, f"TFollowGap{gap}", [110, 120, 140, 160][gap - 1]) / 100.0
    rec = learner.recommend(stop_now, gap, tf_now, _int(params, "TFollowSpeedRatio", 120) / 100.0)
    c = rec["counts"]
    if long_apply_on:
      tf = applied.get("t_follow") if applied.get("gap") == gap else None
      stop = applied.get("stop_distance")
      head = "LONG apply"
    else:
      tf = rec.get("t_follow")
      stop = rec.get("stop_distance_m")
      head = "LONG learn"
    if long_apply_on:   # not learned enough yet: the setting is what the car uses
      tf_s = f"{tf_now:.2f}s(set)" if tf is None else f"{tf:.2f}s"
      stop_s = f"{stop_now:.1f}m(set)" if stop is None else f"{stop:.1f}m"
    else:
      tf_s = "-" if tf is None else f"{tf:.2f}s"
      stop_s = "-" if stop is None else f"{stop:.1f}m"
    line = f"{head}  GAP{gap} {tf_s}  STOP {stop_s}"
    if c['gap'] < MIN_GAP_SAMPLES or c['stops'] < MIN_STOPS:   # progress until both are learned
      line += f"  REC {min(c['gap'], MIN_GAP_SAMPLES)}/{MIN_GAP_SAMPLES}s {min(c['stops'], MIN_STOPS)}/{MIN_STOPS}"
    else:
      # learned: the driver's own habit the applied values move toward, and how they brake / start
      line += f"  TARGET {rec['t_follow']:.2f}s/{rec['stop_distance_m']:.1f}m"
      if rec.get("stop_decel_last_1s") is not None:
        line += f"  BRAKE {rec['stop_decel_last_1s']:.1f}m/s2"
      if rec.get("start_delay_s") is not None:
        line += f"  GO {rec['start_delay_s']:.1f}s"
    # curve speed: lateral accel the vision cap corners at (applied) or the driver's (learning)
    if rec.get("bend_lat_accel") is None:
      line += f"  BEND {c['bends']}/{MIN_BENDS}"
    elif long_apply_on and applied.get("curve_factor"):
      line += f"  BEND {1.9 / applied['curve_factor']:.1f}m/s2"
    else:
      line += f"  BEND {rec['bend_lat_accel']:.1f}m/s2"
    # approach braking: the planner's comfort brake (applied) or the driver's peak (learning)
    if rec.get("approach_decel") is None:
      line += f"  APPR {c['approaches']}/{MIN_APPROACHES}"
    elif long_apply_on and applied.get("comfort_brake"):
      line += f"  APPR {applied['comfort_brake']:.1f}m/s2"
    else:
      line += f"  APPR {rec['approach_decel']:.1f}m/s2"
    if delay_on:
      windows = int(applied.get("long_delay_windows") or 0)
      if applied.get("long_delay") is not None:
        line += f"  LDLY {applied['long_delay']:.2f}s*"
      else:
        line += f"  LDLY {windows}/{LONG_DELAY_MIN_WINDOWS}"
    lines.append(line)
  return "\n".join(lines)


def main():
  params = Params()
  CP = car.CarParams.from_bytes(params.get("CarParams", block=True))
  torque_car = CP.lateralTuning.which() == 'torque'
  tuner = None
  if torque_car:
    torque = CP.lateralTuning.torque
    tuner = load(params, torque.latAccelFactor, torque.friction, live_tune.manual_steer_actuator_delay(), torque.kf)
  learner = load_long(params)
  delay = load_delay(params)
  # One process for both learners: each extra carState subscriber takes one of its 15 msgq slots.
  sm = messaging.SubMaster(['controlsState', 'carState', 'lateralPlan', 'radarState', 'carControl'],
                           poll=['controlsState'])
  lat_on = long_on = long_apply_on = delay_on = False
  long_delay = 0.5
  next_text = 0.0
  next_param = next_save = 0.0
  dirty = False
  steer_delay = live_tune.steer_actuator_delay()
  while True:
    now = time.monotonic()
    if now >= next_param:
      # learns while either the factor/friction or the delay auto-tune is on
      lat_on = torque_car and (params.get_bool("LateralAutoTune") or params.get_bool("LateralAutoTuneDelay"))
      long_on = params.get_bool("LongLearn")
      long_apply_on = params.get_bool("LongLearnApply")
      delay_on = params.get_bool("LongAutoDelay")
      long_delay = long_delay_now(params)
      steer_delay = live_tune.steer_actuator_delay()  # the delay the controller uses now
      next_param = now + 2.0
    if now >= next_text:
      next_text = now + 5.0
      params.put("LearnStatusText", status_text(params, tuner, learner))
    if not lat_on and not long_on and not delay_on:
      if long_apply_on and now >= next_save:   # applying from the records needs no live data
        next_save = now + SAVE_S
        long_apply(params, learner, delay)
      time.sleep(2.0)  # off: no 100 Hz work on the EON
      continue
    sm.update()
    cs = sm['controlsState']
    car_state = sm['carState']
    if long_on:
      lead = sm['radarState'].leadOne
      # the driver alone controls speed: no SCC / openpilot cruise and no long control output
      human = (not car_state.cruiseState.enabled and cs.longControlState == LongCtrlState.off and
               car_state.gearShifter == GearShifter.drive)
      learner.update(human, car_state.vEgo, car_state.aEgo, car_state.gasPressed,
                     bool(lead.status and lead.radar), lead.dRel, lead.vLead,
                     cs.curvature * car_state.vEgo ** 2)
    if delay_on:
      # openpilot drives speed (PID, not stopping / starting) and the driver keeps off the pedals
      op_long = (cs.longControlState == LongCtrlState.pid and car_state.vEgo > 3.0 and
                 not car_state.gasPressed and not car_state.brakePressed)
      delay.update(op_long, sm['carControl'].actuators.accel, car_state.aEgo, long_delay)
    if lat_on and cs.lateralControlState.which() == 'torqueState':
      ts = cs.lateralControlState.torqueState
      lane_change = sm['lateralPlan'].laneChangeState != LaneChangeState.off
      if tuner.update(ts.active and cs.active, car_state.vEgo, car_state.steeringPressed, lane_change,
                      ts.saturated, ts.desiredLateralAccel, ts.actualLateralAccel,
                      ts.f, ts.p, ts.i, ts.d, steer_delay):
        dirty = True
    if now >= next_save:
      next_save = now + SAVE_S
      if lat_on:
        save(params, tuner)
      if long_on:
        params.put("LongLearnData", json.dumps(learner.to_json(), separators=(",", ":")))
      if long_apply_on or delay_on:
        long_apply(params, learner, delay)
      dirty = False
    elif dirty and tuner.updates == 1:
      save(params, tuner)  # the first learned value is used without waiting a minute
      dirty = False


if __name__ == "__main__":
  main()
