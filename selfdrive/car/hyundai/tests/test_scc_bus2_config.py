#!/usr/bin/env python3
import unittest

from selfdrive.car import gen_empty_fingerprint
from selfdrive.car.hyundai.interface import resolve_scc_bus_and_long_control, use_scc_bus2
from selfdrive.car.hyundai.values import CAR


class TestSccBus2Config(unittest.TestCase):
  def test_genesis_existing_long_control_switch_selects_bus2(self):
    self.assertTrue(use_scc_bus2(CAR.GENESIS, False, True))

  def test_harness_forces_bus2_and_long_control_without_fingerprint(self):
    fingerprint = gen_empty_fingerprint()
    scc_bus, openpilot_long = resolve_scc_bus_and_long_control(fingerprint, True, False)
    self.assertEqual(scc_bus, 2)
    self.assertTrue(openpilot_long)

  def test_stock_scc_detection_remains_available(self):
    fingerprint = gen_empty_fingerprint()
    fingerprint[0][1056] = 8
    scc_bus, openpilot_long = resolve_scc_bus_and_long_control(fingerprint, False, False)
    self.assertEqual(scc_bus, 0)
    self.assertFalse(openpilot_long)

  def test_legacy_long_control_switch_is_preserved(self):
    fingerprint = gen_empty_fingerprint()
    fingerprint[2][1056] = 8
    scc_bus, openpilot_long = resolve_scc_bus_and_long_control(fingerprint, False, True)
    self.assertEqual(scc_bus, 2)
    self.assertTrue(openpilot_long)

  def test_mcan_on_bus1_is_never_taken_for_scc(self):
    # bus 1 is the MM-CAN tap; an SCC-looking ID there must not move the SCC bus
    fingerprint = gen_empty_fingerprint()
    fingerprint[1][1056] = 8
    scc_bus, _ = resolve_scc_bus_and_long_control(fingerprint, False, False)
    self.assertEqual(scc_bus, -1)


if __name__ == "__main__":
  unittest.main()
