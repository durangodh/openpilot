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
