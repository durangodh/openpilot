"""Regression checks for Naver guidance activity without importing openpilot."""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "selfdrive" / "eon_cluster" / "remote_hud.py"


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
  print("6 Naver guidance activity checks passed")


if __name__ == "__main__":
  main()
