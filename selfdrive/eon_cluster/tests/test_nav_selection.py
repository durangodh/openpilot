"""Exercise HUD requests without cereal or a running EON."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("nav_selection", Path(__file__).resolve().parents[1] / "nav_selection.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Params:
  def __init__(self):
    self.value = b"1"
    self.writes = 0
    self.fail = False

  def get(self, key):
    return self.value

  def put(self, key, value):
    if self.fail:
      raise OSError("storage unavailable")
    self.value = value.encode()
    self.writes += 1


class SelectionTest(unittest.TestCase):
  def setUp(self):
    self.params = Params()
    self.sync = module.NavSelectionSync(self.params)

  def send(self, app=2, request="a" * 32, session=None, port=7210):
    payload = f"HUDNAV1 {session or self.sync.session} {request} {app}".encode()
    return self.sync.receive(payload, ("192.168.1.2", port))

  def test_switch_both_directions(self):
    self.assertTrue(self.send())
    self.assertEqual(self.sync.telemetry()["hudNavApp"], 2)
    self.assertTrue(self.send(1, "b" * 32))
    self.assertEqual(self.sync.selected(), 1)

  def test_lost_ack_retry_does_not_overwrite_later_eon_choice(self):
    self.send()
    self.params.value = b"1"
    self.assertTrue(self.send())
    self.assertEqual(self.params.writes, 1)
    self.assertEqual(self.sync.selected(), 1)
    self.assertEqual(self.sync.telemetry()["hudNavRequestAck"], "a" * 32)

  def test_reject_invalid_or_previous_session(self):
    for kwargs in ({"app": 0}, {"app": 3}, {"request": "bad"},
                   {"session": "b" * 32}, {"port": 7211}):
      self.assertFalse(self.send(**kwargs))
    for payload in (b"HUD1", b"\xff", b"x" * 129):
      self.assertFalse(self.sync.receive(payload, ("192.168.1.2", 7210)))
    self.assertEqual(self.params.writes, 0)

  def test_failed_write_is_retried_without_ack(self):
    self.params.fail = True
    self.assertFalse(self.send())
    self.assertEqual(self.sync.ack, "")
    self.params.fail = False
    self.assertTrue(self.send())

  def test_request_id_cannot_change_its_value(self):
    self.send()
    self.assertFalse(self.send(1))
    self.assertEqual(self.sync.selected(), 2)

  def test_restart_requires_new_session(self):
    previous = self.sync.session
    self.sync = module.NavSelectionSync(self.params)
    self.assertFalse(self.send(session=previous))
    self.assertTrue(self.send())


if __name__ == "__main__":
  unittest.main()
