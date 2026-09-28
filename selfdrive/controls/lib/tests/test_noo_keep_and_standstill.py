"""NOO 분기 keep desire / 정지 중 회전 desire 보류 / 회전 전 감속도 테스트.

Run with: python -m unittest selfdrive.controls.lib.tests.test_noo_keep_and_standstill
"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from selfdrive.controls.lib.navigation_route import NavigationRouteData, NOO_TURN_DECEL
from selfdrive.controls.lib.tests.test_steering_input_regressions import load_planner_module

KEEP_LEFT, KEEP_RIGHT, TURN_LEFT = 5, 6, 1


def car(**kw):
  base = dict(vEgo=20.0, brakePressed=False, steeringPressed=False,
              steeringTorque=0.0, steeringAngleDeg=0.0,
              leftBlinker=False, rightBlinker=False,
              leftBlindspot=False, rightBlindspot=False, standstill=False)
  base.update(kw)
  return NS(**base)


class NooDesireTest(unittest.TestCase):
  def run_frames(self, guidance, frames):
    module = load_planner_module("desire_helper")
    clock = [100.0]
    results = []
    with tempfile.TemporaryDirectory() as temp_dir:
      guide = Path(temp_dir) / "guide.json"
      guide.write_text(json.dumps({"updated_at_ms": 100000, "guidance_current": guidance}))
      with patch("time.time", side_effect=lambda: clock[0]), \
           patch("time.monotonic", side_effect=lambda: clock[0]):
        helper = module.DesireHelper()
        helper.navigation_route = NavigationRouteData(str(guide))
        for carstate in frames:
          helper.update(carstate, True, 0.0)
          results.append((helper.desire, helper.noo_turn_direction, helper.noo_keep_direction))
          clock[0] += 0.05
    return results

  def test_fork_keep_starts_only_with_driver_torque_and_latches(self):
    fork_right = {"turn_type": 18, "distance_m": 60}
    out = self.run_frames(fork_right, [
      car(),                                                   # 토크 없음 → 없음
      car(steeringPressed=True, steeringTorque=-1.0),          # 우측 토크 → keepRight
      car(),                                                   # 손 떼도 유지
      car(steeringPressed=True, steeringTorque=1.0),           # 반대 토크 → 해제
      car(),                                                   # 다시 토크 전까진 없음
    ])
    self.assertEqual([d for d, _, _ in out], [0, KEEP_RIGHT, KEEP_RIGHT, 0, 0])

  def test_fork_keep_is_blocked_by_brake_blindspot_and_distance(self):
    torque_left = dict(steeringPressed=True, steeringTorque=1.0)
    near = {"turn_type": 17, "distance_m": 60}
    self.assertEqual(self.run_frames(near, [car(brakePressed=True, **torque_left)])[0][0], 0)
    self.assertEqual(self.run_frames(near, [car(leftBlindspot=True, **torque_left)])[0][0], 0)
    self.assertEqual(self.run_frames(near, [car(**torque_left)])[0][0], KEEP_LEFT)
    far = {"turn_type": 17, "distance_m": 400}
    self.assertEqual(self.run_frames(far, [car(**torque_left)])[0][0], 0)

  def test_turn_desire_is_withheld_while_stopped_and_resumes(self):
    turn_left = {"turn_type": 12, "distance_m": 30}
    out = self.run_frames(turn_left, [
      car(vEgo=0.0, standstill=True),
      car(vEgo=0.0, standstill=True),
      car(vEgo=2.0, standstill=False),
    ])
    self.assertEqual(out[0][:2], (0, 0))
    self.assertEqual(out[1][:2], (0, 0))
    self.assertEqual(out[2][:2], (TURN_LEFT, -1))


class NooDecelTest(unittest.TestCase):
  def test_turn_approach_uses_compromise_decel(self):
    self.assertEqual(NOO_TURN_DECEL, 0.8)
    state = NavigationRouteData.guidance_state({"turn_type": 12, "distance_m": 150}, True)
    target = 20.0 / 3.6
    braking = 150.0 - target * 6.0
    expected = (target ** 2 + 2.0 * 0.8 * braking) ** 0.5 * 3.6
    self.assertAlmostEqual(NavigationRouteData.speed_limit_kph(state, 20.0), expected)


if __name__ == "__main__":
  unittest.main()
