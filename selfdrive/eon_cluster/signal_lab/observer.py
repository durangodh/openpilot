# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fresh phone detections for display only; no navigation or control publication."""
import json
import math
import time


def read_observer(path='/dev/shm/eon_signal_lab.json', now=time.time):
  try:
    with open(path, encoding='utf-8') as f:
      raw = f.read(32769)
    if len(raw) > 32768:
      return None
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('observer_only') is not True or data.get('ego_signal_confirmed') is not False:
      return None
    age = now() - float(data.get('received_unix_ms', 0)) / 1000.0
    if not math.isfinite(age) or not 0 <= age <= 1.2:
      return None
    selection = data.get('selection')
    if not isinstance(selection, dict) or selection.get('version') != 1 or selection.get('ego_signal_confirmed') is not False:
      return None
    if selection.get('status') not in ('stable', 'warming', 'ambiguous', 'conflict', 'none'):
      return None
    ds = selection.get('detections') if selection['status'] == 'stable' else []
    if not isinstance(ds, list) or len(ds) > 32:
      return None
    clean = []
    for d in ds:
      if not isinstance(d, dict) or d.get('label') not in ('red', 'green', 'yellow', 'left'):
        return None
      confidence = d.get('score')
      if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        return None
      clean.append({'label': d['label'], 'score': confidence})
    return {'observerOnly': True, 'ageMs': int(age * 1000), 'detections': clean, 'selectionStatus': selection['status']}
  except (OSError, ValueError, TypeError, OverflowError):
    return None
