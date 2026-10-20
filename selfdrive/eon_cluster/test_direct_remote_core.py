import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('direct_remote_core', Path(__file__).with_name('direct_remote_core.py'))
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class GestureTest(unittest.TestCase):
  def setUp(self):
    self.events = []
    self.g = core.DirectRemoteGestures({}, lambda token, gesture: self.events.append((token, gesture)))

  def test_short_and_double_are_release_triggered(self):
    self.g.mapping = {'key:103@double': 'gap'}
    self.g.key('key:103', 1, 1.0)
    self.g.key('key:103', 0, 1.1)
    self.assertEqual(self.events, [])
    self.g.key('key:103', 1, 1.2)
    self.g.key('key:103', 0, 1.3)
    self.assertEqual(self.events, [('key:103', 'double')])
    self.g.key('key:103', 1, 2.0)
    self.g.key('key:103', 0, 2.1)
    self.g.tick(2.46)
    self.assertEqual(self.events[-1], ('key:103', 'short'))

  def test_long_repeat_and_stall_cancel(self):
    self.g.mapping = {'key:108@long': 'res'}
    self.g.key('key:108', 1, 1.0)
    self.g.tick(1.3)
    self.g.tick(1.71)
    self.g.tick(2.2)
    self.assertEqual(self.events, [])  # A stalled reader cannot fire an old hold.
    self.g.key('key:108', 0, 2.3)
    self.assertEqual(self.events, [])
    self.g.tick(3.0)
    self.g.key('key:108', 1, 3.0)
    for now in (3.1, 3.3, 3.5, 3.7, 3.9, 4.1, 4.2):
      self.g.tick(now)
    self.assertEqual(self.events, [('key:108', 'long'), ('key:108', 'long')])
    self.g.key('key:108', 0, 4.25)
    self.assertEqual(len(self.events), 2)

  def test_unmapped_and_disconnect_reset(self):
    self.g.key('hat:x:+', 1, 1.0)
    self.g.reset()
    self.g.key('hat:x:+', 0, 1.1)
    self.assertEqual(self.events, [])
    self.g.learn_gesture = 'long'
    self.g.key('key:115', 1, 2.0)
    self.g.tick(2.71)
    self.assertEqual(self.events, [('key:115', 'long')])

  def test_joystick_hysteresis(self):
    self.assertEqual(core.axis_direction(.54, 0), 0)
    self.assertEqual(core.axis_direction(.56, 0), 1)
    self.assertEqual(core.axis_direction(.35, 1), 1)
    self.assertEqual(core.axis_direction(.2, 1), 0)
    self.assertEqual(core.axis_direction(-.7, 1), -1)

  def test_held_lane_command_cannot_resume_after_gate_closes(self):
    self.g.mapping = {'hat:x:+@long': 'lane_right'}
    self.g.key('hat:x:+', 1, 1.0)
    self.g.tick(1.2)
    self.g.cancel_disallowed(set())
    self.g.tick(1.75)
    self.g.cancel_disallowed({'lane_right'})
    self.g.tick(1.85)
    self.g.key('hat:x:+', 0, 1.9)
    self.assertEqual(self.events, [])


if __name__ == '__main__':
  unittest.main()
