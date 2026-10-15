"""Require range evidence before predicting departure from a stopped lead.

Ported from carrot-wip longitudinal_stopping_lead.py (2026-10-15). While ego and
the radar lead are both stopped, radar speed/acceleration noise could make the
MPC predict the lead driving away and creep forward. Once the range has been
stable for 0.2 s the planner input copy uses vLead = aLead = 0 until the range
really opens by 0.15 m (three 5 cm range bins) for 0.1 s. Only the planner input
copy is changed; a closing range is always passed through.

This fork's radarState has no radar track id, so a range jump of more than
LEAD_CHANGE_DISTANCE is treated as a different lead.
"""
import math


STOPPED_EGO_SPEED = 0.10
STOPPED_LEAD_SPEED = 0.30
STABLE_RANGE_SPAN = 0.10
STABLE_TIME = 0.20
DEPARTURE_DISTANCE = 0.15
DEPARTURE_TIME = 0.10
LEAD_CHANGE_DISTANCE = 1.5


class _Evidence:
  def __init__(self, now, distance):
    self.since = now
    self.min_distance = distance
    self.max_distance = distance
    self.anchor_distance = distance
    self.held = False
    self.departure_since = None
    self.departed = False


class HeldLead:
  """Read-only view of a lead with zero planner speed and acceleration."""
  def __init__(self, lead):
    self._lead = lead

  vLead = vLeadK = aLeadK = 0.0

  def __getattr__(self, name):
    return getattr(self._lead, name)


class StoppingLeadFilter:
  def __init__(self):
    self.evidence = None
    self.now = 0.0

  def reset(self):
    self.evidence = None

  def held(self, lead, v_ego, dt):
    """Return True when the planner should treat this lead as still stopped."""
    self.now += max(0.0, float(dt))
    now = self.now
    try:
      values = (float(lead.dRel), float(lead.vRel), float(lead.vLead), float(lead.aLeadK), float(v_ego))
      valid = bool(lead.status) and bool(getattr(lead, 'radar', True)) and all(map(math.isfinite, values)) \
        and lead.dRel > 0.2
    except (AttributeError, TypeError, ValueError):
      valid = False
    if not valid:
      self.evidence = None
      return False
    d_rel = float(lead.dRel)
    evidence = self.evidence
    if evidence is not None and abs(d_rel - evidence.anchor_distance) > LEAD_CHANGE_DISTANCE:
      evidence = self.evidence = None

    # Explicit approaching/decelerating observations keep the original path.
    if lead.vLead < -STOPPED_LEAD_SPEED or lead.aLeadK < -0.5:
      self.evidence = None
      return False

    quiet = abs(v_ego) <= STOPPED_EGO_SPEED and abs(lead.vLead) <= STOPPED_LEAD_SPEED
    if evidence is None:
      if not quiet:
        return False
      evidence = self.evidence = _Evidence(now, d_rel)
    if evidence.departed:
      return False

    if not evidence.held:
      evidence.min_distance = min(evidence.min_distance, d_rel)
      evidence.max_distance = max(evidence.max_distance, d_rel)
      if not quiet or evidence.max_distance - evidence.min_distance > STABLE_RANGE_SPAN + 1e-6:
        self.evidence = None
        return False
      if now - evidence.since < STABLE_TIME - 1e-6:
        return False
      evidence.held = True
      evidence.anchor_distance = d_rel

    # A closing gap is never frozen: re-establish evidence at the closer range.
    if d_rel < evidence.anchor_distance - STABLE_RANGE_SPAN - 1e-6:
      self.evidence = None
      return False

    opening = d_rel - evidence.anchor_distance >= DEPARTURE_DISTANCE - 1e-6 and lead.vRel > 0.0 and lead.vLead > 0.0
    if opening:
      if evidence.departure_since is None:
        evidence.departure_since = now
      if now - evidence.departure_since >= DEPARTURE_TIME - 1e-6:
        evidence.departed = True
        return False
    else:
      evidence.departure_since = None
    return True
