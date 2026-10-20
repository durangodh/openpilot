import hashlib
import hmac
import unittest

from selfdrive.eon_cluster.ble_r1 import R1Report, authenticated_packet, report_fields


class TestBleR1(unittest.TestCase):
  def test_observed_return_key_is_distinct_from_jog_down(self):
    decoder = R1Report()
    self.assertEqual(decoder.transitions(bytes.fromhex('0250')), [('blebtn:BACK', 1)])
    self.assertEqual(decoder.transitions(bytes.fromhex('0250')), [])
    self.assertEqual(decoder.transitions(bytes.fromhex('0050')), [('blebtn:BACK', 0)])
    self.assertEqual(decoder.transitions(bytes.fromhex('0050')), [])
    self.assertEqual(decoder.transitions(bytes.fromhex('02')), [('blejoy:down', 1)])
    self.assertEqual(decoder.transitions(bytes.fromhex('00')), [('blejoy:down', 0)])

  def test_observed_single_byte_directions_and_release(self):
    decoder = R1Report()
    self.assertEqual(decoder.transitions(b'\x00'), [])
    for raw, direction in (('01', 'up'), ('02', 'down'), ('10', 'left'), ('20', 'right')):
      report = bytes.fromhex(raw)
      token = 'blejoy:' + direction
      self.assertEqual(decoder.transitions(report), [(token, 1)])
      self.assertEqual(decoder.transitions(report), [])
      self.assertEqual(decoder.transitions(b'\x00'), [(token, 0)])
      self.assertEqual(decoder.transitions(b'\x00'), [])
    self.assertEqual(decoder.transitions(b'\x01'), [('blejoy:up', 1)])
    self.assertEqual(decoder.transitions(b'\x20'), [('blejoy:up', 0), ('blejoy:right', 1)])
    self.assertEqual(decoder.transitions(b'\x00'), [('blejoy:right', 0)])
    self.assertIsNone(report_fields(b'\x03'))
    self.assertIsNone(report_fields(b'\xff'))

  def test_observed_two_byte_jog_and_neutral(self):
    decoder = R1Report()
    self.assertEqual(decoder.transitions(bytes.fromhex('0050')), [])
    self.assertEqual(decoder.transitions(bytes.fromhex('0010')), [('blejoy:up', 1)])
    self.assertEqual(decoder.transitions(bytes.fromhex('0010')), [])
    self.assertEqual(decoder.transitions(bytes.fromhex('0050')), [('blejoy:up', 0)])
    self.assertEqual(decoder.transitions(bytes.fromhex('0050')), [])
    for raw in ('', '03', '1050', '0060', '001000'):
      self.assertIsNone(report_fields(bytes.fromhex(raw)))
      self.assertEqual(decoder.transitions(bytes.fromhex(raw)), [])

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
