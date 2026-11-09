"""Past the high angle the torque is released, never pulsed (MDPS fault logs 2026-11-09)."""
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'carcontroller.py'


def test_no_reset_pulse_is_started():
  text = SOURCE.read_text(encoding='utf-8')
  assert "self.cut_steer = True" not in text
  block = text[text.index("if self.angle_limit_counter > self.steer_fault_max_frames:"):]
  block = block[:block.index("elif")]
  assert "self.high_angle_release = True" in block


def test_release_holds_until_back_under_the_angle_in_the_same_frame():
  text = SOURCE.read_text(encoding='utf-8')
  release = text.index("if self.high_angle_release:")
  zero = text.index("if not lkas_active:\n      apply_steer = 0")
  assert release < zero
  assert "if abs(CS.out.steeringAngleDeg) < self.steer_fault_max_angle:\n        self.high_angle_release = False" in text
