"""Remote key commands need telemetry tickets at >= 5 FPS (no messaging imports)."""
import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "remote_hud.py"
NAMES = {"MAX_TELEMETRY_FPS", "PAUSED_TELEMETRY_FPS", "REMOTE_MIN_TELEMETRY_FPS", "_telemetry_fps"}


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


def test_configured_fps_is_kept_without_a_remote_key():
  fps = load()
  assert fps(0, "") == 2
  assert fps(3, "") == 3
  assert fps(15, "") == 10


def test_remote_key_keeps_a_five_fps_floor():
  fps = load()
  key = "0" * 32
  assert fps(0, key) == 5
  assert fps(3, key) == 5
  assert fps(8, key) == 8
