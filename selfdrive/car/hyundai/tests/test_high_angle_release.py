"""aPilot high-angle steering request pulse behavior."""
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'carcontroller.py'


def test_two_frame_torque_fault_pulse_is_used():
  text = SOURCE.read_text(encoding='utf-8')
  assert "torque_fault = lkas_active and self.angle_limit_counter > self.max_angle_frames" in text
  assert "lkas_active = lkas_active and not torque_fault" in text
  assert "self.max_angle_frames + MAX_ANGLE_CONSECUTIVE_FRAMES" in text


def test_old_latched_release_path_is_removed():
  text = SOURCE.read_text(encoding='utf-8')
  assert "self.high_angle_release" not in text
  assert "self.cut_steer = True" not in text
