"""Magicsee R1 game-mode BLE notification decoding and bridge authentication.

The report layout is documented by UbiquityRobotics/bt_joystick: 0x1b header,
button bitmap at byte 3, joystick direction at byte 4. No vehicle I/O here.
"""

import hashlib
import hmac
import re


BLE_PORT = 40983
MAC = re.compile(r'^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$')
BUTTON_BITS = {'B': 0, 'D': 1, 'C': 3, 'A': 4, 'OK': 6, 'BACK': 7}
JOYSTICK = {
  0x10: {'up'}, 0x20: {'up', 'right'}, 0x60: {'right'},
  0x00: {'up', 'left'}, 0x40: {'left'}, 0x90: {'down'},
  0x50: set(),
}

def report_fields(report):
  """Decode legacy frames and the two-byte states verified on the EON R1."""
  if len(report) >= 5 and report[0] == 0x1b:
    return report[3], report[4]
  # Other two-byte states stay visible as diagnostics until measured.
  # Do not assume that their button bits match the legacy layout.
  if report in (b'\x00\x10', b'\x00\x50'):
    return 0, report[1]
  return None


def authenticated_packet(raw, secret, counters):
  """Return (MAC or STATUS, payload) only for a fresh, authenticated frame."""
  if len(raw) > 512:
    return None
  try:
    device, count, payload, signature = raw.decode('ascii').split('|')
    counter = int(count)
    if counter <= counters.get(device, 0) or counter < 1:
      return None
    if device != 'STATUS' and not MAC.fullmatch(device):
      return None
    if len(payload) % 2 or len(payload) > 256 or not re.fullmatch(r'[0-9a-f]*', payload):
      return None
    if not re.fullmatch(r'[0-9a-f]{64}', signature):
      return None
    body = '|'.join((device, count, payload)).encode('ascii')
    expected = hmac.new(bytes.fromhex(secret), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
      return None
    counters[device] = counter
    return device, bytes.fromhex(payload)
  except (UnicodeDecodeError, ValueError):
    return None


class R1Report:
  def __init__(self):
    self.pressed = set()

  def reset(self):
    self.pressed.clear()

  def transitions(self, report):
    fields = report_fields(report)
    if fields is None:
      return []
    buttons, joystick = fields
    active = {'blebtn:' + name for name, bit in BUTTON_BITS.items() if buttons & (1 << bit)}
    active.update('blejoy:' + direction for direction in JOYSTICK.get(joystick, set()))
    changes = [(token, 0) for token in sorted(self.pressed - active)]
    changes.extend((token, 1) for token in sorted(active - self.pressed))
    self.pressed = active
    return changes
