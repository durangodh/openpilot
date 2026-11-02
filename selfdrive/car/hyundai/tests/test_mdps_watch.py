"""MDPS silently not steering: warn after 0.5 s and log the cause (2026-11-03)."""
from types import SimpleNamespace

from selfdrive.car.hyundai.mdps_watch import MdpsWatch

DT = 0.01


def cs(toi_active=1, toi_flt=0, v=20.0, standstill=False):
  out = SimpleNamespace(standstill=standstill, vEgo=v, steeringAngleDeg=1.0, steeringTorque=0.0,
                        steeringTorqueEps=0.0, leftBlinker=False, rightBlinker=False,
                        steerFaultTemporary=False)
  return SimpleNamespace(out=out, mdps12={"CF_Mdps_ToiActive": toi_active, "CF_Mdps_ToiUnavail": 0,
                                          "CF_Mdps_ToiFlt": toi_flt, "CF_Mdps_FailStat": 0,
                                          "CF_Mdps_SErr": 0, "CF_Mdps_Def": 0})


def run(w, state, frames, lkas=True, cut=False):
  return [w.update(state, True, lkas, 50, cut) for _ in range(frames)][-1]


def test_mdps_not_steering_warns_and_logs(tmp_path):
  w = MdpsWatch(DT, str(tmp_path))
  assert not run(w, cs(), 200)                               # steering normally
  assert not run(w, cs(toi_active=0, toi_flt=1), 40)         # 0.4 s: not yet
  assert run(w, cs(toi_active=0, toi_flt=1), 20)             # 0.6 s: MDPS Fault warning
  assert not run(w, cs(), 1)                                 # MDPS takes torque again
  log = "".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir())
  assert "MDPS NOT STEERING" in log and "ToiFlt=1" in log and "ToiActive=0" in log


def test_no_warning_when_not_requesting_or_stopped_or_starting(tmp_path):
  w = MdpsWatch(DT, str(tmp_path))
  assert not run(w, cs(toi_active=0), 200, lkas=False)      # blinker / lat off
  assert not run(w, cs(toi_active=0), 200, cut=True)        # intentional angle cut
  assert not run(w, cs(toi_active=0, v=0.0, standstill=True), 200)
  w = MdpsWatch(DT, str(tmp_path))
  assert not run(w, cs(toi_active=0), 90)                   # just started requesting (grace)
