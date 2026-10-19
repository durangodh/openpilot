import unittest

import numpy as np

from selfdrive.controls.lib.lane_path_validation import repair_lane_times, valid_lane_times


def lane_t(count=33):
  t = np.full(33, np.nan)
  t[:count] = np.linspace(0.0, 10.0, count)
  return t


class TestLaneTimeRepair(unittest.TestCase):
  def test_small_negative_first_sample_is_repaired(self):
    # 2026-10-19 lat trace: t[1] around -0.02 s rejected 67% of lane frames.
    for t1 in (-0.02, -0.165, 0.0):
      t = lane_t()
      t[1] = t1
      self.assertFalse(valid_lane_times(t))
      repaired = repair_lane_times(t)
      self.assertTrue(valid_lane_times(repaired))
      self.assertEqual(repaired[0], 0.0)
      np.testing.assert_array_equal(repaired[2:], t[2:])

  def test_valid_and_padded_times_unchanged(self):
    for t in (lane_t(), lane_t(20)):
      np.testing.assert_array_equal(repair_lane_times(t), t)
      self.assertTrue(valid_lane_times(repair_lane_times(t)))

  def test_large_or_far_backstep_stays_invalid(self):
    t = lane_t()
    t[1] = -0.5
    self.assertFalse(valid_lane_times(repair_lane_times(t)))
    t = lane_t()
    t[8] = t[7]
    self.assertFalse(valid_lane_times(repair_lane_times(t)))

  def test_holes_inf_and_bad_shape_stay_invalid(self):
    t = lane_t()
    t[10] = np.nan
    self.assertFalse(valid_lane_times(repair_lane_times(t)))
    t = lane_t()
    t[3] = np.inf
    self.assertFalse(valid_lane_times(repair_lane_times(t)))
    self.assertFalse(valid_lane_times(repair_lane_times(np.zeros(10))))
    self.assertFalse(valid_lane_times(repair_lane_times(None)))


if __name__ == "__main__":
  unittest.main()
