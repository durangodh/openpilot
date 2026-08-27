from types import SimpleNamespace

from selfdrive.eon_cluster.scene import (align_scene_geometry, camera_lane_position,
                                         final_lateral_path, reconcile_lane_position,
                                         scale_scene_width)


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


def sampled_lane_position_model(left_offsets, right_offsets, centers=None):
  xs = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0]
  centers = centers or [0.0] * len(xs)
  return SimpleNamespace(
    laneLines=[
      polyline(xs, [center + 5.4 for center in centers]),
      polyline(xs, [center + 1.8 for center in centers]),
      polyline(xs, [center - 1.8 for center in centers]),
      polyline(xs, [center - 5.4 for center in centers]),
    ],
    laneLineProbs=[0.8, 0.9, 0.9, 0.8],
    roadEdges=[
      polyline(xs, [center + offset for center, offset in zip(centers, left_offsets)]),
      polyline(xs, [center + offset for center, offset in zip(centers, right_offsets)]),
    ],
    roadEdgeStds=[0.1, 0.1],
  )


def test_camera_lane_position_uses_matched_cross_sections_on_curve():
  centers = [0.0, 0.3, 0.8, 1.5, 2.5, 3.8, 5.2]
  model = sampled_lane_position_model([5.4] * 7, [-5.4] * 7, centers)
  position = camera_lane_position(model)
  assert position["n"] == 3 and position["cur"] == 2
  assert position["laneWidth"] == 3.6


def test_camera_lane_position_rejects_one_outlier_but_fails_closed_on_fork():
  one_outlier = sampled_lane_position_model(
    [5.4, 5.4, 5.4, 12.0, 5.4, 5.4, 5.4], [-5.4] * 7)
  position = camera_lane_position(one_outlier)
  assert position["n"] == 3 and position["cur"] == 2

  inconsistent_fork = sampled_lane_position_model(
    [1.8, 3.6, 5.4, 7.2, 9.0, 10.8, 12.6], [-5.4] * 7)
  assert camera_lane_position(inconsistent_fork) is None


def test_reconcile_lane_position_removes_left_median_phantom_lane():
  # Photo case: the vehicle is in lane 1 of a two-lane road, but the camera
  # rounds the bollard/median space on the left up to a third lane.
  raw = camera_lane_position(lane_position_model(4.8, -5.4))
  assert (raw["n"], raw["cur"]) == (3, 2)
  fixed = reconcile_lane_position(raw, 2)
  assert fixed["reconciled"]
  assert (fixed["n"], fixed["cur"]) == (2, 1)


def test_reconcile_lane_position_removes_left_bollard_phantom_on_single_lane():
  # One real lane with a partial lane-width space to bollards on the left.
  raw = camera_lane_position(lane_position_model(4.8, -1.8))
  assert (raw["n"], raw["cur"]) == (2, 2)
  fixed = reconcile_lane_position(raw, 1)
  assert fixed["reconciled"]
  assert (fixed["n"], fixed["cur"]) == (1, 1)


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


def test_world_geometry_is_anchored_to_final_mpc_path_and_keeps_width():
  path = [[0.0, 0.0, 0.0], [10.0, 1.0, 0.0], [20.0, 2.0, 0.0]]
  lanes = [
    {"p": [[0.0, 5.4], [10.0, 5.4], [20.0, 5.4]], "c": 0.8},
    {"p": [[0.0, 1.8], [10.0, 1.8], [20.0, 1.8]], "c": 0.9},
    {"p": [[0.0, -1.8], [10.0, -1.8], [20.0, -1.8]], "c": 0.9},
    {"p": [[0.0, -5.4], [10.0, -5.4], [20.0, -5.4]], "c": 0.8},
  ]
  edges = [
    {"p": [[0.0, 7.0], [10.0, 7.0], [20.0, 7.0]], "c": 0.9},
    {"p": [[0.0, -7.0], [10.0, -7.0], [20.0, -7.0]], "c": 0.9},
  ]
  fixed_lanes, fixed_edges = align_scene_geometry(path, lanes, edges)
  assert fixed_lanes[1]["p"][1][1] == 2.8
  assert fixed_lanes[2]["p"][1][1] == -0.8
  assert abs(fixed_lanes[1]["p"][1][1] - fixed_lanes[2]["p"][1][1] - 3.6) < 1e-6
  assert fixed_edges[0]["p"][2][1] == 9.0
  assert lanes[1]["p"][1][1] == 1.8  # input is not mutated


def test_world_width_scale_uses_final_path_as_fixed_centre():
  path = [[0.0, 0.0], [10.0, 1.0], [20.0, 2.0]]
  lines = [{"p": [[0.0, 2.0], [10.0, 3.0], [20.0, 4.0]], "c": 0.9}]
  scaled = scale_scene_width(path, lines, 0.5)
  assert scaled[0]["p"] == [[0.0, 1.0], [10.0, 2.0], [20.0, 3.0]]
  assert lines[0]["p"][1][1] == 3.0


def test_world_geometry_interpolates_mismatched_x_grids_consistently():
  path = [[0.0, 0.0], [8.0, 0.8], [20.0, 2.0]]
  xs = [0.0, 5.0, 12.0, 20.0]
  lanes = [
    {"p": [[x, 5.4] for x in xs], "c": 0.8},
    {"p": [[x, 1.8] for x in xs], "c": 0.9},
    {"p": [[x, -1.8] for x in xs], "c": 0.9},
    {"p": [[x, -5.4] for x in xs], "c": 0.8},
  ]
  edges = [{"p": [[x, 7.0] for x in xs], "c": 0.9}]
  fixed_lanes, fixed_edges = align_scene_geometry(path, lanes, edges)
  assert [point[1] for point in fixed_lanes[1]["p"]] == [1.8, 2.3, 3.0, 3.8]
  assert [point[1] for point in fixed_edges[0]["p"]] == [7.0, 7.5, 8.2, 9.0]

  centre_cache = {}
  scaled_lanes = scale_scene_width(path, fixed_lanes, 0.5, centre_cache)
  scaled_edges = scale_scene_width(path, fixed_edges, 0.5, centre_cache)
  assert [point[1] for point in scaled_lanes[1]["p"]] == [0.9, 1.4, 2.1, 2.9]
  assert [point[1] for point in scaled_edges[0]["p"]] == [3.5, 4.0, 4.7, 5.5]
