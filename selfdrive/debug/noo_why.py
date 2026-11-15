#!/usr/bin/env python3
"""EON의 실제 NOO 판정과 최신 내비 안내를 SSH에서 확인한다.

    cd /data/openpilot && python selfdrive/debug/noo_why.py
"""
import time

import cereal.messaging as messaging
from common.conversions import Conversions as CV
from common.params import Params
from selfdrive.controls.lib.desire_helper import (
  NOO_LANE_CHANGE_SPEED_DEFAULT_KPH,
  NOO_LANE_CHANGE_SPEED_RANGE_KPH,
)
from selfdrive.controls.lib.navigation_route import NavigationRouteData


def noo_lane_change_speed(params):
  try:
    speed = int(params.get("NooLaneChangeSpeed", encoding="utf8") or NOO_LANE_CHANGE_SPEED_DEFAULT_KPH)
  except (TypeError, ValueError):
    speed = NOO_LANE_CHANGE_SPEED_DEFAULT_KPH
  low, high = NOO_LANE_CHANGE_SPEED_RANGE_KPH
  return max(low, min(speed, high))


def main():
  params = Params()
  route = NavigationRouteData()
  sm = messaging.SubMaster(['lateralPlan', 'carState'], poll=['lateralPlan'])
  previous_status = None
  next_report = 0.0

  while True:
    sm.update()
    if not (sm.alive['lateralPlan'] and sm.valid['lateralPlan']):
      if previous_status != "waiting":
        print("유효한 lateralPlan 대기 중", flush=True)
        previous_status = "waiting"
      continue
    if not sm.updated['lateralPlan']:
      continue

    plan = sm['lateralPlan']
    reason = plan.nooLaneChangeReason or "-"
    desire = str(plan.desire)
    status = (reason, plan.nooTurnDirection, plan.nooLaneChangeDirection, desire,
              plan.nooCurrentLane, plan.nooTargetLane)
    now = time.monotonic()
    if status == previous_status and now < next_report:
      continue
    previous_status = status
    next_report = now + 0.5

    guidance = route.update()
    kind = guidance.get('kind') or "-"
    direction = guidance.get('direction', 0)
    distance = guidance.get('distance', -1.0)
    minimum_speed = noo_lane_change_speed(params)
    speed = "-"
    if sm.alive['carState'] and sm.valid['carState']:
      speed = f"{sm['carState'].vEgo * CV.MS_TO_KPH:.1f}"

    print(f"{time.strftime('%H:%M:%S')} plan={sm.logMonoTime['lateralPlan'] / 1e9:.3f}s "
          f"내비 {kind} dir={direction:+d} dist={distance:.0f}m "
          f"안내유효={bool(guidance.get('guidance_fresh'))} 차로안내유효={bool(guidance.get('lane_fresh'))} "
          f"경로이탈={bool(guidance.get('off_route'))} | "
          f"속도={speed}km/h NOO차선변경기준={minimum_speed}km/h | "
          f"회전요청={plan.nooTurnDirection:+d} 차선변경요청={plan.nooLaneChangeDirection:+d} "
          f"desire={desire} 차로={plan.nooCurrentLane}>{plan.nooTargetLane} "
          f"카메라/지도={plan.nooCameraLaneCount}/{plan.nooRouteLaneCount} 판정={reason}",
          flush=True)


if __name__ == "__main__":
  main()
