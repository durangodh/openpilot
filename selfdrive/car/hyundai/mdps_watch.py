"""Log why the MDPS silently drops our steering (diagnostics only).

On 2026-11-03 the MDPS stopped steering on a straight road with no alert: the
fault check only counts CF_Mdps_ToiUnavail (ToiFlt/FailStat could stay latched,
2026-10-31), so a refusal reported any other way went unnoticed.

MDPS faults are handled in carstate (ToiUnavail/ToiFlt/FailStat for 0.5 s ->
steerFaultTemporary, the 2026-10-30 behaviour). This only records:
  - not_steering: we request torque (LKAS active, not in the intentional
    high-angle cut) while moving, and MDPS12 CF_Mdps_ToiActive stays 0 for
    NOT_STEERING_S ("MDPS NOT STEERING" line). Nothing acts on it.
  - a log line on every change of our request or the MDPS12 status bits, with a
    snapshot (speed, angle, torques, blinkers), written to LOG_DIR and served at
    http://EON_IP:7714/trace ("MDPS 로그").
  - LKAS11 frames the panda safety rejected. The panda returns a rejected TX
    frame to the host (src = bus + 192, boardd CANPACKET_REJECTED); the interface
    parses LKAS11 there and passes the rejected torques to note_rejected(). On a
    rejection the panda drops the frame and resets its own last torque to 0
    (safety_hyundai_community.h), while we keep ramping from our last torque, so a
    torque/rate mismatch can keep every following frame rejected and the MDPS gets
    no LKAS11 at all. Each rejection streak is logged ("LKAS11 REJECTED" with the
    rejected torque, then "LKAS11 rejection end" with the count). Nothing acts on it.
    The rest of that line is reconstructed on the host, not read from the panda:
    pandaLast(est) and hostEstCheck replay the panda checks with our sent torques
    and the current driver torque (the panda uses its own driver samples), and
    hostMain is the host's MAIN state, not the panda's lat_allowed (which keeps
    lateral allowed for ~0.5 s of MAIN off). A rejection proves the panda dropped
    that frame, not why; no rejection line does not prove the MDPS is at fault.
    Bench check: selfdrive/debug/lkas11_reject_test.py.
"""
import os
import time

LOG_DIR = "/data/media/0/mdps_log"
# Display only: the phone HUD wheel turns yellow while this says the MDPS is faulted
# (also before carstate's 0.5 s debounce, and for an MDPS that silently does not steer). "1 <monotonic>" refreshed
# every FAULT_REFRESH_S while faulted, "0" when clear; remote_hud ignores a stale "1".
FAULT_FILE = "/dev/shm/mdps_fault"
FAULT_REFRESH_S = 0.5
FAULT_STATUS_BITS = ("CF_Mdps_ToiUnavail", "CF_Mdps_ToiFlt", "CF_Mdps_FailStat", "CF_Mdps_SErr")
NOT_STEERING_S = 0.5
# After LKAS starts requesting the MDPS takes a moment to report ToiActive.
START_GRACE_S = 1.0
MIN_SPEED_MS = 1.0
MDPS_BITS = ("CF_Mdps_ToiActive", "CF_Mdps_ToiUnavail", "CF_Mdps_ToiFlt", "CF_Mdps_FailStat",
             "CF_Mdps_SErr", "CF_Mdps_Def")
MAX_LINES_PER_DAY = 5000

# panda safety_hyundai.h limits (LKAS11 tx hook)
PANDA_MAX_STEER = 409
PANDA_MAX_RATE_UP = 3
PANDA_MAX_RATE_DOWN = 7
PANDA_DRIVER_ALLOWANCE = 50
PANDA_DRIVER_FACTOR = 2
PANDA_DRIVER_SCALE = 0.79   # panda scales MDPS12 driver torque: raw * 0.79 - 808
LKAS11_REJECTED_SRC = 192   # boardd CANPACKET_REJECTED + bus


def panda_rejection_check(torque, last, driver_torque):
  """Which panda LKAS11 check this torque fails against the panda's last torque, or None
  (then: the 250 ms real time limit, or lateral not allowed)."""
  if abs(torque) > PANDA_MAX_STEER:
    return "limit"
  driver = driver_torque * PANDA_DRIVER_SCALE
  driver_max = PANDA_MAX_STEER + (PANDA_DRIVER_ALLOWANCE + driver) * PANDA_DRIVER_FACTOR
  driver_min = -PANDA_MAX_STEER + (-PANDA_DRIVER_ALLOWANCE + driver) * PANDA_DRIVER_FACTOR
  highest = min(max(last, 0) + PANDA_MAX_RATE_UP, max(last - PANDA_MAX_RATE_DOWN, max(driver_max, 0)))
  lowest = max(min(last, 0) - PANDA_MAX_RATE_UP, min(last + PANDA_MAX_RATE_DOWN, min(driver_min, 0)))
  if torque > highest or torque < lowest:
    return "rate(last=%d allowed %d..%d)" % (last, lowest, highest)
  return None


class MdpsWatch:
  def __init__(self, dt, log_dir=LOG_DIR, fault_file=FAULT_FILE):
    self.dt = dt
    self.log_dir = log_dir
    self.fault_file = fault_file
    self.fault = False
    self.fault_written = None   # (state, monotonic) of the last write
    self.inactive_frames = 0
    self.request_frames = 0
    self.not_steering = False
    self.last_key = None
    self.lines = 0
    self.day = None
    self.sent_torque = 0          # torque of our last LKAS11
    self.panda_last = 0           # estimate of the panda's desired_torque_last
    self.rejected = []            # rejected LKAS11 torques since the last update()
    self.reject_streak = 0
    self.reject_total = 0
    self.reject_start = 0.0

  def note_rejected(self, torques):
    """LKAS11 torques the panda returned as rejected in this CAN update."""
    self.rejected.extend(int(t) for t in torques)

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

    self._rejections(out, apply_steer)

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
    # Faulted for the HUD: engaged and the MDPS reports a fault bit or does not steer.
    self._publish_fault(bool(lat_active) and (not_steering or any(bits[b] for b in FAULT_STATUS_BITS)))
    return not_steering

  def _publish_fault(self, fault):
    self.fault = fault
    now = time.monotonic()
    last = self.fault_written
    if last is not None and last[0] == fault and (not fault or now - last[1] < FAULT_REFRESH_S):
      return
    try:
      tmp = self.fault_file + ".tmp"
      with open(tmp, "w") as f:
        f.write("1 %.3f" % now if fault else "0")
      os.replace(tmp, self.fault_file)
      self.fault_written = (fault, now)
    except OSError:
      pass

  def _rejections(self, out, apply_steer):
    rejected, self.rejected = self.rejected, []
    if rejected:
      if self.reject_streak == 0:
        torque = rejected[0]
        check = panda_rejection_check(torque, self.panda_last, out.steeringTorque)
        main = getattr(out.cruiseState, "available", None) if hasattr(out, "cruiseState") else None
        # The rejected frame is the one we sent last cycle; its reference is the
        # panda's last torque before it (panda_last).
        self._log("LKAS11 REJECTED | torque=%d pandaLast(est)=%d driverTq=%.0f(panda %.0f) "
                  "hostMain=%s hostEstCheck=%s | v=%.1fkph angle=%.1f" % (
                    torque, self.panda_last, out.steeringTorque,
                    out.steeringTorque * PANDA_DRIVER_SCALE, main,
                    check or ("lat-not-allowed?(main off)" if main is False else "rt-250ms-or-lat_allowed?"),
                    out.vEgo * 3.6, out.steeringAngleDeg))
        self.reject_start = time.monotonic()
      self.reject_streak += len(rejected)
      self.reject_total += len(rejected)
      # The panda resets its last torque to 0 on every rejection.
      self.panda_last = 0
    else:
      if self.reject_streak:
        self._log("LKAS11 rejection end | frames=%d for %.2fs total=%d | next torque=%d" % (
          self.reject_streak, time.monotonic() - self.reject_start, self.reject_total, apply_steer))
        self.reject_streak = 0
      self.panda_last = self.sent_torque
    self.sent_torque = int(apply_steer)

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
