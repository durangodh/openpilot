"""Lead confirmation for the apilot-c2 longitudinal MPC."""
import math


NEW_LEAD_CONFIRM_S = 0.25          # a far, non-closing new lead must persist this long
NEW_LEAD_URGENT_TTC_S = 4.0        # closing faster than this is used at once
NEW_LEAD_URGENT_HEADWAY_S = 1.5    # closer than this headway is used at once
NEW_LEAD_URGENT_MIN_DIST = 25.0    # ... and always closer than this distance
LEAD_SWITCH_JUMP = 5.0             # m, dRel jump that means a different vehicle


class NoLead(object):
  status = False
  dRel = 0.0
  vLead = 0.0
  vLeadK = 0.0
  aLeadK = 0.0
  aLeadTau = 1.5
  modelProb = 0.0
  radar = False


NO_LEAD = NoLead()


class LeadConfirm(object):
  """Delay only far, non-closing new leads by NEW_LEAD_CONFIRM_S.

  radard publishes a lead from the first frame its probability passes 0.5,
  so a car in the next lane on a curve, or a cut-in that is already pulling
  away, was planned against immediately and produced a brake stab. Anything
  close or closing (TTC/headway/distance) is used at once, so a real
  hazard is never delayed. Only the MPC sees the delay.
  """

  def __init__(self):
    self.reset()

  def reset(self):
    self.seen_s = 0.0
    self.prev_d = None

  def update(self, lead, v_ego, dt):
    if lead is None or not lead.status:
      self.reset()
      return False
    d = float(lead.dRel)
    if not math.isfinite(d):
      self.reset()
      return True
    if self.prev_d is None or abs(d - self.prev_d) > max(LEAD_SWITCH_JUMP, 0.15 * self.prev_d):
      self.seen_s = 0.0
    self.prev_d = d
    self.seen_s += dt
    closing = max(0.0, float(v_ego) - float(lead.vLead))
    urgent = (d < max(NEW_LEAD_URGENT_MIN_DIST, NEW_LEAD_URGENT_HEADWAY_S * float(v_ego)) or
              (closing > 0.1 and d / closing < NEW_LEAD_URGENT_TTC_S))
    return urgent or self.seen_s >= NEW_LEAD_CONFIRM_S
