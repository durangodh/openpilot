import unittest

from selfdrive.controls.lib.t_follow import (
  CRUISE_GAP_V,
  desired_follow_distance,
  get_t_follow_base,
  get_t_follow_decel_margin,
  hold_t_follow_while_decelerating,
  limit_t_follow_increase,
)


class TestTFollow(unittest.TestCase):
  def test_base_keeps_configured_high_speed_scaling(self):
    self.assertAlmostEqual(get_t_follow_base(1, CRUISE_GAP_V, 0.0, 1.2, 1.0), 1.10)
    self.assertAlmostEqual(get_t_follow_base(1, CRUISE_GAP_V, 100.0, 1.2, 1.0), 1.32)
    self.assertAlmostEqual(get_t_follow_base(4, CRUISE_GAP_V, 100.0, 1.2, 1.0), 1.92)

  def test_safe_mode_only_increases_the_base_gap(self):
    normal = get_t_follow_base(2, CRUISE_GAP_V, 50.0, 1.2, 1.0)
    safe = get_t_follow_base(2, CRUISE_GAP_V, 50.0, 1.2, 0.5)
    self.assertAlmostEqual(safe, normal * 1.5)

  def test_deceleration_holds_only_gap_reductions(self):
    self.assertAlmostEqual(hold_t_follow_while_decelerating(1.2, 1.4, -0.3), 1.4)
    self.assertAlmostEqual(hold_t_follow_while_decelerating(1.6, 1.4, -0.3), 1.6)
    self.assertAlmostEqual(hold_t_follow_while_decelerating(1.2, 1.4, 0.0), 1.2)

  def test_deceleration_margin_requires_a_real_lead(self):
    self.assertEqual(get_t_follow_decel_margin(-2.5, 0.3, False), 0.0)
    self.assertAlmostEqual(get_t_follow_decel_margin(-2.5, 0.3, True), 0.075)
    self.assertEqual(get_t_follow_decel_margin(0.0, 0.3, True), 0.0)

  def test_increase_is_rate_limited_but_decrease_is_immediate(self):
    self.assertAlmostEqual(limit_t_follow_increase(1.5, 1.2, dt=0.05), 1.205)
    self.assertAlmostEqual(limit_t_follow_increase(1.1, 1.2, dt=0.05), 1.1)

  def test_desired_distance_uses_one_braking_model_for_ego_and_lead(self):
    speed = 100.0 / 3.6
    distance = desired_follow_distance(speed, speed, 1.2, 6.0, 2.5)
    self.assertAlmostEqual(distance, 1.2 * speed + 6.0)
    self.assertGreater(desired_follow_distance(speed, speed * 0.8, 1.2, 6.0, 2.5), distance)


if __name__ == "__main__":
  unittest.main()
