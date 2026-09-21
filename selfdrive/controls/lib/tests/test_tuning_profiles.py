import json
from pathlib import Path
import re

import pytest

from selfdrive.controls.lib import tuning_profiles as profiles


class Params:
  def __init__(self):
    self.data = {'IsOffroad': b'1', 'SelectedCar': b'GENESIS 2015-2016',
                 'CruiseMaxVals1': b'110', 'TFollowGap2': b'120',
                 'LateralTorqueFriction': b'80', 'OffsetTotal': b'-0.06',
                 'CalibrationParams': b'calibration', 'EonClusterHudNavApp': b'tmap'}
    self.keys = set(profiles.SPECS) | set(self.data)
    self.fail_key = None
    self.silent_failure = False
    self.write_count = 0
    self.onroad_at = None

  def all_keys(self):
    return [key.encode() for key in self.keys]

  def get(self, key):
    return self.data.get(key)

  def get_bool(self, key):
    return self.get(key) == b'1'

  def put(self, key, value):
    self.write_count += 1
    if self.write_count == self.onroad_at:
      self.data['IsOffroad'] = b'0'
    if key == self.fail_key:
      self.fail_key = None
      if self.silent_failure:
        return
      raise OSError('disk full')
    self.data[key] = value.encode() if isinstance(value, str) else value

  def remove(self, key):
    self.data.pop(key, None)


def test_roundtrip_isolated_slots_and_non_tuning_unchanged(tmp_path):
  p = Params()
  profiles.save_profile(p, 'a', tmp_path)
  p.put('CruiseMaxVals1', '150')
  profiles.save_profile(p, 'b', tmp_path)
  profiles.restore_profile(p, 'a', tmp_path)
  assert p.get('CruiseMaxVals1') == b'110'
  profiles.restore_profile(p, 'b', tmp_path)
  assert p.get('CruiseMaxVals1') == b'150'
  assert p.get('CalibrationParams') == b'calibration'
  assert p.get('EonClusterHudNavApp') == b'tmap'
  assert p.get('OffsetTotal') == b'-0.06'
  assert not (tmp_path / 'restore-pending.json').exists()


def test_missing_setting_restores_default_semantics(tmp_path):
  p = Params()
  profiles.save_profile(p, 'a', tmp_path)
  p.put('StartAccelApply', '50')
  profiles.restore_profile(p, 'a', tmp_path)
  assert p.get('StartAccelApply') is None


@pytest.mark.parametrize('action', [profiles.save_profile, profiles.restore_profile])
def test_onroad_blocks_writes(tmp_path, action):
  p = Params()
  p.data['IsOffroad'] = b'0'
  before = p.data.copy()
  with pytest.raises(profiles.ProfileError):
    action(p, 'a', tmp_path)
  assert p.data == before
  assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('slot', ['../a', '', 'c', 'A'])
def test_invalid_slot_rejected(tmp_path, slot):
  with pytest.raises(profiles.ProfileError):
    profiles.save_profile(Params(), slot, tmp_path)


@pytest.mark.parametrize('edit', [lambda d: d.update(version=2), lambda d: d.update(version=True),
                                 lambda d: d.update(vehicle='OTHER'), lambda d: d.update(slot='b'),
                                 lambda d: d.update(settings=[]), lambda d: d.update(format='other')])
def test_wrong_format_version_or_vehicle_never_writes_params(tmp_path, edit):
  p = Params()
  profiles.save_profile(p, 'a', tmp_path)
  path = tmp_path / 'profile-a.json'
  data = json.loads(path.read_text())
  edit(data)
  path.write_text(json.dumps(data))
  before = p.data.copy()
  with pytest.raises(profiles.ProfileError):
    profiles.restore_profile(p, 'a', tmp_path)
  assert p.data == before


@pytest.mark.parametrize('bad', ['nan', 'inf', '9999', '1.5', 110, True])
def test_invalid_values_skipped_while_valid_values_restore(tmp_path, bad):
  p = Params()
  profiles.save_profile(p, 'a', tmp_path)
  path = tmp_path / 'profile-a.json'
  data = json.loads(path.read_text())
  data['settings']['CruiseMaxVals1']['value'] = bad
  data['settings']['CalibrationParams'] = {'type': 'int', 'value': '0'}
  data['settings']['LateralTorqueFriction']['type'] = 'float'
  path.write_text(json.dumps(data))
  p.put('CruiseMaxVals1', '150')
  p.put('TFollowGap2', '180')
  result = profiles.restore_profile(p, 'a', tmp_path)
  assert result['skipped'] == 3
  assert p.get('CruiseMaxVals1') == b'150'
  assert p.get('TFollowGap2') == b'120'
  assert p.get('CalibrationParams') == b'calibration'


@pytest.mark.parametrize('silent', [False, True])
def test_failed_write_rolls_back_all_values(tmp_path, silent):
  p = Params()
  profiles.save_profile(p, 'a', tmp_path)
  p.put('StartAccelApply', '55')
  p.put('CruiseMaxVals1', '150')
  before = p.data.copy()
  p.fail_key = 'CruiseMaxVals1'
  p.silent_failure = silent
  with pytest.raises(profiles.ProfileError):
    profiles.restore_profile(p, 'a', tmp_path)
  assert p.data == before
  assert not (tmp_path / 'restore-pending.json').exists()


def test_ignition_during_restore_rolls_back(tmp_path):
  p = Params()
  profiles.save_profile(p, 'a', tmp_path)
  p.put('CruiseMaxVals1', '150')
  before = p.data.copy()
  p.onroad_at = p.write_count + 2
  with pytest.raises(profiles.ProfileError):
    profiles.restore_profile(p, 'a', tmp_path)
  before['IsOffroad'] = b'0'
  assert p.data == before


def test_startup_recovers_power_loss_journal(tmp_path):
  p = Params()
  p.put('CruiseMaxVals1', '150')
  (tmp_path / 'restore-pending.json').write_text(json.dumps({
    'format': profiles.FORMAT, 'version': 1, 'previous': {'CruiseMaxVals1': '110', 'StartAccelApply': None}}))
  p.data['IsOffroad'] = b'0'  # startup occurs before IsOffroad initialization
  assert profiles.recover_pending_restore(p, tmp_path)
  assert p.get('CruiseMaxVals1') == b'110'
  assert not profiles.recover_pending_restore(p, tmp_path)


def test_atomic_save_failure_preserves_previous_slot(tmp_path, monkeypatch):
  p = Params()
  profiles.save_profile(p, 'a', tmp_path)
  path = tmp_path / 'profile-a.json'
  previous = path.read_bytes()
  monkeypatch.setattr(profiles.os, 'replace', lambda *args: (_ for _ in ()).throw(OSError('full')))
  with pytest.raises(OSError):
    profiles.save_profile(p, 'a', tmp_path)
  assert path.read_bytes() == previous
  assert not list(tmp_path.glob('.profile-*'))


def test_unknown_param_from_another_branch_is_skipped(tmp_path):
  p = Params()
  profiles.save_profile(p, 'a', tmp_path)
  p.keys.remove('CruiseMaxVals1')
  assert profiles.restore_profile(p, 'a', tmp_path)['skipped'] == 1


def test_allowlist_matches_registered_c2_keys():
  root = Path(__file__).resolve().parents[4]
  registered = set(re.findall(r'\{"([^"]+)"\s*,', (root / 'selfdrive/common/params.cc').read_text()))
  assert not (set(profiles.SPECS) - registered)
  assert not (set(profiles.SPECS) & {'CalibrationParams', 'SelectedCar', 'ExperimentalMode', 'TrafficStopMode'})
