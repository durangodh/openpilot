from types import SimpleNamespace

from selfdrive.eon_cluster.scene import extract_driving_scene, extract_radar_points


def polyline(xs, ys):
  return SimpleNamespace(x=xs, y=ys)


def test_extract_driving_scene_from_model_and_radar():
  model = SimpleNamespace(
    position=polyline([0.0, 20.0, 60.0], [0.0, 0.2, 0.6]),
    laneLines=[
      polyline([0.0, 40.0, 80.0], [-1.8, -1.7, -1.5]),
      polyline([0.0, 40.0, 80.0], [1.8, 1.7, 1.5]),
    ],
    laneLineProbs=[0.9, 0.8],
    roadEdges=[],
    roadEdgeStds=[],
  )
  radar = SimpleNamespace(
    leadOne=SimpleNamespace(status=True, dRel=32.0, yRel=0.3, vRel=-1.5),
    leadTwo=SimpleNamespace(status=False, dRel=0.0, yRel=0.0, vRel=0.0),
  )
  scene = extract_driving_scene(model, radar)
  assert len(scene["path"]) == 3
  assert len(scene["lanes"]) == 2
  assert scene["lanes"][0]["probability"] == 0.9
  assert scene["leads"] == [{"distance": 32.0, "lateral": 0.3, "relative_speed": -1.5}]


def test_lane_geometry_is_smoothed_and_low_confidence_is_hidden():
  xs = [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
  noisy = [-1.8, -1.45, -1.72, -1.30, -1.48, -1.05, -1.18]
  model = SimpleNamespace(
    position=polyline(xs, [0.0] * len(xs)),
    laneLines=[
      polyline(xs, noisy),
      polyline(xs, [1.8, 2.2, 1.5, 2.3, 1.4, 2.5, 1.3]),
    ],
    laneLineProbs=[0.9, 0.3],
    roadEdges=[],
    roadEdgeStds=[],
  )
  radar = SimpleNamespace(
    leadOne=SimpleNamespace(status=False, dRel=0.0, yRel=0.0, vRel=0.0),
    leadTwo=SimpleNamespace(status=False, dRel=0.0, yRel=0.0, vRel=0.0),
  )
  scene = extract_driving_scene(model, radar)
  assert len(scene["lanes"]) == 1
  smoothed = [point[1] for point in scene["lanes"][0]["points"]]
  assert smoothed != noisy
  second_differences = [
    smoothed[index + 1] - 2.0 * smoothed[index] + smoothed[index - 1]
    for index in range(1, len(smoothed) - 1)
  ]
  assert max(second_differences) - min(second_differences) < 1e-6


def test_road_edges_are_smoothed_and_low_confidence_is_hidden():
  xs = [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
  noisy_edge = [-4.2, -3.7, -4.1, -3.5, -3.9, -3.2, -3.6]
  model = SimpleNamespace(
    position=polyline(xs, [0.0] * len(xs)),
    laneLines=[],
    laneLineProbs=[],
    roadEdges=[
      polyline(xs, noisy_edge),
      polyline(xs, [4.2, 5.0, 3.7, 5.2, 3.6, 5.3, 3.5]),
    ],
    roadEdgeStds=[0.2, 0.75],
  )
  radar = SimpleNamespace(
    leadOne=SimpleNamespace(status=False, dRel=0.0, yRel=0.0, vRel=0.0),
    leadTwo=SimpleNamespace(status=False, dRel=0.0, yRel=0.0, vRel=0.0),
  )
  scene = extract_driving_scene(model, radar)
  assert len(scene["edges"]) == 1
  assert scene["edges"][0]["probability"] == 0.8
  smoothed = [point[1] for point in scene["edges"][0]["points"]]
  assert smoothed != noisy_edge
  second_differences = [
    smoothed[index + 1] - 2.0 * smoothed[index] + smoothed[index - 1]
    for index in range(1, len(smoothed) - 1)
  ]
  assert max(second_differences) - min(second_differences) < 1e-6


def test_invalid_or_far_leads_are_ignored():
  empty_model = SimpleNamespace(position=None, laneLines=[], laneLineProbs=[], roadEdges=[], roadEdgeStds=[])
  radar = SimpleNamespace(
    leadOne=SimpleNamespace(status=True, dRel=200.0, yRel=0.0, vRel=0.0),
    leadTwo=SimpleNamespace(status=False, dRel=20.0, yRel=0.0, vRel=0.0),
  )
  scene = extract_driving_scene(empty_model, radar)
  assert scene["path"] == []
  assert scene["leads"] == []


def test_radar_points_are_filtered_sorted_and_bounded():
  tracks = [SimpleNamespace(trackId=index, dRel=40.0 - index, yRel=0.2, vRel=-1.0,
                            stationary=index % 2 == 0) for index in range(24)]
  tracks.extend([SimpleNamespace(trackId=99, dRel=200.0, yRel=0.0, vRel=0.0, stationary=False),
                 SimpleNamespace(trackId=100, dRel=20.0, yRel=20.0, vRel=0.0, stationary=False)])
  points = extract_radar_points(tracks)
  assert len(points) == 16
  assert [point["distance"] for point in points] == sorted(point["distance"] for point in points)
  assert points[0]["track_id"] == 23
