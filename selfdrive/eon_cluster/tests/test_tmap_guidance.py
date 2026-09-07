"""TMAP lifecycle regressions using the actual HUD summary helper."""
from test_naver_guidance import load_helper


def main():
  active = load_helper()
  zero = {"turn_type": 0, "distance_m": 0}
  turn = {"turn_type": 12, "distance_m": 250}
  guiding = {"guidance_active": True, "route_present": True, "mode": "guiding"}
  idle = {"guidance_active": False, "route_present": False, "mode": "idle"}
  assert not active(idle, zero, 0, True)
  assert not active(guiding, zero, 0, True)
  assert not active(idle, turn, 2000, True)  # Stale route after ending guidance.
  assert not active({**guiding, "route_present": False}, turn, 2000, True)
  assert active(guiding, turn, 2000, True)
  assert active(guiding, zero, 2000, True)  # 0 m can be a real turn at a junction.
  assert active({**guiding, "mode": "off_route"}, turn, 2000, True)
  print("7 TMAP lifecycle checks passed")


if __name__ == "__main__":
  main()
