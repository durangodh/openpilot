"""controlsd (CruiseHelper) must not parse the route polyline file except for the NOO route curve.

2026-11-08 profile: the whole-route JSON (polyline) parsed in the 100 Hz control loop made it
run past 10 ms. Everything but the route-curve speed reads the guide file (no polyline).
"""
import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "cruise_helper.py"


def test_guidance_reader_uses_the_guide_file():
  tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
  readers = {}
  for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and \
       getattr(node.value.func, "id", "") == "NavigationRouteData":
      readers[ast.unparse(node.targets[0])] = ast.unparse(node.value.args[0])
  assert readers == {"self.navigation_route": "GUIDE_FILE",
                     "self.navigation_route_full": "STATE_FILE"}


def test_full_route_file_only_for_the_route_curve():
  text = SOURCE.read_text(encoding="utf-8")
  uses = [line.strip() for line in text.splitlines()
          if "navigation_route_full" in line and "=" not in line.split("navigation_route_full")[1][:3]]
  assert uses == ["map_speed = self.navigation_route_full.cached_map_curve_speed_kph(",
                  "self.navigation_route_full.update(), CS.out.vEgo * CV.MS_TO_KPH,"]
