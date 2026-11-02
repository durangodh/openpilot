import unittest

from selfdrive.car.hyundai.steer_rejection_guard import SteerRejectionGuard


class TestSteerRejectionGuard(unittest.TestCase):
  def test_denial_latches_without_repeated_feedback(self):
    guard = SteerRejectionGuard()
    self.assertFalse(guard.update(True, []))
    self.assertTrue(guard.update(True, [80]))
    for _ in range(200):
      self.assertTrue(guard.update(True, []))

  def test_negative_torque_and_reengagement(self):
    guard = SteerRejectionGuard()
    self.assertTrue(guard.update(True, [-80]))
    self.assertFalse(guard.update(False, []))
    self.assertFalse(guard.update(True, []))
    self.assertTrue(guard.update(True, [-3]))

  def test_zero_denial_does_not_imply_torque_desynchronization(self):
    self.assertFalse(SteerRejectionGuard().update(True, [0, 0]))

  def test_disabled_feedback_does_not_latch(self):
    guard = SteerRejectionGuard()
    self.assertFalse(guard.update(False, [10]))
    self.assertFalse(guard.update(True, []))


if __name__ == '__main__':
  unittest.main()
