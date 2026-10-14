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


def test_kakao_signal_uses_native_hud_size():
  signal = (ROOT / "selfdrive/eon_cluster/kakao_xposed/app/src/main/java/ai/comma/kakaohud/KakaoSignal.java").read_text(
      encoding="utf-8")
  # Keep the 302x192 transport canvas (shared HUD placement) but render Kakao's
  # actual signal at 132x96 instead of enlarging it inside that canvas.
  assert "private static final int CANVAS_W = 302;" in signal
  assert "private static final int CANVAS_H = 192;" in signal
  assert "private static final float CONTENT_SCALE = 1.0f;" in signal


def test_tmap_and_naver_signal_match_kakao_size():
  # 세 네비 모두 302x192 캔버스에 132x96 원본 크기로 그린다(HUD 에서 같은 크기).
  tmap = (ROOT / "selfdrive/eon_cluster/tmap_xposed/app/src/main/java/ai/comma/tmaphud/TmapSignal.java").read_text(
      encoding="utf-8")
  assert "private static final float CONTENT_SCALE = 1.0f;" in tmap
  naver = (ROOT / "selfdrive/eon_cluster/naver_xposed/app/src/main/java/ai/comma/naverhud/NaverSignal.java").read_text(
      encoding="utf-8")
  assert "private static final int CANVAS_W = 302;" in naver
  assert "private static final int CANVAS_H = 192;" in naver
  assert "frame(1, png, CANVAS_W, CANVAS_H)" in naver


if __name__ == "__main__":
  test_signal_stream_enabled_and_forwarded()
  test_kakao_signal_uses_native_hud_size()
  test_tmap_and_naver_signal_match_kakao_size()
  print("traffic signal transport test passed")
