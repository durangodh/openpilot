import hashlib
import hmac
import unittest

from selfdrive.eon_cluster.ble_r1 import R1Report, authenticated_packet


class TestBleR1(unittest.TestCase):
  def test_report_edges_and_releases(self):
    decoder = R1Report()
    self.assertEqual(decoder.transitions(bytes.fromhex('1b00001050')), [('blebtn:A', 1)])
    self.assertEqual(decoder.transitions(bytes.fromhex('1b00001050')), [])
    self.assertEqual(decoder.transitions(bytes.fromhex('1b00000010')),
                     [('blebtn:A', 0), ('blejoy:up', 1)])
    self.assertEqual(decoder.transitions(bytes.fromhex('1b00000050')), [('blejoy:up', 0)])
    self.assertEqual(decoder.transitions(bytes.fromhex('0000000000')), [])

  def test_bridge_authentication_and_replay(self):
    secret = '01' * 16
    body = 'FF:FF:80:06:6C:59|1|1b00001050'
    tag = hmac.new(bytes.fromhex(secret), body.encode('ascii'), hashlib.sha256).hexdigest()
    raw = (body + '|' + tag).encode('ascii')
    counters = {}
    self.assertEqual(authenticated_packet(raw, secret, counters),
                     ('FF:FF:80:06:6C:59', bytes.fromhex('1b00001050')))
    self.assertIsNone(authenticated_packet(raw, secret, counters))
    self.assertIsNone(authenticated_packet(raw.replace(b'10', b'11', 1), secret, {}))


if __name__ == '__main__':
  unittest.main()
