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


def test_panda_rejection_check_mirrors_the_panda():
  from selfdrive.car.hyundai.mdps_watch import panda_rejection_check
  assert panda_rejection_check(410, 0, 0) == "limit"
  assert panda_rejection_check(3, 0, 0) is None                 # +3 from 0 is fine
  assert panda_rejection_check(80, 0, 0).startswith("rate")     # panda reset to 0, we kept 80
  assert panda_rejection_check(83, 80, 0) is None               # in sync
  assert panda_rejection_check(-80, 0, 0).startswith("rate")


def test_rejection_streak_is_logged_with_cause(tmp_path):
  w = MdpsWatch(DT, str(tmp_path))
  state = cs()
  state.out.cruiseState = SimpleNamespace(available=True)
  for t in range(0, 81, 3):                                     # in sync up to 78
    w.update(state, True, True, t, False)
  w.note_rejected([78, 78])                                     # panda rejected our 78 (bus 0 and 1)
  w.update(state, True, True, 81, False)
  for t in (84, 87):
    w.note_rejected([t - 3])                                    # keeps rejecting: panda reset to 0
    w.update(state, True, True, t, False)
  w.update(state, True, True, 90, False)                        # accepted again (no rejection reported)
  log = "".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir())
  assert "LKAS11 REJECTED | torque=78 pandaLast(est)=75" in log
  assert "main=True check=rt-250ms/lat_allowed" in log
  assert "LKAS11 rejection end | frames=4" in log and "total=4" in log
