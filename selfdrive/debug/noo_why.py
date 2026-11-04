#!/usr/bin/env python3
"""NOO(내비 차선변경)가 왜 동작 안 하는지 관문별로 찍어보는 진단기.

EON 에서 주행/정차 중 그대로 실행:
    cd /data/openpilot && python selfdrive/debug/noo_why.py

각 줄의 [X] 가 처음 나오는 곳이 그 프레임에서 NOO 를 막은 조건이다.
"""
import time

import cereal.messaging as messaging
from common.params import Params
from selfdrive.controls.lib import lane_count
from selfdrive.controls.lib.navigation_route import NavigationRouteData
from selfdrive.controls.lib.navigation_noo import NavigationLaneChangeController
from selfdrive.controls.lib.desire_helper import DesireHelper


def mark(ok):
  return "O" if ok else "X"


def camera_reason(md):
  """camera_lane_position 결과와, None 이면 그 이유 (HUD 와 같은 판정)."""
  return lane_count.estimate(md)


def main():
  params = Params()
  route = NavigationRouteData()
  ctrl = NavigationLaneChangeController()
  dh = DesireHelper()
  sm = messaging.SubMaster(['modelV2', 'carState'])

  while True:
    sm.update(0)
    if not sm.updated['modelV2']:
      time.sleep(0.05)
      continue

    md = sm['modelV2']
    cs = sm['carState']
    v_ego = cs.vEgo

    noo_on = params.get_bool('NavigationOnOpenpilot')
    lc_on = params.get_bool('LaneChangeEnabled')
    mode = int(params.get('NooMode', encoding='utf8') or '0')
    lc_min = int(params.get('AutoLaneChangeSpeed', encoding='utf8') or '50')

    st = route.update()
    cam, cam_msg = camera_reason(md)

    lines = []
    lines.append("[%s] 파라미터  NOO=%s NooMode=%d(차선변경 0/2) LaneChange=%s 최저속도=%dkm/h" %
                 (mark(noo_on and lc_on and mode in (0, 2)), noo_on, mode, lc_on, lc_min))
    lines.append("[%s] 속도       %.1f km/h" % (mark(v_ego * 3.6 >= lc_min), v_ego * 3.6))
    guidance_fresh = bool(st.get('route_fresh') or st.get('lane_fresh') or st.get('fresh'))
    lines.append("[%s] 안내스트림 route_fresh=%s lane_fresh=%s ahead=%s off_route=%s kind=%s dir=%s dist=%.0f" %
                 (mark(guidance_fresh and not st.get('off_route')),
                  st.get('route_fresh'), st.get('lane_fresh'), st.get('lane_ahead_fresh'),
                  st.get('off_route'), st.get('kind'), st.get('direction'),
                  st.get('distance', -1.0)))

    lane_cur = st.get('lane_current')
    map_count = int(lane_cur.get('count', 0)) if isinstance(lane_cur, dict) else 0
    avail = lane_cur.get('available') if isinstance(lane_cur, dict) else None
    source = lane_cur.get('source', 'MAP') if isinstance(lane_cur, dict) else 'MAP'
    lines.append("[%s] 지도차로   source=%s count=%d available=%s" %
                 (mark(map_count >= 2), source, map_count, avail))
    lines.append("[%s] 카메라차로 %s" % (mark(cam is not None), cam_msg))

    if cam is not None and map_count:
      resolved = NavigationLaneChangeController._resolve_current_lane(cam, map_count)
      lines.append("[%s] 차로수정합 cam %d vs map %d -> %s" %
                   (mark(resolved is not None), cam['count'], map_count, resolved))

    plan = ctrl.lane_plan(st, cam, v_ego) if cam is not None else None
    lines.append("[%s] lane_plan  %s" % (mark(plan is not None), plan))

    if plan is not None:
      dist = plan['distance']
      delta = abs(plan['target'] - plan['current'])
      if plan.get('source') == 'carrot_prepare':
        lo = NavigationLaneChangeController.CARROT_PREPARE_MIN_DISTANCE
        hi = NavigationLaneChangeController.carrot_prepare_distance(st, v_ego)
      else:
        lo = NavigationLaneChangeController.MIN_DISTANCE
        hi = min(NavigationLaneChangeController.MAX_DISTANCE, max(300.0, v_ego * 20.0, 200.0 * delta))
      lines.append("[%s] 거리창      %.0fm (허용 %.0f~%.0f, source=%s)" %
                   (mark(lo <= dist <= hi), dist, lo, hi, plan.get('source')))

    lb = dh._road_edge_detected(md, -1)
    rb = dh._road_edge_detected(md, 1)
    lines.append("[%s] 좌측여유   roadEdge막힘=%s BSD=%s" % (mark(not lb and not cs.leftBlindspot), lb, cs.leftBlindspot))
    lines.append("[%s] 우측여유   roadEdge막힘=%s BSD=%s" % (mark(not rb and not cs.rightBlindspot), rb, cs.rightBlindspot))

    print("\n" + time.strftime("%H:%M:%S"))
    print("\n".join(lines))
    time.sleep(0.5)


if __name__ == "__main__":
  main()
