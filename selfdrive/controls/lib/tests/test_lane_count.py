"""HUD and NOO must read the same lane from the same frame (lane_count.py)."""
from types import SimpleNamespace

from selfdrive.controls.lib import lane_count
from selfdrive.controls.lib.navigation_noo import NavigationLaneChangeController
from selfdrive.controls.lib.navigation_route import NavigationRouteData
from selfdrive.eon_cluster.scene import camera_lane_position, reconcile_lane_position


def line(xs, ys):
  return SimpleNamespace(x=list(xs), y=list(ys))


def model(left_edge, right_edge, centers=None, edge_std=0.1):
  xs = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0]
  c = centers or [0.0] * len(xs)
  return SimpleNamespace(
    laneLines=[line(xs, [v + 5.4 for v in c]), line(xs, [v + 1.8 for v in c]),
               line(xs, [v - 1.8 for v in c]), line(xs, [v - 5.4 for v in c])],
    laneLineProbs=[0.8, 0.9, 0.9, 0.8],
    roadEdges=[line(xs, [v + left_edge for v in c]), line(xs, [v + right_edge for v in c])],
    roadEdgeStds=[edge_std, 0.1],
  )


CASES = [
  (2.5, -9.0, None), (5.4, -5.4, None), (9.0, -2.5, None),
  (4.8, -5.4, 2), (4.8, -1.8, 1), (2.5, -8.4, 2), (4.8, -8.4, 2), (4.8, -4.8, 2),
  (5.4, -5.4, 3), (5.4, -5.4, 2),
]


def test_hud_and_noo_agree_on_raw_and_reconciled_lane():
  for left, right, route in CASES:
    md = model(left, right)
    noo = NavigationRouteData.camera_lane_position(md)
    hud = camera_lane_position(md)
    assert (hud["n"], hud["cur"]) == (noo["count"], noo["current"])
    if route is not None:
      noo_lane = NavigationLaneChangeController._resolve_current_lane(noo, route)
      hud_fixed = reconcile_lane_position(hud, route)
      assert (hud_fixed["cur"] if hud_fixed else None) == noo_lane, (left, right, route)


def test_curve_uses_matched_cross_sections():
  # On a curve the old NOO path compared unrelated points per line.
  centers = [0.0, 0.3, 0.8, 1.5, 2.5, 3.8, 5.2]
  position = lane_count.camera_lane_position(model(5.4, -5.4, centers))
  assert (position["count"], position["current"]) == (3, 2)


def test_edge_slightly_inside_lane_is_snapped_but_far_inside_fails():
  snapped = lane_count.camera_lane_position(model(1.5, -5.4))   # 0.3 m inside
  assert (snapped["count"], snapped["current"]) == (2, 1)
  position, reason = lane_count.estimate(model(0.9, -5.4))       # 0.9 m inside
  assert position is None and "edge_inside" in reason


def test_reason_names_the_failing_gate():
  assert lane_count.estimate(None) == (None, "no modelV2")
  position, reason = lane_count.estimate(model(5.4, -5.4, edge_std=1.5))
  assert position is None and reason.startswith("roadEdgeStd")
