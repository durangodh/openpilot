#!/usr/bin/env python3
"""Closed-loop lateral auto-tune (selfdrive/controls/lib/lateral_autotune.py).

Learns while LateralAutoTune is on and writes LateralAutoTuneValues (JSON), which the torque
controller uses in place of the LiveTorque values. Status for the HUD / SSH:
  cat /data/params/d/LateralAutoTuneValues
"""
import json
import time

import cereal.messaging as messaging
from cereal import car, log
from common.params import Params
from selfdrive.controls.lib import live_tune
from selfdrive.controls.lib.lateral_autotune import LateralAutoTune

LaneChangeState = log.LateralPlan.LaneChangeState
SAVE_S = 60.0


def load(params, base_factor, base_friction):
  try:
    saved = json.loads(params.get("LateralAutoTuneValues", encoding="utf8") or "{}")
  except (TypeError, ValueError):
    saved = {}
  # Learned values belong to one car tune; a different base starts over.
  if saved.get("base_factor") != round(base_factor, 4) or saved.get("base_friction") != round(base_friction, 4):
    return LateralAutoTune(base_factor, base_friction)
  return LateralAutoTune(base_factor, base_friction, saved.get("factor"), saved.get("friction"))


def save(params, tuner):
  params.put("LateralAutoTuneValues", json.dumps(dict(tuner.status(), wall=int(time.time()))))


def main():
  params = Params()
  CP = car.CarParams.from_bytes(params.get("CarParams", block=True))
  if CP.lateralTuning.which() != 'torque':
    while True:
      time.sleep(60)
  torque = CP.lateralTuning.torque
  tuner = load(params, torque.latAccelFactor, torque.friction)
  sm = messaging.SubMaster(['controlsState', 'carState', 'lateralPlan'], poll=['controlsState'])
  enabled = False
  next_param = next_save = 0.0
  dirty = False
  steer_delay = live_tune.steer_actuator_delay()
  while True:
    now = time.monotonic()
    if now >= next_param:
      enabled = params.get_bool("LateralAutoTune")
      steer_delay = live_tune.steer_actuator_delay()
      next_param = now + 2.0
    if not enabled:
      time.sleep(2.0)  # off: no 100 Hz work on the EON
      continue
    sm.update()
    cs = sm['controlsState']
    if cs.lateralControlState.which() != 'torqueState':
      continue
    ts = cs.lateralControlState.torqueState
    car_state = sm['carState']
    lane_change = sm['lateralPlan'].laneChangeState != LaneChangeState.off
    if tuner.update(ts.active and cs.active, car_state.vEgo, car_state.steeringPressed, lane_change,
                    ts.saturated, ts.desiredLateralAccel, ts.actualLateralAccel,
                    ts.f, ts.p, ts.i, ts.d, steer_delay):
      dirty = True
    if now >= next_save:
      next_save = now + SAVE_S
      save(params, tuner)
      dirty = False
    elif dirty and tuner.updates == 1:
      save(params, tuner)  # the first learned value is used without waiting a minute
      dirty = False


if __name__ == "__main__":
  main()
