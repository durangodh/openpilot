import ast
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

  def get(self, key, encoding=None):
    value = self.data.get(key)
    return value.decode(encoding) if value is not None and encoding else value

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


def test_damaged_journal_keeps_startup_alive_and_preserves_tuning(tmp_path):
  p = Params()
  before = p.data.copy()
  journal = tmp_path / 'restore-pending.json'
  journal.write_text('{broken')
  error = profiles.startup_recovery_error(p, tmp_path)
  assert error
  assert p.get(profiles.RECOVERY_ERROR_KEY).decode() == error
  assert {k: v for k, v in p.data.items() if k != profiles.RECOVERY_ERROR_KEY} == before
  assert journal.read_text() == '{broken'
  # Removing the damaged file alone must not enable potentially mixed tuning.
  journal.unlink()
  assert profiles.startup_recovery_error(p, tmp_path)


def test_complete_slot_repairs_damaged_journal_and_clears_error(tmp_path):
  p = Params()
  profiles.save_profile(p, 'a', tmp_path)
  p.put('CruiseMaxVals1', '150')
  (tmp_path / 'restore-pending.json').write_text('{broken')
  assert profiles.startup_recovery_error(p, tmp_path)
  profiles.restore_profile(p, 'a', tmp_path)
  assert p.get('CruiseMaxVals1') == b'110'
  assert p.get(profiles.RECOVERY_ERROR_KEY) is None
  assert profiles.startup_recovery_error(p, tmp_path) == ''


@pytest.mark.parametrize('damage', ['missing', 'invalid'])
def test_partial_slot_cannot_clear_recovery_block(tmp_path, damage):
  p = Params()
  profiles.save_profile(p, 'a', tmp_path)
  slot = tmp_path / 'profile-a.json'
  data = json.loads(slot.read_text())
  if damage == 'missing':
    data['settings'].pop('TFollowGap2')
  else:
    data['settings']['TFollowGap2']['value'] = '9999'
  slot.write_text(json.dumps(data))
  journal = tmp_path / 'restore-pending.json'
  journal.write_text('{broken')
  profiles.startup_recovery_error(p, tmp_path)
  before = p.data.copy()
  with pytest.raises(profiles.ProfileError):
    profiles.restore_profile(p, 'a', tmp_path)
  assert p.data == before
  assert journal.read_text() == '{broken'


def test_recovery_block_prevents_overwriting_good_slot(tmp_path):
  p = Params()
  profiles.save_profile(p, 'a', tmp_path)
  slot = tmp_path / 'profile-a.json'
  before = slot.read_bytes()
  p.put(profiles.RECOVERY_ERROR_KEY, 'pending repair')
  with pytest.raises(profiles.ProfileError):
    profiles.save_profile(p, 'a', tmp_path)
  assert slot.read_bytes() == before


def test_interrupted_repair_rolls_back_but_remains_blocked(tmp_path):
  p = Params()
  profiles.save_profile(p, 'a', tmp_path)
  p.put('CruiseMaxVals1', '150')
  (tmp_path / 'restore-pending.json').write_text(json.dumps({
    'format': profiles.FORMAT, 'version': 1, 'recoveryRequired': True,
    'previous': {'CruiseMaxVals1': '145'}}))
  assert profiles.startup_recovery_error(p, tmp_path)
  assert p.get('CruiseMaxVals1') == b'145'
  assert profiles.startup_recovery_error(p, tmp_path)  # blocked across further reboots
  profiles.restore_profile(p, 'a', tmp_path)
  assert profiles.startup_recovery_error(p, tmp_path) == ''
  assert p.get('CruiseMaxVals1') == b'110'


def test_repair_write_failure_does_not_clear_block(tmp_path):
  p = Params()
  profiles.save_profile(p, 'a', tmp_path)
  p.put('CruiseMaxVals1', '150')
  (tmp_path / 'restore-pending.json').write_text('{broken')
  profiles.startup_recovery_error(p, tmp_path)
  p.fail_key = 'CruiseMaxVals1'
  with pytest.raises(profiles.ProfileError):
    profiles.restore_profile(p, 'a', tmp_path)
  assert p.get('CruiseMaxVals1') == b'150'
  assert p.get(profiles.RECOVERY_ERROR_KEY)
  assert profiles.startup_recovery_error(p, tmp_path)


def test_error_return_blocks_boot_even_if_params_write_fails(tmp_path):
  p = Params()
  (tmp_path / 'restore-pending.json').write_text('{broken')
  p.fail_key = profiles.RECOVERY_ERROR_KEY
  assert profiles.startup_recovery_error(p, tmp_path)


def test_manager_boot_latches_control_block_until_reboot(tmp_path, monkeypatch):
  from types import SimpleNamespace as NS
  import os
  source = Path(__file__).resolve().parents[3] / 'manager' / 'manager.py'
  tree = ast.parse(source.read_text())
  init = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'manager_init')
  # Exercise the actual startup recovery call before unrelated registration /
  # hardware initialization, followed by the actual manager process deny list.
  stop = next(i for i, n in enumerate(init.body) if isinstance(n, ast.AnnAssign))
  init.body = init.body[:stop]
  thread = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'manager_thread')
  stop = next(i for i, n in enumerate(thread.body) if isinstance(n, ast.Expr) and
              isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name) and n.value.func.id == 'ensure_running')
  thread.body = thread.body[:stop + 1]
  p = Params()
  p.clear_all = lambda *args: None
  (tmp_path / 'restore-pending.json').write_text('{broken')
  startup = profiles.startup_recovery_error
  monkeypatch.setattr(profiles, 'startup_recovery_error', lambda params: startup(params, tmp_path))
  calls = []
  env = dict(Params=lambda: p, ParamKeyType=NS(CLEAR_ON_MANAGER_START=4),
             cloudlog=NS(bind=lambda **kw: None, info=lambda *a: None, error=lambda *a: None),
             set_time=lambda *a: None, os=os, EON=False, UNREGISTERED_DONGLE_ID='unregistered',
             Process=lambda **kw: NS(start=lambda: None), launcher=lambda *a: None,
             managed_processes={}, ensure_running=lambda *a, **kw: calls.append(kw['not_run']))
  exec(compile(ast.Module(body=[init, thread], type_ignores=[]), str(source), 'exec'), env)
  env['manager_init']()
  assert env['tuning_recovery_blocked']
  env['manager_thread']()
  assert 'controlsd' in calls[-1]
  assert 'ui' not in calls[-1] and 'remote_hud' not in calls[-1]
  p.remove(profiles.RECOVERY_ERROR_KEY)  # UI repaired: remain blocked this boot
  env['manager_thread']()
  assert 'controlsd' in calls[-1]
  (tmp_path / 'restore-pending.json').unlink()
  env['manager_init']()  # next boot after completed repair
  env['manager_thread']()
  assert 'controlsd' not in calls[-1]
