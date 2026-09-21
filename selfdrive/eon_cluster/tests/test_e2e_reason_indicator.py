#!/usr/bin/env python3
"""Guard the EON Conditional E2E reason badge and its planner transport."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def main():
  schema = (ROOT / "cereal/log.capnp").read_text(encoding="utf-8")
  planner = (ROOT / "selfdrive/controls/lib/longitudinal_planner.py").read_text(encoding="utf-8")
  onroad = (ROOT / "selfdrive/ui/qt/onroad.cc").read_text(encoding="utf-8")

  assert "e2eReason @65 :UInt8" in schema
  assert "longitudinalPlan.e2eReason" in planner
  assert 'getE2eReason()' in onroad
  for label in ('"OFF"', '"ACC"', '"SIG"', '"VIS"', '"GO"', '"E2E"'):
    assert label in onroad

  start = onroad.index("  // ---- Conditional E2E reason")
  end = onroad.index("  // ---- LIMIT / CAM ----", start)
  reason_area = onroad[start:end]
  for removed in ('"MAP"', '"NDA"', '"HDA"'):
    assert removed not in reason_area

  print("EON Conditional E2E reason indicator checks passed")


if __name__ == "__main__":
  main()
