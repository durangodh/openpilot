import unittest

from selfdrive.controls.lib.t_follow import (
  StoppedLeadComfortBrake,
  T_FOLLOW_DECEL_RELEASE_RATE, T_FOLLOW_DT,
  CRUISE_GAP_V,
  clamp_desired_follow_distance,
  filter_t_follow_accel,
  get_stopped_lead_comfort_brake,
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

  def test_deceleration_tapers_only_gap_reductions(self):
    # A shorter target gap is approached gradually while braking, not held.
    self.assertAlmostEqual(hold_t_follow_while_decelerating(1.2, 1.4, True),
                           1.4 - T_FOLLOW_DECEL_RELEASE_RATE * T_FOLLOW_DT)
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

  def test_stopped_lead_caps_high_speed_comfort_brake(self):
    # 70 km/h (19.44 m/s) approaching a confirmed stopped lead.
    capped = get_stopped_lead_comfort_brake(2.5, 70.0 / 3.6, 0.0, True)
    self.assertLess(capped, 1.6)

  def test_stopped_lead_cap_requires_confirmed_high_closing_risk(self):
    self.assertEqual(get_stopped_lead_comfort_brake(2.5, 70.0 / 3.6, 0.0, False), 2.5)
    self.assertEqual(get_stopped_lead_comfort_brake(2.5, 8.0, 0.0, True), 2.5)
    self.assertEqual(get_stopped_lead_comfort_brake(2.5, 20.0, 18.0, True), 2.5)

  def test_stopped_lead_cap_enters_smoothly_as_lead_slows(self):
    values = [get_stopped_lead_comfort_brake(2.5, 20.0, speed, True)
              for speed in (5.0, 4.0, 3.01, 3.0, 2.99, 2.0)]
    self.assertEqual(values[0], 2.5)
    self.assertTrue(all(left >= right for left, right in zip(values, values[1:])))
    self.assertLess(abs(values[2] - values[4]), 0.02)

  def test_both_t_follow_directions_are_rate_limited(self):
    self.assertAlmostEqual(limit_t_follow_change(1.5, 1.2, dt=0.05), 1.205)
    self.assertAlmostEqual(limit_t_follow_change(1.2, 1.44, dt=0.05), 1.425)

  def test_desired_distance_cannot_be_negative(self):
    self.assertEqual(clamp_desired_follow_distance(6.0, 180.0), 0.0)
    self.assertAlmostEqual(clamp_desired_follow_distance(45.0, 10.0), 35.0)




class TestStoppedLeadComfortBrake(unittest.TestCase):
  def ride(self, start_kph):
    latch, v, decels = StoppedLeadComfortBrake(), start_kph / 3.6, []
    while v > 5.0 / 3.6:
      cb = latch.update(2.5, v, 0.0, True)
      decels.append(v / (v / cb + 1.4))  # deceleration along the MPC gap envelope
      v -= decels[-1] * 0.05
    return decels

  def test_planned_decel_never_grows_while_slowing_for_a_stopped_lead(self):
    for start in (100, 70, 50):
      decels = self.ride(start)
      self.assertTrue(all(b <= a + 1e-6 for a, b in zip(decels, decels[1:])), start)

  def test_cap_is_held_but_released_when_lead_moves_or_is_lost(self):
    latch = StoppedLeadComfortBrake()
    capped = latch.update(2.5, 70.0 / 3.6, 0.0, True)
    self.assertLess(capped, 2.0)
    # Slow ego no longer qualifies on its own, but the approach keeps the cap.
    self.assertAlmostEqual(latch.update(2.5, 5.0, 0.0, True), capped)
    self.assertEqual(latch.update(2.5, 5.0, 6.0, True), 2.5)  # lead pulls away
    latch.update(2.5, 70.0 / 3.6, 0.0, True)
    self.assertEqual(latch.update(2.5, 5.0, 0.0, False), 2.5)  # lead lost

  def test_never_above_configured_value(self):
    latch = StoppedLeadComfortBrake()
    latch.update(2.5, 70.0 / 3.6, 0.0, True)
    self.assertLessEqual(latch.update(1.2, 5.0, 0.0, True), 1.2)


if __name__ == "__main__":
  unittest.main()
