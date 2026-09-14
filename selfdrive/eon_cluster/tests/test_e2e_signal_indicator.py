#!/usr/bin/env python3
"""Guard the E2E stop/start icon in the former drive-mode label slot."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def main():
  sender = (ROOT / "selfdrive/eon_cluster/remote_hud.py").read_text(encoding="utf-8")
  hud = (ROOT / "selfdrive/eon_cluster/android_hud/app/src/main/java/ai/comma/remotehud/HudService.java").read_text(encoding="utf-8")

  assert '"trafficState": max(0, min(2, int(_finite(' in sender
  start = hud.index("    private void drawModeAndEta(")
  end = hud.index("    private void drawRange(", start)
  mode_area = hud[start:end]
  assert 's.optInt("trafficState", 0)' in mode_area
  assert "drawInferredTrafficSignal" in mode_area
  assert 'state == 1 ? Color.rgb(255, 58, 70)' in mode_area
  assert 'state == 2 ? Color.rgb(47, 219, 119)' in mode_area
  assert 'c.drawRoundRect(scratchRect, 10f, 10f, p)' in mode_area
  assert 'if (state != 1 && state != 2)' not in mode_area
  for removed in ('"NORM"', '"SAFE"', '"ECO"', '"FAST"', '"E2E"'):
    assert removed not in mode_area
  print("E2E signal indicator test passed")


if __name__ == "__main__":
  main()
