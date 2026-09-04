import json
from types import SimpleNamespace

from common.transformations.camera import get_view_frame_from_road_frame
from selfdrive.eon_cluster import remote_hud


def _lead(prob, x, y):
  return SimpleNamespace(prob=prob, x=[x], y=[y])


def test_model_candidates_are_validated_and_deduplicated():
  model = SimpleNamespace(leadsV3=[
    _lead(0.82, 31.52, -0.5),
    _lead(0.75, 32.0, -0.6),  # same physical candidate
    _lead(0.66, 46.52, 3.2),
    _lead(0.10, 20.0, 0.0),   # below display threshold
  ])

  assert remote_hud._model_vision_objects(model) == [
    {"d": 30.0, "y": 0.5, "p": 0.82, "src": "M"},
    {"d": 45.0, "y": -3.2, "p": 0.66, "src": "M"},
  ]


def test_detector_objects_require_fresh_timestamp(tmp_path, monkeypatch):
  detections = tmp_path / "vision_vehicle_objects.json"
  monkeypatch.setattr(remote_hud, "VISION_OBJECTS_FILE", str(detections))
  remote_hud._VISION_OBJECTS_CACHE.update(signature=None, objects=[])
  detections.write_text(json.dumps({
    "updated_at_ms": 10_000,
    "objects": [
      {"d": 18.0, "y": 3.1, "p": 0.91},
      {"d": 0.5, "y": 0.0, "p": 0.99},
      {"d": 25.0, "y": 0.0, "p": 0.12},
    ],
  }), encoding="utf-8")

  assert remote_hud._detector_vision_objects(10_850) == [
    {"d": 18.0, "y": 3.1, "p": 0.91, "src": "D"},
  ]
  assert remote_hud._detector_vision_objects(10_901) == []


def test_detector_wins_duplicate_merge(monkeypatch):
  monkeypatch.setattr(remote_hud, "_detector_vision_objects", lambda: [
    {"d": 30.0, "y": 0.5, "p": 0.55, "src": "D"},
  ])
  model = SimpleNamespace(leadsV3=[_lead(0.95, 31.52, -0.5)])

  assert remote_hud._vision_objects(model) == [
    {"d": 30.0, "y": 0.5, "p": 0.55, "src": "D"},
  ]


def test_model_candidates_survive_missing_detector_output(tmp_path, monkeypatch):
  missing = tmp_path / "missing_vehicle_detector_output.json"
  monkeypatch.setattr(remote_hud, "VISION_OBJECTS_FILE", str(missing))
  remote_hud._VISION_OBJECTS_CACHE.update(signature=None, objects=[])
  model = SimpleNamespace(leadsV3=[_lead(0.81, 26.52, -1.1)])

  assert remote_hud._vision_objects(model) == [
    {"d": 25.0, "y": 1.1, "p": 0.81, "src": "M"},
  ]


def test_radar_lead_wire_keeps_sensor_provenance():
  radar = SimpleNamespace(leadOne=SimpleNamespace(
    status=True, dRel=21.0, yRel=-0.3, vRel=-1.0, aLeadK=-0.2,
    radar=True, modelProb=0.76,
  ))
  vision = SimpleNamespace(leadOne=SimpleNamespace(
    status=True, dRel=21.0, yRel=-0.3, vRel=-1.0, aLeadK=-0.2,
    radar=False, modelProb=0.76,
  ))

  assert remote_hud._lead(radar, "leadOne")["src"] == "R"
  assert remote_hud._lead(vision, "leadOne")["src"] == "V"


def test_camera_ground_homography_is_finite_and_matches_eon_frame():
  extrinsic = get_view_frame_from_road_frame(0.0, 0.0, 0.0, 1.22)
  calibration = SimpleNamespace(extrinsicMatrix=extrinsic.reshape(-1).tolist())
  remote_hud._CAMERA_GROUND_CACHE.update(signature=None, value=None)

  ground = remote_hud._camera_ground(calibration)
  assert ground["w"] == 1164
  assert ground["h"] == 874
  assert len(ground["m"]) == 9
  assert all(abs(value) < 1e6 for value in ground["m"])
  # Invalid/stale calibration fails closed, so phone detections disappear.
  assert remote_hud._camera_ground(SimpleNamespace(extrinsicMatrix=[])) is None
