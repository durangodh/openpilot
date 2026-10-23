#!/usr/bin/env python3
import unittest

from selfdrive.controls.lib.longitudinal_limits import (A_CRUISE_MIN, A_CRUISE_MIN_LIMITER,
                                                        CRUISE_DECEL_MARGIN, get_cruise_min_accel)


class TestCruiseDecelLimit(unittest.TestCase):
  def test_no_limiter_keeps_cruise_bound(self):
    self.assertEqual(get_cruise_min_accel(0.0), A_CRUISE_MIN)

  def test_gentle_rate_never_weakens_cruise_bound(self):
    self.assertEqual(get_cruise_min_accel(0.5), A_CRUISE_MIN)

  def test_strong_rate_widens_bound_with_margin(self):
    self.assertAlmostEqual(get_cruise_min_accel(1.8), -(1.8 + CRUISE_DECEL_MARGIN))

  def test_bound_is_capped(self):
    self.assertEqual(get_cruise_min_accel(3.0), A_CRUISE_MIN_LIMITER)


if __name__ == "__main__":
  unittest.main()
