import time

import pytest

from selfdrive.controls.lib.conditional_e2e import ConditionalE2EController, signal_requires_stop
from selfdrive.controls.lib.navigation_route import NavigationRouteData
from selfdrive.controls.lib.tests.test_conditional_e2e import DT_MDL, update


def guidance(kind="none", direction=0, distance=-1.0):
  return {"fresh": kind != "none", "kind": kind, "direction": direction, "distance": distance}


def naver(state, guide="STRAIGHT", distance=90.0, remaining=20, blink=False, extra=()):
  signals = [{"guide": guide, "state": state, "remaining_sec": remaining}] + list(extra)
  return {"source": "NAVER", "distance_m": distance, "blink": blink, "signals": signals}


# ---- NavigationRouteData.signal_state ----

@pytest.mark.parametrize("state,phase", [
  ("RED", "red"), ("Red", "red"), ("stop-And-Remain", "red"),
  ("YELLOW", "yellow"), ("protected-clearance", "yellow"),
  ("GREEN", "green"), ("protected-Movement-Allowed", "green"), ("Dark", None),
])
def test_signal_phase_names(state, phase):
  result = NavigationRouteData.signal_state(naver(state), True, guidance())
  assert (result and result["phase"]) == phase


def test_signal_follows_the_movement_ego_will_make():
  sig = naver("GREEN", "STRAIGHT", extra=[{"guide": "LEFT", "state": "RED", "remaining_sec": 30}])
  assert NavigationRouteData.signal_state(sig, True, guidance())["phase"] == "green"
  # Left turn guidance at this intersection: the left-turn signal applies.
  assert NavigationRouteData.signal_state(sig, True, guidance("turn", -1, 85.0))["phase"] == "red"
  # A turn far beyond this signal does not change the movement here.
  assert NavigationRouteData.signal_state(sig, True, guidance("turn", -1, 400.0))["phase"] == "green"
  # Right turn: no signal stop is implied.
  assert NavigationRouteData.signal_state(sig, True, guidance("turn", 1, 90.0)) is None


@pytest.mark.parametrize("sig,fresh", [
  (naver("RED"), False),                       # stale
  (naver("RED", blink=True), True),            # flashing
  (naver("RED", distance=0.0), True),          # no distance
  ({"source": "KAKAO", "signals": []}, True),  # cleared
])
def test_unusable_signal_returns_none(sig, fresh):
  assert NavigationRouteData.signal_state(sig, fresh, guidance()) is None


# ---- signal_requires_stop ----

def test_red_requires_stop_unless_green_before_arrival():
  assert signal_requires_stop("red", 100.0, -1.0, 15.0)
  assert signal_requires_stop("red", 100.0, 5.0, 15.0)        # arrives in 6.7 s, still red-ish
  assert not signal_requires_stop("red", 100.0, 3.0, 15.0)    # green 3 s, arrival 6.7 s
  assert not signal_requires_stop("red", 250.0, -1.0, 15.0)   # too far
  assert not signal_requires_stop("green", 100.0, 20.0, 15.0)


def test_amber_only_when_it_cannot_be_cleared_but_can_be_stopped_for():
  assert signal_requires_stop("yellow", 80.0, 2.0, 15.0)      # 30 m in 2 s < 80 m
  assert not signal_requires_stop("yellow", 25.0, 2.0, 15.0)  # clears it
  assert not signal_requires_stop("yellow", 30.0, 1.0, 20.0)  # too close to stop at 3 m/s^2


# ---- ConditionalE2EController with signal assist ----

def weak_slowdown(controller, frames, **signal):
  # Model slows from 15 to 11 m/s: not enough for the model-only thresholds.
  mode = None
  for _ in range(frames):
    mode = update(controller, model_x=90.0, model_v0=15.0, model_v_end=11.0, v_ego=15.0, **signal)
  return mode


def test_weak_model_slowdown_alone_is_not_a_signal_stop():
  controller = ConditionalE2EController(DT_MDL)
  weak_slowdown(controller, 20)
  assert not controller.stopping


def test_red_signal_turns_weak_model_slowdown_into_a_stop():
  controller = ConditionalE2EController(DT_MDL)
  weak_slowdown(controller, 20, signal_phase="red", signal_distance=100.0, signal_remaining=30.0)
  assert controller.stopping


@pytest.mark.parametrize("signal", [
  dict(signal_phase="green", signal_distance=100.0, signal_remaining=30.0),
  dict(signal_phase="red", signal_distance=100.0, signal_remaining=1.0),   # green before arrival
  dict(signal_phase="red", signal_distance=40.0, signal_remaining=30.0),   # model stop beyond signal + 30 m
])
def test_no_assist_for_green_soon_green_or_mismatched_position(signal):
  controller = ConditionalE2EController(DT_MDL)
  weak_slowdown(controller, 20, **signal)
  assert not controller.stopping


def test_route_file_signal_is_parsed_and_expires(tmp_path):
  path = tmp_path / "guide.json"
  now_ms = time.time() * 1000.0
  path.write_text(__import__("json").dumps({
    "guidance_current": {"turn_type": 11, "distance_m": 300},
    "traffic_signal": naver("RED", distance=120.0, remaining=25),
    "stream_updated_at_ms": {"guidance_current": now_ms, "traffic_signal": now_ms},
  }))
  route = NavigationRouteData(str(path))
  assert route.update()["signal"] == {"phase": "red", "distance": 120.0, "remaining": 25.0}
