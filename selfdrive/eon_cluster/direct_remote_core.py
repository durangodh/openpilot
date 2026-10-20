"""Gestures from one Bluetooth HID input device; no Android or vehicle dependencies."""

LONG_S = .7
DOUBLE_S = .35
MAX_HOLD_S = 10.0
MAX_STALL_S = .4


def axis_direction(position, previous):
  """Joystick dead zone with separate enter and exit thresholds."""
  if abs(position) >= .55:
    return 1 if position > 0 else -1
  if abs(position) <= .25:
    return 0
  return previous


class DirectRemoteGestures:
  def __init__(self, mapping, emit, learn_gesture=None):
    self.mapping = mapping
    self.emit = emit
    self.learn_gesture = learn_gesture
    self.down = {}
    self.pending = {}
    self.last_tick = None

  def reset(self):
    self.down.clear()
    self.pending.clear()
    self.last_tick = None

  def assigned(self, token, gesture):
    return self.learn_gesture == gesture or self.mapping.get(token + '@' + gesture, 'none') != 'none'

  def _emit(self, token, gesture):
    self.emit(token, gesture)

  def key(self, token, value, now):
    if value == 1:
      self.down.setdefault(token, {'start': now, 'last': None, 'fired': False, 'blocked': False})
    elif value == 0:
      held = self.down.pop(token, None)
      if held is None or held['blocked'] or not 0 <= now - held['start'] <= MAX_HOLD_S or held['fired']:
        return
      self._flush(now)
      if now - held['start'] >= LONG_S and self.assigned(token, 'long'):
        self.pending.pop(token, None)
        self._emit(token, 'long')
      elif self.assigned(token, 'double'):
        first = self.pending.pop(token, None)
        if first is not None and 0 <= now - first <= DOUBLE_S:
          self._emit(token, 'double')
        else:
          self.pending[token] = now
      else:
        self._emit(token, 'short')

  def _flush(self, now):
    for token, released in list(self.pending.items()):
      if now < released or now - released >= DOUBLE_S:
        del self.pending[token]
        if 0 <= now - released <= MAX_STALL_S:
          self._emit(token, 'short')

  def cancel_disallowed(self, allowed):
    """A held driving gesture cannot resume after a safety gate closes."""
    for token, held in self.down.items():
      actions = (self.mapping.get(token + '@' + gesture, 'none')
                 for gesture in ('short', 'long', 'double'))
      if any(action != 'none' and action not in allowed for action in actions):
        held['blocked'] = True
    for token in list(self.pending):
      action = self.mapping.get(token + '@short', 'none')
      if action != 'none' and action not in allowed:
        del self.pending[token]

  def tick(self, now):
    if self.last_tick is not None and not 0 <= now - self.last_tick <= MAX_STALL_S:
      self.reset()
    self.last_tick = now
    self._flush(now)
    for token, held in list(self.down.items()):
      age = now - held['start']
      if not 0 <= age <= MAX_HOLD_S:
        del self.down[token]
        continue
      if held['blocked'] or age < LONG_S or not self.assigned(token, 'long'):
        continue
      action = self.mapping.get(token + '@long', 'none')
      interval = .15 if action.startswith('lane_') else .5
      repeat = action in ('res', 'set', 'lane_left', 'lane_right')
      if held['fired'] and (not repeat or now - held['last'] < interval):
        continue
      if held['fired'] and now - held['last'] > .6:
        del self.down[token]
        continue
      held['fired'], held['last'] = True, now
      self.pending.pop(token, None)
      self._emit(token, 'long')
