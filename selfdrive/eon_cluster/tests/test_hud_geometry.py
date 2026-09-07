import copy
import unittest

from selfdrive.eon_cluster.hud_geometry import normalize_geometry


class TestHudGeometry(unittest.TestCase):
  def scene(self):
    # A left bend and objects on the left, represented in each source's axis.
    return {"path": [[10, -1, 0.3], [30, -3, 0.8]],
            "lanes": [{"p": [[10, -2], [30, -4]], "c": 0.9}],
            "edges": [{"p": [[10, -5], [30, -7]], "c": 0.8}],
            "pathOffset": -0.2,
            "lead": {"d": 30, "y": 3}, "lead2": {"d": 35, "y": -3},
            "visionObjects": [{"d": 30, "y": 3, "src": "M"}],
            "phoneVisionObjects": [{"d": 30, "y": 3, "vy": 0.5, "src": "P"}],
            "navi": {"scene": {"curve": [[10, 1], [30, 3]]}},
            "mapPose": [37, 127, 0], "cameraGround": {"m": list(range(9))}}

  def test_sources_share_left_axis_and_preserve_other_fields(self):
    raw = self.scene()
    packet = normalize_geometry(copy.deepcopy(raw), 0)
    self.assertEqual(packet["path"], [[10, 1, 0.3], [30, 3, 0.8]])
    self.assertEqual(packet["lanes"], [{"p": [[10, 2], [30, 4]], "c": 0.9}])
    self.assertEqual(packet["edges"], [{"p": [[10, 5], [30, 7]], "c": 0.8}])
    self.assertEqual(packet["pathOffset"], 0.2)
    for key in ("lead", "lead2", "visionObjects", "phoneVisionObjects",
                "navi", "mapPose", "cameraGround"):
      self.assertEqual(packet[key], raw[key])

  def test_flip_changes_only_display_flag_and_is_reversible(self):
    normal = normalize_geometry(self.scene(), 0)
    flipped = normalize_geometry(copy.deepcopy(normal), 1)
    self.assertEqual(flipped.pop("hudPathFlip"), 1)
    expected = copy.deepcopy(normal)
    expected.pop("hudPathFlip")
    self.assertEqual(flipped, expected)
    self.assertEqual(normalize_geometry(flipped, 0), normal)

  def test_right_and_empty_geometry(self):
    packet = normalize_geometry({"path": [[10, 2, 1, 99], [30, 4]],
                                 "lanes": [None, {"p": []}], "edges": None}, 1)
    self.assertEqual(packet["path"], [[10, -2, 1, 99], [30, -4]])
    self.assertEqual(packet["lanes"], [None, {"p": []}])
    self.assertIsNone(packet["edges"])
    self.assertEqual(normalize_geometry({}, 0)["hudPathFlip"], 0)


if __name__ == "__main__":
  unittest.main()
