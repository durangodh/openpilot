import sys
import types
import unittest

# direct_remote imports compiled Params / cereal; the bridge logic under test needs neither.
for name, attrs in (('common.params', {'Params': object}), ('cereal.messaging', {'SubMaster': object})):
  if name not in sys.modules:
    try:
      __import__(name)
    except Exception:
      sys.modules[name] = types.SimpleNamespace(**attrs)
from selfdrive.eon_cluster.direct_remote import ALIVE_TIMEOUT_S, LEGACY_RENEW_S, RESTART_BACKOFF_S, BleBridge


class BleBridgeLifecycleTest(unittest.TestCase):
  def setUp(self):
    self.calls = []
    self.ok = True
    bridge = BleBridge.__new__(BleBridge)
    bridge.secret = 'a' * 32
    bridge.counters = {}
    bridge.node = None
    bridge.next_renew = 0.0
    bridge.running = False
    bridge.target = None
    bridge.last_alive = 0.0
    bridge.heartbeat_seen = True   # new APK unless a test says otherwise
    bridge.last_status = 0.0
    bridge.status = ''
    bridge.permission_granted = True
    bridge.am_error = ''
    bridge.service = 'pkg/Service'
    bridge._am = lambda *args: (self.calls.append(args), self.ok)[1]
    self.bridge = bridge

  def starts(self):
    return [c for c in self.calls if c[0] == 'startservice']

  def test_no_periodic_am_while_heartbeat_arrives(self):
    self.assertTrue(self.bridge.start(0., 'ble:AA:BB:CC:11:22:33'))
    self.assertEqual([c[0] for c in self.calls], ['stopservice', 'startservice'])
    for t in range(1, 120):
      self.bridge.last_alive = float(t)        # 5 s heartbeat or any report
      self.assertFalse(self.bridge.start(float(t), 'ble:AA:BB:CC:11:22:33'))
    self.assertEqual(len(self.starts()), 1)

  def test_restarts_with_new_key_only_after_heartbeat_stops(self):
    self.bridge.start(0., '')
    first_key = self.bridge.secret
    self.assertFalse(self.bridge.start(ALIVE_TIMEOUT_S - 0.1, ''))
    self.assertTrue(self.bridge.start(ALIVE_TIMEOUT_S + 0.1, ''))
    self.assertEqual(len(self.starts()), 2)
    self.assertNotEqual(self.bridge.secret, first_key)

  def test_new_r1_applies_immediately_with_same_key(self):
    self.bridge.start(0., '')
    key = self.bridge.secret
    self.bridge.next_renew = 0.0
    self.bridge.last_alive = 1.0
    self.assertTrue(self.bridge.start(1., 'ble:AA:BB:CC:11:22:33'))
    self.assertEqual(self.calls[-1][-1], 'AA:BB:CC:11:22:33')
    self.assertEqual(self.calls[-2][0], 'startservice')   # no stop for a live service
    self.assertEqual(self.bridge.secret, key)

  def test_failed_start_backs_off(self):
    self.ok = False
    self.assertFalse(self.bridge.start(0., ''))
    self.assertFalse(self.bridge.running)
    self.bridge.start(RESTART_BACKOFF_S - 0.1, '')
    self.assertEqual(len(self.starts()), 1)
    self.ok = True
    self.assertTrue(self.bridge.start(RESTART_BACKOFF_S, ''))

  def test_old_apk_without_heartbeat_keeps_the_12s_renewal(self):
    # An installed lease-based APK stops itself after 18 s without startservice.
    self.bridge.heartbeat_seen = False
    self.bridge.start(0., '')
    self.assertTrue(self.bridge.legacy)
    key = self.bridge.secret
    for t in range(1, 49):
      self.bridge.start(float(t), '')
    self.assertEqual(len(self.starts()), 1 + 48 // int(LEGACY_RENEW_S))
    self.assertEqual([c[0] for c in self.calls].count('stopservice'), 1)  # never drops the R1 link
    self.assertEqual(self.bridge.secret, key)
    self.bridge.heartbeat_seen = True   # new APK installed -> renewals stop
    n = len(self.starts())
    for t in range(49, 200):
      self.bridge.last_alive = float(t)
      self.bridge.start(float(t), '')
    self.assertEqual(len(self.starts()), n)


if __name__ == '__main__':
  unittest.main()
