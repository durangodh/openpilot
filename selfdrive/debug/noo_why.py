#!/usr/bin/env python3
"""NOO(내비 차선변경)가 왜 동작 안 하는지 관문별로 찍어보는 진단기.

EON 에서 주행/정차 중 그대로 실행:
    cd /data/openpilot && python selfdrive/debug/noo_why.py

각 줄의 [X] 가 처음 나오는 곳이 그 프레임에서 NOO 를 막은 조건이다.
"""
import math
import time

import cereal.messaging as messaging
from common.params import Params
from selfdrive.controls.lib.navigation_route import NavigationRouteData
from selfdrive.controls.lib.navigation_noo import NavigationLaneChangeController
from selfdrive.controls.lib.desire_helper import DesireHelper


def mark(ok):
  return "O" if ok else "X"


def camera_reason(md):
  """camera_lane_position 이 None 을 주는 이유를 그대로 되짚는다."""
  if md is None:
    return None, "modelV2 없음"

  def near_y(line):
    try:
      values = [float(y) for x, y in zip(line.x, line.y)
                if math.isfinite(float(x)) and 0.0 <= float(x) <= 30.0 and math.isfinite(float(y))]
    except (AttributeError, TypeError, ValueError):
      return None
    return sorted(values)[len(values) // 2] if len(values) >= 2 else None

  try:
    lanes, edges = md.laneLines, md.roadEdges
    if len(lanes) < 3 or len(edges) < 2:
      return None, "laneLines/roadEdges 개수 부족"
    inner = [near_y(lanes[1]), near_y(lanes[2])]
    road = [near_y(edges[0]), near_y(edges[1])]
    conf = min(float(md.laneLineProbs[1]), float(md.laneLineProbs[2]))
    edge_std = max(float(md.roadEdgeStds[0]), float(md.roadEdgeStds[1]))
  except (AttributeError, IndexError, TypeError, ValueError) as e:
    return None, "모델 필드 오류 %s" % e

  if any(v is None for v in inner + road):
    return None, "차선/도로경계 y 표본 부족"
  if conf < NavigationRouteData.LANE_PROB_MIN:
    return None, "차선확률 %.2f < %.2f" % (conf, NavigationRouteData.LANE_PROB_MIN)
  if not math.isfinite(edge_std) or edge_std > NavigationRouteData.ROAD_EDGE_STD_MAX:
    return None, "roadEdgeStd %.2f > %.2f (경계 불확실)" % (
      edge_std, NavigationRouteData.ROAD_EDGE_STD_MAX)

  lane_left, lane_right = max(inner), min(inner)
  road_left, road_right = max(road), min(road)
  width = lane_left - lane_right
  if not 2.5 <= width <= 4.5:
    return None, "차로폭 %.2fm 범위밖" % width
  if road_left < lane_left:
    if lane_left - road_left > NavigationRouteData.EDGE_INSIDE_TOL:
      return None, "좌 도로경계가 자차차선 안쪽 (%.1f/%.1f)" % (road_left, lane_left)
    road_left = lane_left
  if road_right > lane_right:
    if road_right - lane_right > NavigationRouteData.EDGE_INSIDE_TOL:
      return None, "우 도로경계가 자차차선 안쪽 (%.1f/%.1f)" % (road_right, lane_right)
    road_right = lane_right

  lr = max(0.0, road_left - lane_left) / width
  rr = max(0.0, lane_right - road_right) / width
  total = 1 + int(round(lr)) + int(round(rr))
  cur = 1 + int(round(lr))
  def adjacent(index, outer_y, inner_y):
    try:
      probability = float(md.laneLineProbs[index])
    except (AttributeError, IndexError, TypeError, ValueError):
      return False
    return (outer_y is not None and probability >= NavigationRouteData.ADJACENT_LANE_PROB_MIN and
            width * 0.65 <= abs(outer_y - inner_y) <= width * 1.35)

  outer_left = near_y(lanes[0]) if len(lanes) > 0 else None
  outer_right = near_y(lanes[3]) if len(lanes) > 3 else None
  left_adjacent = adjacent(0, outer_left, lane_left)
  right_adjacent = adjacent(3, outer_right, lane_right)
  return ({"count": total, "current": cur, "confidence": conf, "width": width,
           "left_frac": lr - math.floor(lr), "right_frac": rr - math.floor(rr),
           "left_adjacent": left_adjacent, "right_adjacent": right_adjacent},
          "cam %d차로 중 %d (std %.2f, 폭 %.2f, L%.2f R%.2f, 옆차로=%s/%s)" %
          (total, cur, edge_std, width, lr, rr, left_adjacent, right_adjacent))


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
        hi = min(controller.MAX_DISTANCE, max(300.0, v_ego * 20.0, 200.0 * delta))
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
