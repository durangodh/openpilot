import unittest

import numpy as np

from selfdrive.controls.lib.dynamic_lane import (LANE_PROFILE_AUTO,
                                                 LANE_PROFILE_LESS,
                                                 LANE_PROFILE_ONLY,
                                                 select_lateral_path,
                                                 update_dynamic_lane_profile,
                                                 update_low_speed_laneless)


def update(profile, left_prob=0.4, right_prob=0.4, lane_change_active=False,
           lane_change_off=True, low_speed=False, buffer=True):
  return update_dynamic_lane_profile(
    profile, left_prob, right_prob, lane_change_active,
    lane_change_off, low_speed, buffer)


class TestDynamicLane(unittest.TestCase):
  def test_explicit_profiles_and_low_speed_override(self):
    self.assertEqual(update(LANE_PROFILE_ONLY, buffer=True), (False, False, False))
    self.assertEqual(update(LANE_PROFILE_LESS, buffer=False), (True, True, True))
    self.assertEqual(update(LANE_PROFILE_ONLY, low_speed=True), (True, False, False))

  def test_auto_requires_both_lane_lines_to_enter(self):
    self.assertEqual(update(LANE_PROFILE_AUTO, left_prob=0.8, right_prob=0.4),
                     (True, True, True))
    self.assertEqual(update(LANE_PROFILE_AUTO, left_prob=0.8, right_prob=0.8),
                     (False, False, False))

  def test_auto_hysteresis_requires_both_lane_lines_to_exit(self):
    self.assertEqual(update(LANE_PROFILE_AUTO, left_prob=0.2, right_prob=0.8, buffer=False),
                     (False, False, False))
    self.assertEqual(update(LANE_PROFILE_AUTO, left_prob=0.2, right_prob=0.2, buffer=False),
                     (True, True, True))

  def test_lane_change_forces_laneless_and_pre_change_holds_it(self):
    self.assertEqual(update(LANE_PROFILE_AUTO, left_prob=0.8, right_prob=0.8,
                            lane_change_active=True, lane_change_off=False,
                            buffer=False), (True, True, True))
    self.assertEqual(update(LANE_PROFILE_AUTO, lane_change_active=False,
                            lane_change_off=False, buffer=True), (True, True, True))

  def test_low_speed_status_does_not_force_profile_laneless(self):
    status_laneless, profile_laneless, buffer = update(
      LANE_PROFILE_AUTO, left_prob=0.8, right_prob=0.8, low_speed=True)
    self.assertTrue(status_laneless)
    self.assertFalse(profile_laneless)
    self.assertFalse(buffer)

  def test_low_speed_lane_profile_uses_laneplanner_blend(self):
    model_path = np.array([[0.0, 1.0], [1.0, 2.0]])
    lane_path = np.array([[0.0, 1.5], [1.0, 2.5]])
    status_laneless, profile_laneless, _ = update(
      LANE_PROFILE_ONLY, low_speed=True)

    self.assertTrue(status_laneless)
    self.assertFalse(profile_laneless)
    np.testing.assert_array_equal(
      select_lateral_path(model_path, lane_path, profile_laneless), lane_path)

    _, profile_laneless, _ = update(LANE_PROFILE_LESS, low_speed=True)
    np.testing.assert_array_equal(
      select_lateral_path(model_path, lane_path, profile_laneless), model_path)

  def test_low_speed_hysteresis(self):
    self.assertTrue(update_low_speed_laneless(17.9 / 3.6, True))
    self.assertFalse(update_low_speed_laneless(18.0 / 3.6, True))
    self.assertFalse(update_low_speed_laneless(14.0 / 3.6, False))
    self.assertTrue(update_low_speed_laneless(13.9 / 3.6, False))

  def test_selected_path_does_not_alias_either_candidate(self):
    model_path = np.array([[0.0, 1.0], [1.0, 2.0]])
    lane_path = np.array([[0.0, 10.0], [1.0, 20.0]])

    selected_model = select_lateral_path(model_path, lane_path, True)
    selected_model[0, 1] = 99.0
    self.assertEqual(model_path[0, 1], 1.0)
    self.assertEqual(lane_path[0, 1], 10.0)

    selected_lane = select_lateral_path(model_path, lane_path, False)
    selected_lane[0, 1] = 99.0
    self.assertEqual(model_path[0, 1], 1.0)
    self.assertEqual(lane_path[0, 1], 10.0)


if __name__ == "__main__":
  unittest.main()
