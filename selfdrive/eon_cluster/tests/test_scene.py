from types import SimpleNamespace

from selfdrive.eon_cluster.scene import (camera_lane_position, final_lateral_path,
                                         reconcile_lane_position)


def polyline(xs, ys):
  return SimpleNamespace(x=xs, y=ys)


def test_final_lateral_path_uses_optimized_mpc_xy_and_model_z():
  lateral = SimpleNamespace(
    mpcSolutionValid=True,
    dPathPoints=[0.0, 9.0, 18.0],
    mpcPathX=[0.0, 0.9, 3.7],
    mpcPathY=[0.0, 0.4, 1.8],
  )
  model = SimpleNamespace(
    position=SimpleNamespace(z=[0.0, 0.1, 0.2]),
  )
  assert final_lateral_path(lateral, model, [0.0, 0.1, 0.4]) == [
    [0.0, 0.0, 0.0], [0.9, 0.4, 0.1], [3.7, 1.8, 0.2],
  ]


def test_final_lateral_path_rejects_invalid_or_missing_optimized_mpc_path():
  model = SimpleNamespace(position=SimpleNamespace(z=[]))
  invalid = SimpleNamespace(mpcSolutionValid=False,
                            mpcPathX=[0.0, 1.0], mpcPathY=[0.0, 1.0])
  missing_solution = SimpleNamespace(mpcSolutionValid=True,
                                     dPathPoints=[0.0, 1.0])
  assert final_lateral_path(invalid, model, [0.0, 1.0]) == []
  assert final_lateral_path(missing_solution, model, [0.0, 1.0]) == []


def lane_position_model(left_edge, right_edge, lane_probs=(0.8, 0.9, 0.9, 0.8),
                        edge_stds=(0.1, 0.1)):
  xs = [0.0, 10.0, 20.0, 30.0]
  return SimpleNamespace(
    laneLines=[polyline(xs, [5.4] * 4), polyline(xs, [1.8] * 4),
               polyline(xs, [-1.8] * 4), polyline(xs, [-5.4] * 4)],
    laneLineProbs=list(lane_probs),
    roadEdges=[polyline(xs, [left_edge] * 4), polyline(xs, [right_edge] * 4)],
    roadEdgeStds=list(edge_stds),
  )


def test_camera_lane_position_tracks_left_middle_and_right_lane():
  assert camera_lane_position(lane_position_model(2.5, -9.0))["cur"] == 1
  middle = camera_lane_position(lane_position_model(5.4, -5.4))
  assert middle["n"] == 3 and middle["cur"] == 2
  assert camera_lane_position(lane_position_model(9.0, -2.5))["cur"] == 3


def test_camera_lane_position_accepts_edge_order_and_rejects_weak_geometry():
  reversed_edges = lane_position_model(-5.4, 5.4)
  assert camera_lane_position(reversed_edges) == {
    "n": 3, "cur": 2, "confidence": 0.9, "laneWidth": 3.6,
    "leftFrac": 0.0, "rightFrac": 0.0,
  }
  assert camera_lane_position(lane_position_model(
    5.4, -5.4, lane_probs=(0.8, 0.2, 0.9, 0.8))) is None
  assert camera_lane_position(lane_position_model(
    5.4, -5.4, edge_stds=(0.8, 0.1))) is None


def test_reconcile_lane_position_removes_left_median_phantom_lane():
  # Photo case: the vehicle is in lane 1 of a two-lane road, but the camera
  # rounds the bollard/median space on the left up to a third lane.
  raw = camera_lane_position(lane_position_model(4.8, -5.4))
  assert (raw["n"], raw["cur"]) == (3, 2)
  fixed = reconcile_lane_position(raw, 2)
  assert fixed["reconciled"]
  assert (fixed["n"], fixed["cur"]) == (2, 1)


def test_reconcile_lane_position_removes_right_shoulder_phantom_lane():
  raw = camera_lane_position(lane_position_model(2.5, -8.4))
  assert (raw["n"], raw["cur"]) == (3, 1)
  fixed = reconcile_lane_position(raw, 2)
  assert (fixed["n"], fixed["cur"]) == (2, 1)


def test_reconcile_lane_position_rejects_ambiguous_or_real_extra_lane():
  ambiguous = camera_lane_position(lane_position_model(4.8, -8.4))
  assert reconcile_lane_position(ambiguous, 2) is None

  whole_extra_lane = camera_lane_position(lane_position_model(5.4, -5.4))
  assert reconcile_lane_position(whole_extra_lane, 2) is None
