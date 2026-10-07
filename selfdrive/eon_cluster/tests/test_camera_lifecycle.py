#!/usr/bin/env python3
"""Regression guards for clearing passed camera information from the HUD."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_kakao_does_not_recache_or_publish_zero_distance_events():
  bridge = (ROOT / "selfdrive/eon_cluster/kakao_xposed/app/src/main/java/ai/comma/kakaohud/KakaoBridge.java").read_text(
      encoding="utf-8")
  assert "if (distance > 0 && distance < bestDistance)" in bridge
  assert "if (remaining <= 0)" in bridge
  assert "if (distance <= 0)" in bridge
  # Both the section and fixed-camera pass paths latch the cached item empty,
  # preventing the same delayed SDK item from appearing again next frame.
  assert bridge.count("cachedSafetyEmpty = true; cachedSafetyItem = null;") >= 4


def test_sender_and_renderer_require_positive_distance():
  sender = (ROOT / "selfdrive/eon_cluster/remote_hud.py").read_text(encoding="utf-8")
  assert "if cam_speed <= 0 or cam_dist <= 0:" in sender
  assert "camera_section = False" in sender

  hud = (ROOT / "selfdrive/eon_cluster/android_hud/app/src/main/java/ai/comma/remotehud/HudService.java").read_text(
      encoding="utf-8")
  draw_camera = hud.split("private void drawCamera(", 1)[1].split("private void drawTpms(", 1)[0]
  assert "if (limit <= 0 || dist <= 0)" in draw_camera


if __name__ == "__main__":
  test_kakao_does_not_recache_or_publish_zero_distance_events()
  test_sender_and_renderer_require_positive_distance()
  print("camera lifecycle tests passed")
