import subprocess
import unittest
from types import SimpleNamespace
from selfdrive.eon_cluster.bluetooth_policy import BluetoothPolicy, SERVICE


class PolicyLifecycleTest(unittest.TestCase):
  def setUp(self):
    self.calls = []
    self.result = SimpleNamespace(returncode=0, stdout=b'Starting service')
    def run(command, **options):
      self.calls.append((command, options))
      return self.result
    self.policy = BluetoothPolicy(run)

  def test_target_and_renewal(self):
    self.policy.renew(0., 'ble:AA:BB:CC:11:22:33')
    self.assertEqual(self.calls[0][0], ['/system/bin/am', 'startservice', '-n', SERVICE,
                                     '--es', 'target', 'ble:AA:BB:CC:11:22:33'])
    self.assertEqual(self.calls[0][1]['env']['LD_LIBRARY_PATH'], '')
    self.policy.renew(5.9, 'ble:AA:BB:CC:11:22:33')
    self.assertEqual(len(self.calls), 1)
    self.policy.renew(6., 'ble:AA:BB:CC:11:22:33')
    self.assertEqual(len(self.calls), 2)
    self.policy.renew(12., 'ble:AA:BB:CC:11:22:33')
    self.assertEqual(len(self.calls), 2)
    self.policy.renew(18., 'ble:AA:BB:CC:11:22:33')
    self.assertEqual(len(self.calls), 3)

  def test_new_selection_applies_immediately(self):
    self.policy.renew(0., 'ble:AA:BB:CC:11:22:33')
    self.policy.renew(.1, 'ble:AA:BB:CC:11:22:34')
    self.assertEqual(len(self.calls), 2)
    self.assertEqual(self.calls[-1][0][-1], 'ble:AA:BB:CC:11:22:34')

  def test_missing_package_error_is_visible_and_retried(self):
    self.result.stdout = b'Error: Not found; no service started.'
    self.policy.renew(0., '')
    self.assertFalse(self.policy.running)
    self.assertIn('Error', self.policy.error)
    self.result.stdout = b'Starting service'
    self.policy.renew(8., '')
    self.assertTrue(self.policy.running)
    self.assertEqual(self.policy.error, '')

  def test_disable_stops_service_and_reenable_is_immediate(self):
    self.policy.renew(0., '')
    self.policy.stop()
    self.assertEqual(self.calls[-1][0], ['/system/bin/am', 'stopservice', '-n', SERVICE])
    self.policy.stop()
    self.assertEqual(len(self.calls), 2)
    self.policy.renew(.1, '')
    self.assertTrue(self.policy.running)
    self.assertEqual(len(self.calls), 3)

  def test_timeout_does_not_escape_into_control_process(self):
    def timeout(*args, **kwargs):
      raise subprocess.TimeoutExpired('am', 8)
    policy = BluetoothPolicy(timeout)
    policy.renew(0., '')
    self.assertFalse(policy.running)
    self.assertTrue(policy.error)


if __name__ == '__main__':
  unittest.main()
