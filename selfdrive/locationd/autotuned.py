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
from selfdrive.controls.lib.long_learn import LongLearn

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
  lat_on = long_on = False
  next_param = next_save = 0.0
  dirty = False
  steer_delay = live_tune.steer_actuator_delay()
  while True:
    now = time.monotonic()
    if now >= next_param:
      # learns while either the factor/friction or the delay auto-tune is on
      lat_on = torque_car and (params.get_bool("LateralAutoTune") or params.get_bool("LateralAutoTuneDelay"))
      long_on = params.get_bool("LongLearn")
      steer_delay = live_tune.steer_actuator_delay()  # the delay the controller uses now
      next_param = now + 2.0
    if not lat_on and not long_on:
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
      dirty = False
    elif dirty and tuner.updates == 1:
      save(params, tuner)  # the first learned value is used without waiting a minute
      dirty = False


if __name__ == "__main__":
  main()
