"""Watch the MDPS for silently dropping our steering, warn, and log why.

On 2026-11-03 the MDPS stopped steering on a straight road with no alert: the
fault check only counts CF_Mdps_ToiUnavail (ToiFlt/FailStat could stay latched,
2026-10-31), so a refusal reported any other way went unnoticed.

Two things here:
  - not_steering: we request torque (LKAS active, not in the intentional
    high-angle cut) while moving, and MDPS12 CF_Mdps_ToiActive stays 0 for
    NOT_STEERING_S. The interface shows the non-disengaging "MDPS Fault"
    warning for it; lateral stays requested so the MDPS can pick it up again.
  - a log line on every change of our request or the MDPS12 status bits, with a
    snapshot (speed, angle, torques, blinkers), written to LOG_DIR and served at
    http://EON_IP:7714/trace ("MDPS 로그").
"""
import os
import time

LOG_DIR = "/data/media/0/mdps_log"
NOT_STEERING_S = 0.5
# After LKAS starts requesting the MDPS takes a moment to report ToiActive.
START_GRACE_S = 1.0
MIN_SPEED_MS = 1.0
MDPS_BITS = ("CF_Mdps_ToiActive", "CF_Mdps_ToiUnavail", "CF_Mdps_ToiFlt", "CF_Mdps_FailStat",
             "CF_Mdps_SErr", "CF_Mdps_Def")
MAX_LINES_PER_DAY = 5000


class MdpsWatch:
  def __init__(self, dt, log_dir=LOG_DIR):
    self.dt = dt
    self.log_dir = log_dir
    self.inactive_frames = 0
    self.request_frames = 0
    self.not_steering = False
    self.last_key = None
    self.lines = 0
    self.day = None

  def update(self, CS, lat_active, lkas_active, apply_steer, cut_steer):
    mdps = getattr(CS, "mdps12", None) or {}
    bits = {name: int(mdps.get(name, 0) or 0) for name in MDPS_BITS}
    out = CS.out
    requesting = bool(lkas_active) and not cut_steer
    self.request_frames = self.request_frames + 1 if requesting else 0
    moving = not out.standstill and out.vEgo > MIN_SPEED_MS
    if requesting and moving and self.request_frames * self.dt > START_GRACE_S and \
       bits["CF_Mdps_ToiActive"] == 0:
      self.inactive_frames += 1
    else:
      self.inactive_frames = 0
    not_steering = self.inactive_frames * self.dt >= NOT_STEERING_S

    key = (bool(lat_active), bool(lkas_active), bool(cut_steer), not_steering, tuple(bits.values()))
    if key != self.last_key:
      reason = self._reason(lat_active, lkas_active, cut_steer, not_steering, bits)
      self._log("%s | latActive=%d lkas=%d cut=%d notSteering=%d %s | v=%.1fkph angle=%.1f "
                "driverTq=%.0f epsTq=%.2f apply=%d blinker=%d%d steerFaultTemp=%d" % (
                  reason, lat_active, lkas_active, cut_steer, not_steering,
                  " ".join("%s=%d" % (n.replace("CF_Mdps_", ""), v) for n, v in bits.items()),
                  out.vEgo * 3.6, out.steeringAngleDeg, out.steeringTorque, out.steeringTorqueEps,
                  apply_steer, out.leftBlinker, out.rightBlinker, out.steerFaultTemporary))
      self.last_key = key
    self.not_steering = not_steering
    return not_steering

  @staticmethod
  def _reason(lat_active, lkas_active, cut_steer, not_steering, bits):
    if not_steering:
      return "MDPS NOT STEERING"
    if bits["CF_Mdps_ToiUnavail"] or bits["CF_Mdps_ToiFlt"] or bits["CF_Mdps_FailStat"] or \
       bits["CF_Mdps_SErr"]:
      return "mdps-status"
    if lat_active and not lkas_active:
      return "lkas-off (blinker/turn)"
    if cut_steer:
      return "angle-cut"
    return "change"

  def _log(self, text):
    try:
      day = time.strftime("%Y%m%d")
      if day != self.day:
        self.day, self.lines = day, 0
      if self.lines >= MAX_LINES_PER_DAY:
        return
      self.lines += 1
      os.makedirs(self.log_dir, exist_ok=True)
      with open(os.path.join(self.log_dir, "mdps_%s.log" % day), "a", encoding="utf-8") as f:
        f.write("%s %s\n" % (time.strftime("%H:%M:%S"), text))
    except OSError:
      pass
