import unittest

from selfdrive.controls.lib.t_follow import (
  CRUISE_GAP_V,
  clamp_desired_follow_distance,
  filter_t_follow_accel,
  get_t_follow_base,
  get_t_follow_decel_margin,
  hold_t_follow_while_decelerating,
  limit_t_follow_change,
  update_t_follow_decel_hold,
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
    self.assertAlmostEqual(hold_t_follow_while_decelerating(1.2, 1.4, True), 1.4)
    self.assertAlmostEqual(hold_t_follow_while_decelerating(1.6, 1.4, True), 1.6)
    self.assertAlmostEqual(hold_t_follow_while_decelerating(1.2, 1.4, False), 1.2)

  def test_one_accel_glitch_does_not_release_deceleration_hold(self):
    filtered = filter_t_follow_accel(-0.4)
    active = update_t_follow_decel_hold(False, filtered)
    self.assertTrue(active)

    filtered = filter_t_follow_accel(0.2, filtered)
    active = update_t_follow_decel_hold(active, filtered)
    self.assertTrue(active)
    self.assertLess(filtered, -0.1)

  def test_hold_releases_after_sustained_deceleration_end(self):
    filtered = filter_t_follow_accel(-0.4)
    active = update_t_follow_decel_hold(False, filtered)
    for _ in range(20):
      filtered = filter_t_follow_accel(0.0, filtered)
      active = update_t_follow_decel_hold(active, filtered)
    self.assertFalse(active)

  def test_deceleration_margin_requires_a_real_lead(self):
    self.assertEqual(get_t_follow_decel_margin(-2.5, 0.3, False), 0.0)
    self.assertAlmostEqual(get_t_follow_decel_margin(-2.5, 0.3, True), 0.075)
    self.assertEqual(get_t_follow_decel_margin(0.0, 0.3, True), 0.0)

  def test_both_t_follow_directions_are_rate_limited(self):
    self.assertAlmostEqual(limit_t_follow_change(1.5, 1.2, dt=0.05), 1.205)
    self.assertAlmostEqual(limit_t_follow_change(1.2, 1.44, dt=0.05), 1.425)

  def test_desired_distance_cannot_be_negative(self):
    self.assertEqual(clamp_desired_follow_distance(6.0, 180.0), 0.0)
    self.assertAlmostEqual(clamp_desired_follow_distance(45.0, 10.0), 35.0)


if __name__ == "__main__":
  unittest.main()
