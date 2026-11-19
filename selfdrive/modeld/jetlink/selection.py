"""Model source selection policy; no messaging, CAN or actuator publisher.

A decision identifies an exact session/request/frame, never a reusable last
prediction. Integration currently observes this policy in the parked camera
probe only; production modeld continues publishing the native model.
"""
from collections import Counter, deque
from dataclasses import dataclass
import math
import time
import uuid

import numpy as np

from selfdrive.modeld.jetlink.prediction import TIME


@dataclass(frozen=True)
class Limits:
  response_s: float = .05
  age_s: float = .1
  recovery_frames: int = 20
  max_frame_lag: int = 1

  def __post_init__(self):
    if not all(math.isfinite(v) and v > 0 for v in (self.response_s, self.age_s)) or self.response_s > self.age_s:
      raise ValueError('invalid model timing limits')
    if type(self.recovery_frames) is not int or self.recovery_frames < 1 or type(self.max_frame_lag) is not int or not 0 <= self.max_frame_lag <= 1:
      raise ValueError('invalid model recovery limits')


@dataclass(frozen=True)
class Validation:
  camera: bool = False
  output: bool = False
  recovery: bool = False

  @property
  def complete(self):
    return self.camera is True and self.output is True and self.recovery is True


@dataclass(frozen=True)
class Ticket:
  session: str
  sequence: int
  frame_id: int
  started: float
  real_camera: bool
  calibrated: bool


@dataclass(frozen=True)
class Decision:
  source: str
  reason: str
  ticket: object = None
  disengage_required: bool = False


def valid_prediction(converted):
  """Validate legacy field widths/numbers, not driving quality or calibration."""
  def vector(value, n, probability=False, nonnegative=False):
    array = np.asarray(value)
    if array.shape != (n,) or array.dtype.kind not in 'fiu' or not np.isfinite(array).all():
      raise ValueError('shape/finite')
    if (probability or nonnegative) and np.any(array < 0):
      raise ValueError('negative')
    if probability and np.any(array > 1):
      raise ValueError('probability')
    return array

  def points(value, exact_time):
    for axis in ('x', 'y', 'z'):
      vector(value[axis], 33)
    times = vector(value['t'], 33, nonnegative=True)
    if np.any(np.diff(times) < 0) or np.any(times > 10.000001):
      raise ValueError('time order')
    if exact_time and not np.allclose(times, TIME, rtol=0, atol=1e-6):
      raise ValueError('time grid')

  try:
    model, pose = converted['model'], converted['odometry']
    for name in ('position', 'velocity', 'acceleration', 'orientation', 'orientationRate'):
      points(model[name], True)
    for axis in ('xStd', 'yStd', 'zStd'):
      vector(model['position'][axis], 33, nonnegative=True)
    for name, count in (('laneLines', 4), ('roadEdges', 2)):
      if len(model[name]) != count:
        return False
      for line in model[name]:
        points(line, False)
    vector(model['laneLineProbs'], 4, probability=True)
    vector(model['laneLineStds'], 4, nonnegative=True)
    vector(model['roadEdgeStds'], 2, nonnegative=True)
    if len(model['leadsV3']) != 3:
      return False
    for i, lead in enumerate(model['leadsV3']):
      if lead['probTime'] != i * 2:
        return False
      vector([lead['prob']], 1, probability=True)
      if not np.array_equal(vector(lead['t'], 6), np.arange(6) * 2):
        return False
      for name in ('x', 'y', 'v', 'a'):
        vector(lead[name], 6)
        vector(lead[name + 'Std'], 6, nonnegative=True)
    meta = model['meta']
    vector([meta['engagedProb']], 1, probability=True)
    if type(meta['hardBrakePredicted']) is not bool:
      return False
    desire = vector(meta['desireState'], 8, probability=True)
    future = vector(meta['desirePrediction'], 32, probability=True).reshape(4, 8)
    if not np.isclose(desire.sum(), 1., atol=1e-5) or not np.allclose(future.sum(axis=1), 1., atol=1e-5):
      return False
    probabilities = meta['disengagePredictions']
    if not np.array_equal(vector(probabilities['t'], 5), np.arange(1, 6) * 2):
      return False
    for name in ('gasDisengageProbs', 'brakeDisengageProbs', 'steerOverrideProbs',
                 'brake3MetersPerSecondSquaredProbs', 'brake4MetersPerSecondSquaredProbs', 'brake5MetersPerSecondSquaredProbs'):
      vector(probabilities[name], 5, probability=True)
    for name in ('trans', 'rot', 'transStd', 'rotStd'):
      vector(pose[name], 3, nonnegative=name.endswith('Std'))
  except (KeyError, TypeError, ValueError, OverflowError):
    return False
  return True


class ModelSelector:
  """One in-flight request, immediate invalidation, conservative re-entry.

  The owner must tick decide() independently of the blocking network thread.
  Decisions alone do not publish either model or request actual disengagement.
  """
  def __init__(self, limits=Limits(), clock=time.monotonic):
    self.limits, self.clock = limits, clock
    self.enabled, self.validation = False, Validation()
    self.session, self.connected = '', False
    self.pending = self.candidate = None
    self.last_sequence, self.last_frame, self.healthy = 0, None, 0
    self.last_requested_frame = None
    self.latch = False
    self.peak_healthy = 0
    self.reason, self.source = 'disconnected', 'native'
    self.counts = Counter()
    self.events = deque(maxlen=64)
    self._last_event = None

  def configure(self, enabled=False, validation=Validation()):
    if type(enabled) is not bool or not isinstance(validation, Validation):
      raise ValueError('invalid model selector configuration')
    self.enabled, self.validation = enabled, validation
    self._drop('configuration_changed')

  def connect(self):
    # A reconnect cannot reuse response IDs, recurrent health or a candidate.
    self.session = uuid.uuid4().hex
    self.connected = True
    self.pending = self.candidate = None
    self.last_sequence, self.last_frame, self.healthy = 0, None, 0
    self.last_requested_frame = None
    self.reason = 'warming_up'
    return self.session

  def _drop(self, reason):
    if self.source == 'external':
      self.latch = True
    self.pending = self.candidate = None
    self.last_frame, self.healthy = None, 0
    self.reason = reason
    self.counts[reason] += 1

  def disconnect(self, reason='disconnected'):
    self.connected = False
    self._drop(reason)

  def begin(self, sequence, frame_id, started, real_camera, calibrated):
    now = self.clock()
    if not self.connected or self.pending is not None:
      self._drop('overlapping_request' if self.pending else 'disconnected')
      return None
    if type(sequence) is not int or not self.last_sequence < sequence < 0xffffffff or type(frame_id) is not int or not 0 <= frame_id <= 0xffffffff:
      self._drop('request_order')
      return None
    self.last_sequence = sequence
    if self.last_requested_frame is not None:
      gap = (frame_id - self.last_requested_frame) & 0xffffffff
      if gap == 0 or gap >= 0x80000000:
        self._drop('request_order')
        return None
    if not math.isfinite(started) or started > now or now - started > self.limits.response_s:
      self._drop('stale_input')
      return None
    self.last_requested_frame = frame_id
    self.pending = Ticket(self.session, sequence, frame_id, started, real_camera is True, calibrated is True)
    return self.pending

  def accept(self, ticket, frame_id, converted):
    now = self.clock()
    # An old session or expired reply cannot clear a newer healthy candidate.
    if ticket is None or ticket.session != self.session or ticket != self.pending:
      self.counts['ignored_reply'] += 1
      return False
    self.pending = None
    if frame_id != ticket.frame_id:
      self._drop('frame_mismatch')
    elif not self.connected:
      self._drop('disconnected')
    elif now < ticket.started or now - ticket.started > self.limits.response_s:
      self._drop('late_response')
    elif not ticket.real_camera:
      self._drop('synthetic_input')
    elif not ticket.calibrated:
      self._drop('uncalibrated')
    elif not valid_prediction(converted):
      self._drop('invalid_output')
    else:
      if self.last_frame is not None and ((frame_id - self.last_frame) & 0xffffffff) != 1:
        self._drop('frame_gap')
      self.candidate = ticket
      self.last_frame = frame_id
      self.healthy += 1
      self.peak_healthy = max(self.peak_healthy, self.healthy)
      self.reason = 'healthy' if self.healthy >= self.limits.recovery_frames else 'warming_up'
      self.counts['accepted'] += 1
      return True
    return False

  def decide(self, native_frame, native_available, engaged=False):
    now = self.clock()
    native_available = native_available is True and type(native_frame) is int and 0 <= native_frame <= 0xffffffff
    if self.pending and (now < self.pending.started or now - self.pending.started > self.limits.response_s):
      self._drop('late_response')
    if self.candidate and (now < self.candidate.started or now - self.candidate.started > self.limits.age_s):
      self._drop('stale_result')
    if not engaged:
      self.latch = False
    reason = self.reason
    if not native_available:
      if self.pending is not None or self.candidate is not None or self.healthy:
        self._drop('native_unavailable')
      reason = 'native_unavailable'
    elif not self.connected:
      reason = self.reason
    elif self.candidate is None:
      reason = self.reason
    else:
      lag = (native_frame - self.candidate.frame_id) & 0xffffffff
      if lag >= 0x80000000:
        reason = 'waiting_native_frame'
      elif lag > self.limits.max_frame_lag:
        self._drop('frame_out_of_window')
        reason = self.reason
      elif self.healthy < self.limits.recovery_frames:
        reason = 'warming_up'
      elif not self.validation.complete:
        reason = 'validation_pending'
      elif not self.enabled:
        reason = 'disabled'
      elif self.latch:
        reason = 'recovery_latched'
      elif engaged and self.source != 'external':
        reason = 'wait_disengaged'
      else:
        reason = 'external_ready'
    source = 'none' if not native_available else 'external' if reason == 'external_ready' else 'native'
    if self.source == 'external' and source != 'external':
      self.latch = True
      self.counts['fallbacks'] += 1
    self.source = source
    decision = Decision(source, reason, self.candidate if source == 'external' else None, source == 'none')
    identity = (source, reason)
    if identity != self._last_event:
      self.events.append(dict(at_monotonic=now, source=source, reason=reason))
      self._last_event = identity
    return decision

  def snapshot(self, decision):
    return dict(source=decision.source, reason=decision.reason, disengage_required=decision.disengage_required,
                connected=self.connected, enabled=self.enabled, validation_complete=self.validation.complete,
                healthy_frames=self.healthy, peak_healthy_frames=self.peak_healthy, recovery_frames=self.limits.recovery_frames,
                recovery_latched=self.latch, counts=dict(self.counts), history=list(self.events),
                session=self.session, candidate_frame=None if self.candidate is None else self.candidate.frame_id,
                candidate_sequence=None if self.candidate is None else self.candidate.sequence,
                response_budget_ms=self.limits.response_s * 1000, maximum_age_ms=self.limits.age_s * 1000)
