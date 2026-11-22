"""Optional corroboration for an existing APILOT standstill, never a stop target.

Phone candidates are NOT ego-signal confirmations. The controller separately
requires a fresh navigation red for ego's movement and an existing model stop.
"""
import json
import math
import os
from pathlib import Path
import re
import time

ENABLED_FILE = '/data/eon_signal_lab/stop_hold_enabled'
RESULT_FILE = '/dev/shm/eon_signal_lab.json'
MODEL_SHA = '458c8f1273ae407e6fa8334159a7f6dc29c88a2680b15eae2771105b0bdeffc2'


def setting(enabled=None, path=ENABLED_FILE):
  p = Path(path)
  if enabled is not None:
    if type(enabled) is not bool:
      raise ValueError('enabled must be boolean')
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + '.%d.tmp' % os.getpid())
    fd = os.open(str(tmp), os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as f:
      f.write('1' if enabled else '0')
    os.replace(str(tmp), str(p))
  try:
    with p.open(encoding='ascii') as f:
      return f.read(2) == '1'
  except (OSError, UnicodeError):
    return False


def phone_red_hint(path=RESULT_FILE, enabled_path=ENABLED_FILE, now=time.time):
  """Fail closed on missing/ambiguous/late data. No green/departure authority."""
  if not setting(path=enabled_path):
    return False
  try:
    with open(path, encoding='utf8') as f:
      raw = f.read(32769)
    if len(raw) > 32768:
      return False
    d = json.loads(raw)
    if (not isinstance(d, dict) or d.get('protocol') != 1 or
        d.get('model_sha256') != MODEL_SHA or d.get('observer_only') is not True or
        d.get('ego_signal_confirmed') is not False or
        not isinstance(d.get('session'), str) or re.fullmatch('[0-9a-f]{24}', d['session']) is None or
        type(d.get('frame_id')) is not int or d['frame_id'] <= 0):
      return False
    age_ms = (now() - float(d['received_unix_ms']) / 1000) * 1000
    roundtrip = d['roundtrip_ms']
    if (type(roundtrip) not in (int, float) or not math.isfinite(roundtrip) or
        not math.isfinite(age_ms) or age_ms < 0 or roundtrip < 0 or age_ms + roundtrip > 850):
      return False
    selected = d.get('selection')
    if (not isinstance(selected, dict) or selected.get('version') != 1 or
        selected.get('status') != 'stable' or selected.get('ego_signal_confirmed') is not False):
      return False
    ds = selected.get('detections')
    # Red+left is intentionally NOT accepted: the permitted movement may differ.
    if not isinstance(ds, list) or len(ds) != 1 or not isinstance(ds[0], dict):
      return False
    red = ds[0]
    score, box = red.get('score'), red.get('box')
    return (red.get('label') == 'red' and type(score) in (int, float) and
            math.isfinite(score) and .6 <= score <= 1 and isinstance(box, list) and len(box) == 4 and
            all(type(x) in (int, float) and math.isfinite(x) and 0 <= x <= 1 for x in box) and
            box[2] > box[0] and box[3] > box[1])
  except (OSError, ValueError, KeyError, TypeError, OverflowError):
    return False
