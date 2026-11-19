import json
import numpy as np
import pytest
from selfdrive.modeld.jetlink.prediction import PredictionState, TIME, DISTANCE, line_times
from selfdrive.modeld.jetlink.client import SPEC, LinkError


def output():
  values = np.zeros(18580)
  start, _ = SPEC['output_slices']['plan']
  plan = values[start:start + 495].reshape(33, 15)
  for i in range(15):
    plan[:, i] = i * 100 + np.arange(33)
  plan[:, 0] = TIME * 20
  values[start + 495:start + 990] = np.log(2)
  return values


def test_n16_plan_columns_do_not_use_legacy_five_hypothesis_offsets():
  value = output()
  converted = PredictionState().convert(value, 7)
  model = converted['model']
  for key, begin in (('position', 0), ('velocity', 3), ('acceleration', 6), ('orientation', 9), ('orientationRate', 12)):
    for column, axis in enumerate(('x', 'y', 'z')):
      expected = TIME * 20 if begin + column == 0 else (begin + column) * 100 + np.arange(33)
      assert np.allclose(model[key][axis], expected)
    assert model[key]['t'] == TIME.tolist()
  assert np.allclose(model['position']['yStd'], 2)
  assert converted['summary']['yaw_rate_2s'] == np.interp(2., TIME, np.arange(33) + 1400)
  assert 'direct_action' not in converted  # No direct torque/acceleration publisher.
  json.dumps(converted, allow_nan=False)


def test_three_lead_horizons_remain_distinct_and_std_is_exponentiated():
  value = output()
  start, _ = SPEC['output_slices']['lead']
  lead = value[start:start + 72].reshape(3, 6, 4)
  for i in range(3):
    lead[i, :, :] = np.arange(24).reshape(6, 4) + i * 100
  value[start + 72:start + 144] = np.log(3)
  model = PredictionState().convert(value, 1)['model']
  for i, item in enumerate(model['leadsV3']):
    assert item['x'] == (np.arange(6) * 4 + i * 100).tolist()
    assert item['a'] == (np.arange(6) * 4 + i * 100 + 3).tolist()
    assert np.allclose(item['vStd'], 3)
    assert item['probTime'] == i * 2


def test_lane_probabilities_use_second_logit_per_lane_and_keep_axis_sign():
  value = output()
  start, end = SPEC['output_slices']['lane_lines_prob']
  value[start:end] = [-2, 0, -2, 1, -2, 2, -2, 3]
  start, _ = SPEC['output_slices']['lane_lines']
  lanes = value[start:start + 264].reshape(4, 33, 2)
  for i in range(4): lanes[i, :, 0] = i - 2
  model = PredictionState().convert(value, 1)['model']
  assert model['laneLines'][0]['y'] == [-2.] * 33
  assert model['laneLines'][3]['y'] == [1.] * 33
  assert np.allclose(model['laneLineProbs'], 1 / (1 + np.exp(-np.arange(4))))


def test_meta_uses_six_value_stride_and_resets_brake_history_on_frame_gap():
  value = output()
  start, _ = SPEC['output_slices']['meta']
  value[start:start + 55] = 10
  state = PredictionState()
  for i in range(1, 6):
    model = state.convert(value, i)['model']
  assert model['meta']['hardBrakePredicted'] is True
  assert state.convert(value, 9)['model']['meta']['hardBrakePredicted'] is False
  for probabilities in model['meta']['disengagePredictions'].values():
    assert len(probabilities) == 5
  assert np.isclose(sum(model['meta']['desireState']), 1)
  assert np.allclose(np.array(model['meta']['desirePrediction']).reshape(4, 8).sum(axis=1), 1)


def test_stationary_and_short_plan_have_finite_monotonic_line_times():
  for x in (np.zeros(33), TIME * 2):
    plan = np.zeros((33, 15)); plan[:, 0] = x
    times = line_times(plan)
    assert np.isfinite(times).all() and np.all(np.diff(times) >= 0)
    assert times[0] == 0 and times[-1] == 10
  assert np.allclose(line_times(np.column_stack((TIME * 20, np.zeros((33, 14))))), DISTANCE / 20)


@pytest.mark.parametrize('bad', [np.zeros(18579), np.full(18580, np.nan), np.full(18580, np.inf)])
def test_invalid_n16_output_never_becomes_a_candidate(bad):
  with pytest.raises(LinkError): PredictionState().convert(bad, 1)


def test_candidate_encodes_in_actual_legacy_eon_schemas():
  pytest.importorskip('capnp')
  from cereal import log
  candidate = PredictionState().convert(output(), 1)
  model = log.ModelDataV2.new_message(**candidate['model'])
  assert len(model.position.x) == 33 and len(model.leadsV3) == 3
  assert len(model.meta.disengagePredictions.brakeDisengageProbs) == 5
  pose = log.CameraOdometry.new_message(**candidate['odometry'])
  assert len(pose.trans) == 3 and len(pose.rotStd) == 3


def test_web_candidate_download_rejects_output_from_a_different_run(monkeypatch, tmp_path):
  from selfdrive import eon_web_jetlink as web
  path = tmp_path / 'report.json'
  monkeypatch.setattr(web, 'REPORT', path)
  path.write_text(json.dumps(dict(started_at=123)))
  candidate = dict(source='camera', applied=False, started_at=122)
  path.with_suffix('.candidate.json').write_text(json.dumps(candidate))
  controller = web.JetlinkBench('/repo')
  assert 'output' in controller.candidate()
  candidate['started_at'] = 123
  path.with_suffix('.candidate.json').write_text(json.dumps(candidate))
  assert controller.candidate() == candidate
  candidate['applied'] = True
  path.with_suffix('.candidate.json').write_text(json.dumps(candidate))
  assert 'output' in controller.candidate()
