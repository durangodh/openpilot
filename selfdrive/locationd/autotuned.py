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
from selfdrive.controls.lib.lateral_autotune import LateralAutoTune
from selfdrive.controls.lib.long_learn import LongLearn, apply_step

LaneChangeState = log.LateralPlan.LaneChangeState
LongCtrlState = car.CarControl.Actuators.LongControlState
GearShifter = car.CarState.GearShifter
SAVE_S = 60.0


def load(params, base_factor, base_friction, sad):
  try:
    saved = json.loads(params.get("LateralAutoTuneValues", encoding="utf8") or "{}")
  except (TypeError, ValueError):
    saved = {}
  # Learned values belong to one car tune; a different base starts over.
  if saved.get("base_factor") != round(base_factor, 4) or saved.get("base_friction") != round(base_friction, 4):
    return LateralAutoTune(base_factor, base_friction, sad=saved.get("sad") or sad)
  return LateralAutoTune(base_factor, base_friction, saved.get("factor"), saved.get("friction"),
                         saved.get("sad") or sad)


def save(params, tuner):
  params.put("LateralAutoTuneValues", json.dumps(dict(tuner.status(), wall=int(time.time()))))


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


def long_apply(params, learner):
  """LongLearnApply: move LongLearnApplied (read by the planner) toward the driver's habits."""
  gap = _int(params, "PrevCruiseGap", 4)
  gap = gap if 1 <= gap <= 4 else 4
  stop_now = _int(params, "StopDistance", 600) / 100.0
  tf_now = _int(params, f"TFollowGap{gap}", [110, 120, 140, 160][gap - 1]) / 100.0
  ratio = _int(params, "TFollowSpeedRatio", 120) / 100.0
  try:
    applied = json.loads(params.get("LongLearnApplied", encoding="utf8") or "{}")
  except (TypeError, ValueError):
    applied = {}
  new = apply_step(applied, learner.recommend(stop_now, gap, tf_now, ratio), gap, tf_now, stop_now)
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
      parts.append(f"F {st['factor']:.2f}/{st['base_factor']:.2f}  FR {st['friction']:.3f}")
      sad = st.get("sad")
      parts.append(f"SAD {'-' if sad is None else f'{sad:.2f}'}{'*' if lat_delay else ''} ({st['delay_windows']})")
      parts.append(f"L{st['samples_left']} R{st['samples_right']}")
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
  if long_on or long_apply_on:
    gap = _int(params, "PrevCruiseGap", 4)
    gap = gap if 1 <= gap <= 4 else 4
    stop_now = _int(params, "StopDistance", 600) / 100.0
    tf_now = _int(params, f"TFollowGap{gap}", [110, 120, 140, 160][gap - 1]) / 100.0
    rec = learner.recommend(stop_now, gap, tf_now, _int(params, "TFollowSpeedRatio", 120) / 100.0)
    c = rec["counts"]
    if long_apply_on:
      try:
        applied = json.loads(params.get("LongLearnApplied", encoding="utf8") or "{}")
      except (TypeError, ValueError):
        applied = {}
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
    lines.append(f"{head}  GAP{gap} {tf_s}  STOP {stop_s}  ({min(c['gap'], 999)}s {c['stops']}stop {c['starts']}go)")
  return "\n".join(lines)


def main():
  params = Params()
  CP = car.CarParams.from_bytes(params.get("CarParams", block=True))
  torque_car = CP.lateralTuning.which() == 'torque'
  tuner = None
  if torque_car:
    torque = CP.lateralTuning.torque
    tuner = load(params, torque.latAccelFactor, torque.friction, live_tune.manual_steer_actuator_delay())
  learner = load_long(params)
  # One process for both learners: each extra carState subscriber takes one of its 15 msgq slots.
  sm = messaging.SubMaster(['controlsState', 'carState', 'lateralPlan', 'radarState'], poll=['controlsState'])
  lat_on = long_on = long_apply_on = False
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
      steer_delay = live_tune.steer_actuator_delay()  # the delay the controller uses now
      next_param = now + 2.0
    if now >= next_text:
      next_text = now + 5.0
      params.put("LearnStatusText", status_text(params, tuner, learner))
    if not lat_on and not long_on:
      if long_apply_on and now >= next_save:   # applying from the records needs no live data
        next_save = now + SAVE_S
        long_apply(params, learner)
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
                     bool(lead.status and lead.radar), lead.dRel, lead.vLead)
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
      if long_apply_on:
        long_apply(params, learner)
      dirty = False
    elif dirty and tuner.updates == 1:
      save(params, tuner)  # the first learned value is used without waiting a minute
      dirty = False


if __name__ == "__main__":
  main()
