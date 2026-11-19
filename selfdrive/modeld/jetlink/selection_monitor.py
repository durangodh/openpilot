"""Observe source-selection decisions without changing native model publishers."""
import copy
import threading

import numpy as np

from selfdrive.modeld.jetlink.selection import ModelSelector


def native_observer():
  # Construct and access SubMaster on the monitor's own thread.
  from cereal import messaging
  sm = messaging.SubMaster(['modelV2', 'controlsState'])

  def read():
    sm.update(0)
    available = all(sm.alive[key] and sm.valid[key] for key in ('modelV2', 'controlsState'))
    frame = 0
    engaged = True  # Unknown engagement must not authorize automatic entry.
    if available:
      model, controls = sm['modelV2'], sm['controlsState']
      frame = int(model.frameId)
      engaged = bool(controls.enabled or controls.active)
      for name in ('position', 'velocity', 'acceleration', 'orientation', 'orientationRate'):
        points = getattr(model, name)
        for axis in ('x', 'y', 'z'):
          data = np.asarray(getattr(points, axis))
          available = available and data.shape == (33,) and bool(np.isfinite(data).all())
    return frame, available, engaged
  return read


class SelectionMonitor:
  """An independent 50 Hz observer; a blocked socket cannot stop its expiry tick.

  The benchmark always uses an unarmed selector with unvalidated camera/output/
  recovery inputs. Test injection is only for exercising the pure policy.
  """
  def __init__(self, observer_factory=native_observer, selector=None):
    self.selector = selector if selector is not None else ModelSelector()
    self.lock = threading.Lock()
    self.stop_event = threading.Event()
    self.thread = None
    self.observer_factory = observer_factory
    self.native_frame, self.native_available, self.engaged = 0, False, True
    self.observer_error = ''
    self.state = {}
    with self.lock:
      self._refresh()

  def _refresh(self):
    decision = self.selector.decide(self.native_frame, self.native_available, self.engaged)
    self.state = dict(self.selector.snapshot(decision), mode='shadow', applied=False,
                      native_frame=self.native_frame, native_available=self.native_available,
                      observer_error=self.observer_error)

  def start(self):
    if self.thread is not None:
      raise RuntimeError('model selection monitor already started')
    self.thread = threading.Thread(target=self._run, name='n16-selection-shadow', daemon=True)
    self.thread.start()

  def _run(self):
    try:
      read = self.observer_factory()
      while not self.stop_event.is_set():
        frame, available, engaged = read()
        with self.lock:
          self.native_frame, self.native_available, self.engaged = frame, available, engaged
          self._refresh()
        self.stop_event.wait(.02)
    except Exception as error:
      with self.lock:
        self.native_available = False
        self.observer_error = str(error)[:200]
        self.selector.disconnect('observer_failed')
        self._refresh()

  def connect(self):
    with self.lock:
      session = self.selector.connect()
      self._refresh()
      return session

  def begin(self, sequence, frame_id, started, calibrated):
    with self.lock:
      ticket = self.selector.begin(sequence, frame_id, started, True, calibrated)
      self._refresh()
      return ticket

  def accept(self, ticket, frame_id, prediction):
    with self.lock:
      accepted = self.selector.accept(ticket, frame_id, prediction)
      self._refresh()
      return accepted

  def fail(self, reason):
    with self.lock:
      if self.selector.connected or self.selector.reason != reason:
        self.selector.disconnect(reason)
      self._refresh()

  def snapshot(self):
    with self.lock:
      return copy.deepcopy(self.state)

  def close(self, reason='stopped'):
    self.stop_event.set()
    if self.thread is not None:
      self.thread.join(timeout=1)
    self.fail(reason)
