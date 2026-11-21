"""NOO highway-exit preparation window and NOO route steering (NooHighwayPrepare, NooRouteSteer).

Only native/hardware imports of the lateral planner are replaced; the production route geometry
(NavigationRouteData) and the planner's blend logic run unchanged.
"""
import importlib.util
import math
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import patch

import numpy as np

from selfdrive.controls.lib.navigation_noo import NavigationLaneChangeController
from selfdrive.controls.lib.navigation_route import NavigationRouteData
from selfdrive.controls.lib.tests.test_navigation_noo import confirm_noo, noo_state

LAT0, LON0 = 37.5, 127.0
R = 6371008.8


def lat_lon(x_east, y_north):
  return {"lat": LAT0 + math.degrees(y_north / R),
          "lon": LON0 + math.degrees(x_east / (R * math.cos(math.radians(LAT0))))}


def left_turn_points(corner_m=5.0, radius=10.0):
  """North from 20 m behind the car, then a left arc of `radius` starting `corner_m` ahead."""
  points = [lat_lon(0.0, y) for y in np.arange(-20.0, corner_m, 2.0)]
  for angle in np.linspace(0.0, math.pi / 2, 12)[1:]:
    points.append(lat_lon(-radius + radius * math.cos(angle), corner_m + radius * math.sin(angle)))
  points += [lat_lon(-radius - d, corner_m + radius) for d in (5.0, 10.0, 20.0, 40.0)]
  return points


def straight_points():
  return [lat_lon(0.0, y) for y in np.arange(-40.0, 400.0, 40.0)]


def turn_state(near=None, polyline=None, distance=20.0):
  route = {"polyline": polyline if polyline is not None else straight_points()}
  if near is not None:
    route["polyline_near"] = near
  return {"fresh": True, "route_fresh": True, "kind": "turn", "direction": -1, "turn_type": 12,
          "distance": distance, "route": route, "vehicle": {"lat": LAT0, "lon": LON0}}


# ── highway exit window ─────────────────────────────────────────────────────

def test_highway_prepare_distance():
  f = NavigationLaneChangeController.highway_prepare_distance
  fork = {"kind": "fork", "road_limit_kph": 100.0}
  assert f(fork, 25.0, 3, 1000) == 1000.0          # 3 lanes: 1100 m, capped by the setting
  assert f(fork, 25.0, 2, 1000) == 800.0
  assert f(fork, 25.0, 1, 1000) == 500.0
  assert f(fork, 25.0, 3, 0) == 0.0                # setting 0 = off
  assert f(fork, 25.0, 0, 1000) == 0.0             # nothing to move
  assert f({"kind": "fork", "road_limit_kph": 60.0}, 25.0, 3, 1000) == 0.0   # not a highway
  assert f({"kind": "turn", "road_limit_kph": 100.0}, 25.0, 3, 1000) == 0.0  # only exits
  assert f({"kind": "fork", "road_limit_kph": 0.0}, 25.0, 2, 1000) == 800.0  # 90 km/h, no limit sent
  assert f({"kind": "fork", "road_limit_kph": 0.0}, 15.0, 2, 1000) == 0.0    # 54 km/h, no limit sent


def test_highway_exit_without_lane_guidance_starts_far_ahead():
  ego = {"count": 4, "current": 1, "confidence": 0.9}
  state = noo_state([0, 0, 0, 1], distance=900.0, road_limit=100.0)
  state.update(lane_fresh=False, lane_current=None)
  assert confirm_noo(NavigationLaneChangeController(), state, ego) == 0   # old 350 m window
  controller = NavigationLaneChangeController()
  controller.highway_prepare_m = 1000.0
  assert confirm_noo(controller, state, ego) == 1
  state["distance"] = 1100.0
  controller = NavigationLaneChangeController()
  controller.highway_prepare_m = 1000.0
  assert confirm_noo(controller, state, ego) == 0                          # beyond the setting


def test_highway_exit_with_lane_guidance_uses_lanes_to_move():
  ego = {"count": 3, "current": 1, "confidence": 0.9}
  state = noo_state([0, 0, 1], distance=750.0, road_limit=100.0)
  assert confirm_noo(NavigationLaneChangeController(), state, ego) == 0   # old: 500 m at 90 km/h
  controller = NavigationLaneChangeController()
  controller.highway_prepare_m = 1000.0
  assert confirm_noo(controller, state, ego) == 1                          # 2 lanes: 800 m


def test_city_fork_keeps_the_normal_window():
  ego = {"count": 3, "current": 1, "confidence": 0.9}
  state = noo_state([0, 0, 1], distance=600.0, road_limit=60.0)
  controller = NavigationLaneChangeController()
  controller.highway_prepare_m = 1000.0
  assert confirm_noo(controller, state, ego) == 0


# ── route geometry ──────────────────────────────────────────────────────────

SAMPLES = [4.0 * 10.0 * (i / 32.0) ** 2 for i in range(17)]  # 4 m/s over the MPC horizon


def test_dense_near_window_is_preferred_over_the_thinned_route():
  # The whole-route polyline here is too coarse to show the corner; the near window has it.
  assert NavigationRouteData.route_curvature_profile(turn_state(), SAMPLES) is None
  profile = NavigationRouteData.route_curvature_profile(turn_state(near=left_turn_points()), SAMPLES)
  assert profile is not None and max(profile) > 0.02 and min(profile) >= 0.0   # left only
  # TMAP (no polyline_near) still uses its polyline.
  assert NavigationRouteData.route_curvature_profile(turn_state(polyline=left_turn_points()), SAMPLES)


def test_route_kink_against_the_instruction_never_steers_that_way():
  state = turn_state(near=left_turn_points())
  state["direction"] = 1                                     # told to turn right, route bends left
  assert NavigationRouteData.route_curvature_profile(state, SAMPLES) is None


# ── lateral planner blend ───────────────────────────────────────────────────

def load_lateral_planner():
  def stub(name, **attrs):
    module = ModuleType(name)
    module.__dict__.update(attrs)
    return module

  class Params:
    def get(self, key, encoding=None):
      return None

    def get_bool(self, key):
      return False

  lane_change_state = NS(off=0, preLaneChange=1, laneChangeStarting=2, laneChangeFinishing=3)
  cereal = stub("cereal", log=NS(LateralPlan=NS(LaneChangeState=lane_change_state,
                                                Desire=NS(laneChangeLeft=3, laneChangeRight=4))))
  modules = {
    "common.realtime": stub("common.realtime", sec_since_boot=lambda: 0.0, DT_MDL=0.05),
    "common.params": stub("common.params", Params=Params, put_nonblocking=lambda *a: None),
    "selfdrive.controls.lib.lane_planner": stub("selfdrive.controls.lib.lane_planner", LanePlanner=object),
    "selfdrive.swaglog": stub("selfdrive.swaglog", cloudlog=NS(warning=lambda *a: None)),
    "selfdrive.controls.lib.lateral_mpc_lib": stub("selfdrive.controls.lib.lateral_mpc_lib"),
    "selfdrive.controls.lib.lateral_mpc_lib.lat_mpc": stub(
      "selfdrive.controls.lib.lateral_mpc_lib.lat_mpc", LateralMpc=object, N=16),
    "selfdrive.controls.lib.drive_helpers": stub(
      "selfdrive.controls.lib.drive_helpers", CONTROL_N=17, MIN_SPEED=1.0),
    "selfdrive.controls.lib.desire_helper": stub(
      "selfdrive.controls.lib.desire_helper", DesireHelper=object, AUTO_LCA_START_TIME=1.0),
    "cereal": cereal, "cereal.messaging": stub("cereal.messaging"),
    "selfdrive.controls.lib.lane_auto_center": stub(
      "selfdrive.controls.lib.lane_auto_center", LaneAutoCenter=object),
  }
  source = Path(__file__).parents[1] / "lateral_planner.py"
  spec = importlib.util.spec_from_file_location("lateral_planner_under_test", source)
  module = importlib.util.module_from_spec(spec)
  with patch.dict(sys.modules, modules):
    spec.loader.exec_module(module)
  return module


LP_MODULE = load_lateral_planner()


def planner(mode, speed=4.0, state=None, cancel=False, trusted=True):
  state = state or turn_state(near=left_turn_points())
  lp = LP_MODULE.LateralPlanner.__new__(LP_MODULE.LateralPlanner)
  lp.noo_route_steer = mode
  lp.noo_map_blend = 0.0
  lp.noo_map_profile_cache = None
  lp.noo_map_path_cache = None
  lp.v_ego = speed
  lp.v_plan = np.full(33, speed)
  guide = {k: v for k, v in state.items() if k != "route"}
  lp.DH = NS(navigation_state=guide, noo_turn_direction=-1, noo_driver_cancel=cancel,
             noo_pose_trusted=trusted)
  lp.noo_map_navigation_route = NS(
    update=lambda: state,
    cached_route_curvature_profile=lambda s, d, max_curvature: NavigationRouteData.route_curvature_profile(
      s, d, max_curvature=max_curvature))
  return lp


def run(lp, frames=20, pressed=False, active=True):
  sm = {"carState": NS(steeringPressed=pressed), "carControl": NS(latActive=active)}
  samples = lp.v_ego * np.array([10.0 * (i / 32.0) ** 2 for i in range(17)])
  zeros = np.zeros(17)
  out = None
  for _ in range(frames):
    out = lp.noo_route_steering(sm, samples, zeros.copy(), zeros.copy(), zeros.copy())
  return out


def test_mode_2_blends_the_route_shape_into_the_reference():
  lp = planner(2)
  y, heading, yaw_rate = run(lp)
  assert abs(lp.noo_map_blend - LP_MODULE.NOO_MAP_BLEND_MAX) < 1e-6   # 14 km/h: full blend
  assert y[-1] > 0.0 and heading[-1] > 0.0 and yaw_rate.max() > 0.0   # toward the left turn
  assert y[0] == 0.0                                                  # anchored at the model path


def test_mode_1_only_displays():
  lp = planner(1)
  y, heading, yaw_rate = run(lp)
  assert lp.noo_map_blend > 0.5
  assert not y.any() and not heading.any() and not yaw_rate.any()


def test_mode_0_does_nothing():
  lp = planner(0)
  y, _, _ = run(lp)
  assert lp.noo_map_blend == 0.0 and not y.any()


def test_driver_touch_cancel_or_untrusted_pose_drops_it_at_once():
  lp = planner(2)
  run(lp)
  y, _, _ = run(lp, frames=1, pressed=True)
  assert lp.noo_map_blend == 0.0 and not y.any()
  lp = planner(2, cancel=True)
  assert not run(lp)[0].any()
  lp = planner(2, trusted=False)
  assert not run(lp)[0].any() and lp.noo_map_blend == 0.0
  lp = planner(2)
  assert not run(lp, active=False)[0].any()


def test_blend_fades_with_speed_and_stops_above_50_kph():
  lp = planner(2, speed=35.0 / 3.6)
  run(lp)
  assert 0.0 < lp.noo_map_blend < LP_MODULE.NOO_MAP_BLEND_MAX
  lp = planner(2, speed=55.0 / 3.6)
  assert not run(lp)[0].any() and lp.noo_map_blend == 0.0


def test_outside_the_turn_window_nothing_is_added():
  for distance in (1.0, 80.0):
    lp = planner(2, state=turn_state(near=left_turn_points(), distance=distance))
    assert not run(lp)[0].any()


# ── Naver next-junction lanes (lane_ahead from the route's lane points) ─────

def test_naver_route_lane_points_select_the_next_maneuver():
  # Module output: every lane point ahead, distance along the route from the car.
  lane_ahead = [
    {"source": "NAVER_ROUTE", "count": 3, "available": [0, 1, 0], "distance_m": 180.0},   # current junction
    {"source": "NAVER_ROUTE", "count": 3, "available": [1, 0, 0], "distance_m": 420.0},   # next turn (left)
    {"source": "NAVER_ROUTE", "count": 4, "available": [0, 0, 0, 1], "distance_m": 1300.0},
  ]
  following = {"fresh": True, "direction": -1, "turn_type": 12, "segment": 240.0}  # 180 + 240 = 420 m
  chosen = NavigationRouteData.select_lane_ahead(lane_ahead, 180.0, following)
  assert chosen is lane_ahead[1]
  # Nothing near the next maneuver: no lanes are borrowed from another junction.
  far = {"fresh": True, "direction": -1, "turn_type": 12, "segment": 2000.0}
  assert NavigationRouteData.select_lane_ahead(lane_ahead, 180.0, far) is None


def test_naver_next_junction_lanes_prepare_the_next_turn():
  state = noo_state([1, 1, 0], distance=180.0, direction=-1)   # current: lanes 1-2 allowed
  state["kind"] = "turn"
  state["next"] = {"fresh": True, "direction": -1, "turn_type": 12, "segment": 240.0}
  state["lane_ahead_fresh"] = True
  state["lane_ahead"] = {"source": "NAVER_ROUTE", "count": 3, "available": [1, 0, 0], "distance_m": 420.0}
  ego = {"count": 3, "current": 2, "confidence": 0.9}
  plan = NavigationLaneChangeController.lane_plan(state, ego, 15.0)
  assert plan["source"] == "ahead" and plan["target"] == 1 and plan["direction"] == -1
