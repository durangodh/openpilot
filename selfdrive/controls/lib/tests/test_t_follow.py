import unittest

from selfdrive.controls.lib.t_follow import (
  StoppedLeadComfortBrake,
  T_FOLLOW_RELEASE_RATE,
  clamp_desired_follow_distance,
  get_stopped_lead_comfort_brake,
  release_t_follow,
)


class TestTFollow(unittest.TestCase):
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

  def test_t_follow_rises_at_once_and_is_released_gradually(self):
    self.assertAlmostEqual(release_t_follow(1.5, 1.2, 0.05), 1.5)
    self.assertAlmostEqual(release_t_follow(1.2, 1.44, 0.05), 1.44 - T_FOLLOW_RELEASE_RATE * 0.05)
    self.assertAlmostEqual(release_t_follow(1.2, None, 0.05), 1.2)
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

  def test_detection_does_not_start_behind_the_car(self):
    # 70 km/h, stopped car first seen 120 m ahead: the earlier envelope must
    # fit the real distance instead of demanding a sudden brake stab.
    v, d, tf, stop = 70.0 / 3.6, 120.0, 1.5, 6.0
    cb = StoppedLeadComfortBrake().update(2.5, v, 0.0, True, d, tf, stop)
    self.assertLessEqual(v * v / (2 * cb) + tf * v + stop, d + 1e-6)
    # Far enough away, the full early-braking cap is still used.
    far = StoppedLeadComfortBrake().update(2.5, v, 0.0, True, 250.0, tf, stop)
    self.assertAlmostEqual(far, get_stopped_lead_comfort_brake(2.5, v, 0.0, True))
    # Too close for any cap: never below the configured value's envelope.
    self.assertEqual(StoppedLeadComfortBrake().update(2.5, v, 0.0, True, 60.0, tf, stop), 2.5)


if __name__ == "__main__":
  unittest.main()
