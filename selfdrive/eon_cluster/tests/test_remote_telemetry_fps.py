"""HUD telemetry respects the configured FPS."""
import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "remote_hud.py"
NAMES = {"MAX_TELEMETRY_FPS", "PAUSED_TELEMETRY_FPS", "_telemetry_fps"}


def load():
  tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
  def wanted(node):
    if isinstance(node, ast.FunctionDef):
      return node.name in NAMES
    return (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and
            node.targets[0].id in NAMES)
  body = [node for node in tree.body if wanted(node)]
  env = {}
  exec(compile(ast.Module(body=body, type_ignores=[]), str(SOURCE), "exec"), env)
  return env["_telemetry_fps"]


def test_configured_fps_is_kept():
  fps = load()
  assert fps(0) == 2
  assert fps(3) == 3
  assert fps(8) == 8
  assert fps(15) == 10
