#!/usr/bin/env python3
"""Guard the native traffic-signal asset path from nav app to Remote HUD."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def assignment(source, name):
  tree = ast.parse(source)
  for node in tree.body:
    if isinstance(node, ast.Assign):
      for target in node.targets:
        if isinstance(target, ast.Name) and target.id == name:
          return ast.literal_eval(node.value)
  raise AssertionError("missing assignment: %s" % name)


def test_signal_stream_enabled_and_forwarded():
  server_path = ROOT / "selfdrive/carrot_navi_server.py"
  server = server_path.read_text(encoding="utf-8")
  assert "traffic_signal" in assignment(server, "JSON_NAMES")
  assert "traffic_signal" in assignment(server, "IMAGE_NAMES")
  assert "traffic_signal" in assignment(server, "ENABLED")
  assert '"traffic_signal": TRAFFIC_SIGNAL_FILE' in server

  relay = (ROOT / "selfdrive/eon_cluster/remote_hud.py").read_text(encoding="utf-8")
  assert '(b"SIG1", TRAFFIC_SIGNAL_FILE, OVERLAY_MAX_BYTES, b"")' in relay

  hud = (ROOT / "selfdrive/eon_cluster/android_hud/app/src/main/java/ai/comma/remotehud/HudService.java").read_text(encoding="utf-8")
  assert 'tagEquals(header, "SIG1")' in hud
  assert "trafficSignalFrame" in hud
  assert "drawNativeOverlay(c, p, trafficSignal" in hud


if __name__ == "__main__":
  test_signal_stream_enabled_and_forwarded()
  print("traffic signal transport test passed")
