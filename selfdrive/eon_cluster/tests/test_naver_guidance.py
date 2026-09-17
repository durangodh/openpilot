"""Regression checks for Naver guidance activity without importing openpilot."""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "selfdrive" / "eon_cluster" / "remote_hud.py"
ROUTE_SOURCE = ROOT / "selfdrive" / "controls" / "lib" / "navigation_route.py"


def load_helper():
  tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
  helper = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "_navigation_is_active")
  namespace = {}
  exec(compile(ast.Module(body=[helper], type_ignores=[]), str(SOURCE), "exec"), namespace)
  return namespace["_navigation_is_active"]


def main():
  active = load_helper()
  naver_guide = {"source": "NAVER", "distance_m": 240, "turn_type": 12}

  # HUD6 can report false here solely because its enum string is not "Guiding".
  assert active({"active": False, "state": "GUID"}, naver_guide, 0, True)
  assert active({"active": False, "state": "Unknown"}, naver_guide, 0, True)
  # A stale maneuver must not resurrect navigation after it really ended.
  assert not active({"active": False, "state": "Idle"}, naver_guide, 0, False)
  assert not active({"active": False, "state": "Idle"}, {}, 0, True)
  # Preserve normal TMAP/route behavior.
  assert active({"active": True}, {}, 1500, False)
  assert not active({"active": True}, {}, 0, False)
  # A live real maneuver is sufficient while a newly selected route briefly
  # reports zero/missing remaining distance.
  tmap_guide = {"distance_m": 620, "turn_type": 13, "main_text": "우회전"}
  assert active({"guidance_active": True, "route_present": True}, tmap_guide, 0, True)
  assert not active({"guidance_active": True, "route_present": True}, tmap_guide, 0, False)
  assert not active({"guidance_active": True, "route_present": True},
                    {"distance_m": 0, "turn_type": 0}, 0, True)

  route_tree = ast.parse(ROUTE_SOURCE.read_text(encoding="utf-8"))
  fork_right = next(node.value for node in route_tree.body
                    if isinstance(node, ast.Assign) and
                    any(isinstance(target, ast.Name) and target.id == "FORK_RIGHT"
                        for target in node.targets))
  values = ast.literal_eval(fork_right)
  assert 18 in values
  print("10 navigation guidance and code mapping checks passed")


if __name__ == "__main__":
  main()
