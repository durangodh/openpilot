"""NOO preparation for the maneuver after the current one (all navigation apps)."""
import json
from unittest.mock import patch

from selfdrive.controls.lib.navigation_noo import NavigationLaneChangeController
from selfdrive.controls.lib.navigation_route import NavigationRouteData

EGO_MIDDLE = {"count": 3, "current": 2, "confidence": 0.9}


def state(available, distance=100.0, kind="none", next_kind="turn", next_direction=1,
          next_segment=80.0, lane_ahead=None):
  # Naver/Kakao shape: no lane guidance for the next maneuver, its distance is
  # the segment after the current maneuver.
  data = {
    "fresh": True, "route_fresh": True, "lane_fresh": True, "off_route": False,
    "kind": kind, "turn_type": 51, "direction": 0, "distance": distance,
    "road_limit_kph": 50.0,
    "lane_current": {"count": len(available), "available": available, "distance_m": distance},
    "next": {"fresh": True, "kind": next_kind, "direction": next_direction,
             "distance": next_segment, "segment": next_segment, "turn_type": 13},
    "lane_ahead_fresh": lane_ahead is not None, "lane_ahead": lane_ahead,
  }
  return data


def confirm(controller, data, ego=EGO_MIDDLE):
  result = 0
  for _ in range(controller.CONFIRM_FRAMES):
    result = controller.update(data, ego, 15.0, True, True)
  return result


def test_prepares_one_lane_toward_the_next_turn_inside_current_lanes():
  plan = NavigationLaneChangeController.lane_plan(state([1, 1, 1]), EGO_MIDDLE, 15.0)
  assert plan["source"] == "next_prepare"
  assert (plan["current"], plan["target"], plan["direction"]) == (2, 3, 1)
  assert plan["distance"] == 180.0          # 100 m to the current + 80 m segment
  assert confirm(NavigationLaneChangeController(), state([1, 1, 1])) == 1


def test_never_leaves_the_lanes_the_current_maneuver_allows():
  plan = NavigationLaneChangeController.lane_plan(state([1, 1, 0]), EGO_MIDDLE, 15.0)
  assert plan["source"] == "current" and plan["direction"] == 0
  assert confirm(NavigationLaneChangeController(), state([1, 1, 0])) == 0


def test_left_next_turn_moves_left():
  plan = NavigationLaneChangeController.lane_plan(
    state([1, 1, 1], next_direction=-1), EGO_MIDDLE, 15.0)
  assert (plan["target"], plan["direction"]) == (1, -1)


def test_outside_the_prepare_window_or_during_a_fork_keeps_the_lane():
  # 50 km/h road: carrot window 200 m. 100 + 300 = 400 m is too far.
  far = NavigationLaneChangeController.lane_plan(state([1, 1, 1], next_segment=300.0),
                                                EGO_MIDDLE, 15.0)
  assert far["source"] == "current" and far["direction"] == 0
  fork = NavigationLaneChangeController.lane_plan(state([1, 1, 1], kind="fork"), EGO_MIDDLE, 15.0)
  assert fork is None or fork["source"] != "next_prepare"
  straight_next = NavigationLaneChangeController.lane_plan(
    state([1, 1, 1], next_kind="none"), EGO_MIDDLE, 15.0)
  assert straight_next["source"] == "current"


def test_real_lane_ahead_guidance_takes_priority():
  ahead = {"count": 3, "available": [1, 0, 0], "distance_m": 180.0}
  plan = NavigationLaneChangeController.lane_plan(
    state([1, 1, 1], next_direction=-1, lane_ahead=ahead), EGO_MIDDLE, 15.0)
  assert plan["source"] == "ahead"
  assert (plan["target"], plan["direction"]) == (1, -1)


def test_tmap_lane_ahead_list_picks_the_next_maneuver_entry():
  entries = [{"count": 3, "available": [0, 1, 0], "distance_m": 120.0},
             {"count": 3, "available": [0, 0, 1], "distance_m": 410.0},
             {"count": 4, "available": [1, 0, 0, 0], "distance_m": 900.0}]
  next_state = {"segment": 280.0}
  # current 130 m + segment 280 m = 410 m
  assert NavigationRouteData.select_lane_ahead(entries, 130.0, next_state) is entries[1]
  # nothing near 2000 m: fail closed instead of using another junction's lanes
  assert NavigationRouteData.select_lane_ahead(entries, 1500.0, {"segment": 500.0}) is None
  assert NavigationRouteData.select_lane_ahead(entries, 130.0, None) is None
  single = {"count": 3, "available": [0, 0, 1]}
  assert NavigationRouteData.select_lane_ahead(single, 130.0, None) is single


def test_tmap_list_payload_is_fresh_end_to_end(tmp_path):
  now = 100.0
  root = {
    "updated_at_ms": 100000,
    "stream_updated_at_ms": {key: 99900 for key in ("guidance_current", "guidance_next",
                                                    "lane_current", "lane_ahead")},
    "guidance_current": {"turn_type": 12, "distance_m": 130},
    # carrot_navi_server adds display_distance_m (segment) to TMAP's next item.
    "guidance_next": {"turn_type": 13, "distance_m": 410, "display_distance_m": 280},
    "lane_current": {"count": 3, "available": [1, 1, 0]},
    "lane_ahead": [{"count": 3, "available": [0, 1, 0], "distance_m": 130},
                   {"count": 3, "available": [0, 0, 1], "distance_m": 410}],
    "route": {"polyline": []}, "vehicle": {"lat": 37.5, "lon": 127.1},
  }
  path = tmp_path / "route.json"
  path.write_text(json.dumps(root))
  with patch("time.time", return_value=now), patch("time.monotonic", return_value=now):
    result = NavigationRouteData(str(path)).update()
  assert result["lane_ahead_fresh"]
  assert result["lane_ahead"]["available"] == [0, 0, 1]
