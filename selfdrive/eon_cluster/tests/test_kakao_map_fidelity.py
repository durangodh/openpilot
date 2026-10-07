#!/usr/bin/env python3
"""Regression guards for matching Kakao's original driving-map presentation."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "selfdrive/eon_cluster/kakao_xposed/app/src/main/java/ai/comma/kakaohud/KakaoMap.java"


def source():
  return SOURCE.read_text(encoding="utf-8")


def test_screen_camera_preserves_both_anchors_and_native_camera_values():
  code = source()
  assert "markerAnchorX = screenSync ? screen.anchorX : ANCHOR_X;" in code
  assert "markerAnchorY = screenSync ? screen.anchorY : ANCHOR_Y;" in code
  assert "screen.bearing != null" in code
  assert "screen.tilt != null" in code
  assert "screen.zoom != null" in code


def test_native_marker_has_small_safe_fallback():
  code = source()
  assert "FALLBACK_MARKER_RADIUS_HEIGHT = 0.035f" in code
  assert "prepareNativeUserMarker();" in code
  assert "updateNativeUserMarker();" in code
  assert "if (!nativeMarkerActive) drawVehicleMarker(frame);" in code
  assert "nativeLocationVisibleMethod.invoke(user, true);" in code
  assert "visible.invoke(user, false);" in code


def test_reused_route_objects_are_reapplied():
  code = source()
  assert "pendingRouteRevision++;" in code
  assert "revision == appliedRouteRevision" in code
  assert "appliedRouteRevision = revision;" in code
  assert "setRoutesMethod.invoke(mapTarget(), mapRoutes);" in code


if __name__ == "__main__":
  test_screen_camera_preserves_both_anchors_and_native_camera_values()
  test_native_marker_has_small_safe_fallback()
  test_reused_route_objects_are_reapplied()
  print("Kakao map fidelity tests passed")
