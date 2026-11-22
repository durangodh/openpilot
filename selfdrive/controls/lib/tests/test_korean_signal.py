import json
import time

from selfdrive.controls.lib.conditional_e2e import ConditionalE2EController
from selfdrive.controls.lib.korean_signal import read_signal, setting, stop_phase
from selfdrive.controls.lib.tests.test_conditional_e2e import DT_MDL, update
from selfdrive.controls.lib.tests.test_signal_assist import weak_slowdown


def result(path, labels, status="stable", age=0.1, score=0.8):
  path.write_text(json.dumps({
    "received_unix_ms": (time.time() - age) * 1000,
    "selection": {"version": 1, "status": status,
                  "detections": [{"label": l, "score": score, "box": [.4, .1, .45, .14]} for l in labels]},
  }))
  return str(path)


def test_read_signal(tmp_path):
  p = tmp_path / "r.json"
  assert read_signal(result(p, ["red"])) == "red"
  assert read_signal(result(p, ["red", "left"])) == "red_left"
  assert read_signal(result(p, ["yellow"])) == "yellow"
  assert read_signal(result(p, ["green"])) == "green"
  assert read_signal(result(p, ["red"], status="warming")) is None
  assert read_signal(result(p, ["red"], age=1.5)) is None
  assert read_signal(result(p, ["red"], score=0.3)) is None
  assert read_signal(str(tmp_path / "missing")) is None


def test_stop_phase_and_setting(tmp_path):
  assert stop_phase("red_left", False) == "red" and stop_phase("red_left", True) == "green"
  assert stop_phase("green", False) == "green" and stop_phase("yellow", False) == "yellow"
  assert stop_phase(None, False) is None
  assert stop_phase("left", False) is None and stop_phase("left", True) == "green"
  f = str(tmp_path / "en")
  assert setting(path=f) is True              # on by default
  setting(False, path=f)
  assert setting(path=f) is False


def test_ai_red_turns_weak_model_slowdown_into_a_stop():
  c = ConditionalE2EController(DT_MDL)
  weak_slowdown(c, 20, vision_phase="red")
  assert c.stopping
  c = ConditionalE2EController(DT_MDL)
  weak_slowdown(c, 20, vision_phase=None)
  assert not c.stopping


def test_amber_only_when_it_can_be_stopped_for():
  c = ConditionalE2EController(DT_MDL)
  for _ in range(20):
    update(c, model_x=90.0, model_v0=15.0, model_v_end=11.0, v_ego=15.0, vision_phase="yellow")
  assert c.stopping
  c = ConditionalE2EController(DT_MDL)
  for _ in range(20):
    update(c, model_x=30.0, model_v0=20.0, model_v_end=15.0, v_ego=20.0, vision_phase="yellow")  # 67 m to stop
  assert not c.stopping


def stopped_for_signal():
  c = ConditionalE2EController(DT_MDL)
  update(c, model_x=50.0, model_v0=10.0, model_v_end=1.0, v_ego=10.0)
  for _ in range(5):
    update(c, model_x=10.0, model_v0=0.0, model_v_end=0.0, v_ego=0.0)
  assert c.stopping
  return c


def go(c, frames, **kw):
  for _ in range(frames):
    update(c, model_x=150.0, model_v0=0.0, model_v_end=12.0, v_ego=0.0, **kw)


def test_red_holds_a_model_only_launch_but_not_gas_or_green():
  c = stopped_for_signal()
  go(c, 20, vision_phase="red")
  assert c.stopping and not c.prepare
  go(c, 1, vision_phase="red", gas_pressed=True)
  assert c.prepare
  c = stopped_for_signal()
  go(c, 20, vision_phase=None)                 # green / nothing seen: the model's launch as before
  assert c.prepare


def wait(c, frames, **kw):
  for _ in range(frames):
    update(c, model_x=10.0, model_v0=0.0, model_v_end=0.0, v_ego=0.0, **kw)


def test_green_launches_when_the_model_still_waits():
  c = stopped_for_signal()
  wait(c, 20)
  assert c.stopping                               # model alone keeps waiting
  wait(c, 6, vision_phase="green")                # 0.3 s of green
  assert c.prepare and not c.stopping


def test_green_waits_for_a_car_right_ahead():
  c = stopped_for_signal()
  wait(c, 20, vision_phase="green", lead_present=True, radar_lead_present=True, radar_lead_distance=8.0)
  assert not c.prepare
