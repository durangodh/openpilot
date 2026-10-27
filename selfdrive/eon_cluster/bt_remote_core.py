"""Gestures, wake guard and link bookkeeping for one Bluetooth HID remote; no Android or vehicle dependencies."""
from selfdrive.eon_cluster.hud_remote import LINK_LOST, LINK_NONE, LINK_OK, LINK_SLEEP

LONG_S = .7
DOUBLE_S = .35
MAX_HOLD_S = 10.0
MAX_STALL_S = .4
# A classic HID remote that went to sleep reconnects on the next press and may
# deliver that press late. Presses within this window after the input node
# appears only wake the remote, so a delayed command never fires.
WAKE_IGNORE_S = 1.0
# A disconnect after this much idle time is the remote's own power saving.
# A disconnect soon after a press is reported as a real link loss.
SLEEP_IDLE_S = 60.0


def learning_allowed(offroad, alive=False, valid=False, can_valid=False, standstill=False, parked=False):
  """Engine-on learning requires fresh vehicle data and a stationary car in P."""
  return bool(offroad or (alive and valid and can_valid and standstill and parked))


def axis_direction(position, previous):
  """Joystick dead zone with separate enter and exit thresholds."""
  if abs(position) >= .55:
    return 1 if position > 0 else -1
  if abs(position) <= .25:
    return 0
  return previous


class Gestures:
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

  def key(self, token, value, now):
    """value 1 press / 0 release at kernel time now; emit gets the time the gesture completed."""
    if value == 1:
      self.down.setdefault(token, {'start': now, 'last': None, 'fired': False, 'blocked': False})
    elif value == 0:
      held = self.down.pop(token, None)
      if held is None or held['blocked'] or not 0 <= now - held['start'] <= MAX_HOLD_S or held['fired']:
        return
      self._flush(now)
      if now - held['start'] >= LONG_S and self.assigned(token, 'long'):
        self.pending.pop(token, None)
        self.emit(token, 'long', now)
      elif self.assigned(token, 'double'):
        first = self.pending.pop(token, None)
        if first is not None and 0 <= now - first <= DOUBLE_S:
          self.emit(token, 'double', now)
        else:
          self.pending[token] = now
      else:
        self.emit(token, 'short', now)

  def _flush(self, now):
    for token, released in list(self.pending.items()):
      if now < released or now - released >= DOUBLE_S:
        del self.pending[token]
        if 0 <= now - released <= MAX_STALL_S:
          self.emit(token, 'short', released)

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
      repeat = action in ('res', 'set')
      if held['fired'] and (not repeat or now - held['last'] < .5):
        continue
      if held['fired'] and now - held['last'] > .6:
        del self.down[token]
        continue
      held['fired'], held['last'] = True, now
      self.pending.pop(token, None)
      self.emit(token, 'long', now)


class LinkTracker:
  """Connection state and statistics of the learned remote, for the HUD and the log."""

  def __init__(self):
    self.connected = False
    self.ever_connected = False
    self.connected_at = None
    self.last_input = None
    self.last_drop = LINK_NONE
    self.connects = 0
    self.sleeps = 0
    self.losses = 0
    self.wake_drops = 0
    self.latencies = []

  def connect(self, now):
    """Returns a log line, or None if already connected."""
    if self.connected:
      return None
    self.connected, self.ever_connected = True, True
    self.connected_at = now
    self.connects += 1
    return 'connect #%d' % self.connects

  def disconnect(self, now):
    """Classify the drop by the time since the last press; returns a log line or None."""
    if not self.connected:
      return None
    self.connected = False
    since = now - (self.last_input if self.last_input is not None else self.connected_at)
    if since >= SLEEP_IDLE_S:
      self.sleeps += 1
      self.last_drop = LINK_SLEEP
      kind = 'sleep'
    else:
      self.losses += 1
      self.last_drop = LINK_LOST
      kind = 'LOST'
    return 'disconnect %s idle=%.1fs up=%.1fs' % (kind, since, now - self.connected_at)

  def guarded(self, stamp):
    """True for a press that only wakes the remote."""
    return self.connected_at is not None and stamp - self.connected_at < WAKE_IGNORE_S

  def input(self, now):
    self.last_input = now

  def wake_drop(self):
    self.wake_drops += 1

  def latency(self, ms):
    self.latencies = (self.latencies + [ms])[-200:]

  @property
  def link(self):
    if self.connected:
      return LINK_OK
    return self.last_drop if self.ever_connected else LINK_NONE

  def summary(self):
    text = '연결 %d회 / 절전 %d / 비정상 끊김 %d / 깨우기 입력 %d' % (
      self.connects, self.sleeps, self.losses, self.wake_drops)
    if self.latencies:
      text += ' / 지연 평균 %dms 최대 %dms' % (sum(self.latencies) / len(self.latencies), max(self.latencies))
    return text
