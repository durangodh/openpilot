"""Korean traffic-light AI (EON road camera -> S20 phone -> signal_lab) for the signal stop.

2026-11-22: the earlier phone result was display-only (and a very narrow standstill veto) and
never helped real driving. Day-time red lights are often missed by the model-only stop, and the
navigation apps have no signal data at many junctions. A stable red (or amber) from the phone now
works like the navigation red in conditional_e2e:

  * weaker model slowing is accepted as a signal stop (the stop position is still the model's);
  * while stopped for the signal, a model-only launch waits as long as the red is seen.

Green departure is unchanged (the model's). Red together with a left arrow counts as red unless
the left blinker is on. Anything stale (> 1 s), unpaired, ambiguous or switched off is ignored,
so the stop then behaves exactly as before. Gas always wins.
"""
import json
import math
import os
import time
from pathlib import Path

ENABLED_FILE = '/data/eon_signal_lab/stop_hold_enabled'
RESULT_FILE = '/dev/shm/eon_signal_lab.json'
MAX_AGE_S = 1.0
MIN_SCORE = 0.5


def setting(enabled=None, path=ENABLED_FILE):
  """On unless switched off in EON Web (the file holds '0')."""
  p = Path(path)
  if enabled is not None:
    if type(enabled) is not bool:
      raise ValueError('enabled must be boolean')
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + '.%d.tmp' % os.getpid())
    with open(str(tmp), 'w') as f:
      f.write('1' if enabled else '0')
    os.replace(str(tmp), str(p))
  try:
    with p.open(encoding='ascii') as f:
      return f.read(2) != '0'
  except FileNotFoundError:
    return True
  except (OSError, UnicodeError):
    return False


def read_signal(path=RESULT_FILE, now=time.time):
  """'red', 'yellow', 'green', 'red_left' (red + left arrow), or None."""
  try:
    with open(path, encoding='utf8') as f:
      raw = f.read(32769)
    if len(raw) > 32768:
      return None
    d = json.loads(raw)
    age = now() - float(d['received_unix_ms']) / 1000.0
    if not math.isfinite(age) or not -0.5 <= age <= MAX_AGE_S:
      return None
    sel = d.get('selection')
    if not isinstance(sel, dict) or sel.get('status') != 'stable':
      return None
    labels = set()
    for det in sel.get('detections') or []:
      score = det.get('score')
      if type(score) in (int, float) and math.isfinite(score) and score >= MIN_SCORE:
        labels.add(det.get('label'))
    if 'red' in labels:
      return 'red_left' if 'left' in labels else 'red'
    if 'yellow' in labels:
      return 'yellow'
    if 'green' in labels or 'left' in labels:
      return 'green'
    return None
  except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
    return None


def stop_phase(signal, left_blinker):
  """The phase that matters for ego: red_left is red unless turning left."""
  if signal == 'red_left':
    return None if left_blinker else 'red'
  if signal in ('red', 'yellow'):
    return signal
  return None


class KoreanSignal:
  """Reads the phone result at most every 0.2 s (the phone sends about 5 per second)."""

  def __init__(self, path=RESULT_FILE, enabled_path=ENABLED_FILE, clock=time.monotonic):
    self.path, self.enabled_path, self.clock = path, enabled_path, clock
    self.next_read = 0.0
    self.next_setting = 0.0
    self.enabled = True
    self.signal = None

  def update(self):
    now = self.clock()
    if now >= self.next_setting:
      self.next_setting = now + 2.0
      self.enabled = setting(path=self.enabled_path)
    if not self.enabled:
      self.signal = None
    elif now >= self.next_read:
      self.next_read = now + 0.2
      self.signal = read_signal(self.path)
    return self.signal
