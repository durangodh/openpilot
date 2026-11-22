import json

from selfdrive.controls.lib.e2e_stop_log import E2EStopLog


def run(log, frames, **kw):
  base = dict(enabled=True, stopping=False, v_ego=15.0, brake_pressed=False, gas_pressed=False, model_valid=True,
              model_x=150.0, model_v_end=12.0, model_v0=15.0, model_y=0.0, lead_present=False, lead_distance=0.0)
  base.update(kw)
  for _ in range(frames):
    log.update(**base)


def rows(path):
  return [json.loads(l) for l in open(path)] if path.exists() else []


def test_missed_and_auto_stops(tmp_path):
  path = tmp_path / "log"
  log = E2EStopLog(0.05, str(path), clock=lambda: 1_700_000_000.0)
  run(log, 60, model_v_end=6.0)
  run(log, 1, brake_pressed=True)
  run(log, 40, brake_pressed=True, enabled=False, v_ego=0.0)
  r = rows(path)
  assert [x["kind"] for x in r] == ["MISSED"] and r[0]["mv_min3s"] == 6.0
  run(log, 5, stopping=True)
  assert [x["kind"] for x in rows(path)] == ["MISSED", "AUTO"]


def test_no_missed_behind_a_lead_or_when_resuming(tmp_path):
  path = tmp_path / "log"
  log = E2EStopLog(0.05, str(path))
  run(log, 10)
  run(log, 1, brake_pressed=True, lead_present=True, lead_distance=30.0)
  run(log, 10, v_ego=0.0, lead_present=True, lead_distance=8.0)
  run(log, 10)
  run(log, 1, brake_pressed=True)
  run(log, 10, v_ego=10.0, gas_pressed=True)
  run(log, 10, v_ego=0.0)
  run(log, 10)
  run(log, 1, brake_pressed=True)
  run(log, 10, v_ego=0.0, lead_present=True, lead_distance=6.0)
  assert rows(path) == []
