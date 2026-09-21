"""C2 tuning profiles. Explicit keys only; no calibration, identity or HUD data."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import tempfile


PROFILE_ROOT = Path('/data/tuning_profiles')
FORMAT = 'g-remote-tuning'
VERSION = 1
MAX_BYTES = 128 * 1024
# Stored units match Params/UI, not physical units. Keep this list deliberately
# limited to longitudinal, lateral and curve tuning, including enable switches.
INT_RANGES = {
  'StartAccelApply': (0, 100), 'StopAccelApply': (0, 100),
  'JerkStartLimit': (5, 50), 'StoppingDecelRate': (20, 200),
  'PidJerkAccel': (30, 300), 'PidJerkDecel': (30, 300),
  'LowSpeedJerkBoost': (100, 500), 'StandstillHoldApply': (10, 100),
  'StandstillReleaseSpeed': (2, 20), 'StandstillReleaseMs': (50, 2000),
  'NoLeadCruiseAccelFactor': (30, 100), 'NoLeadCruiseJerkLimit': (5, 100),
  'TFollowSpeedRatio': (100, 300), 'LeadDepartCost': (5, 100),
  'MySafeModeFactor': (50, 100), 'MyEcoModeFactor': (10, 95),
  'LongTuningKpV': (0, 200), 'LongTuningKiV': (0, 2000), 'LongTuningKf': (0, 200),
  'LongitudinalActuatorDelayLowerBound': (0, 100),
  'LongitudinalActuatorDelayUpperBound': (0, 100), 'LongActuatorDelay': (0, 100),
  'ComfortBrake': (150, 400), 'XEgoObstacleCost': (100, 1200), 'StopDistance': (200, 1000),
  'CustomSteerRatio': (1000, 2000), 'SteerActuatorDelay': (0, 80),
  'MpcPathCost': (100, 5000), 'MpcLateralMotionCost': (0, 1000),
  'MpcLateralAccelCost': (0, 1000), 'MpcLateralJerkCost': (0, 500),
  'SteeringRateCost': (200, 1200), 'LateralTorqueAccelFactor': (500, 4500),
  'LateralTorqueFriction': (0, 200), 'LateralTorqueKpV': (0, 500),
  'LateralTorqueKiV': (0, 200), 'LateralTorqueKf': (0, 200), 'LateralTorqueKd': (0, 200),
  'LatAccelFrictionFactor': (0, 300), 'LatJerkFrictionFactor': (0, 200),
  'LatLowSpeedCurvTauMs': (0, 1000), 'LatMpcInputOffset': (0, 20),
  'AdjustLaneOffset': (0, 40), 'LanelessOffset': (-30, 30), 'DynamicLaneProfile': (0, 2),
  'AutoCurveSpeedFactor': (50, 300), 'AutoCurveSpeedLowerLimit': (5, 80),
  'AutoCurveSpeedDecelRate': (0, 300), 'MapTurnSpeedFactor': (50, 150),
}
INT_RANGES.update({'CruiseMaxVals' + suffix: (10, 250) for suffix in ('1', '20', '2', '3', '4', '5', '6')})
INT_RANGES.update({'TFollowGap' + str(i): (70, 300) for i in range(1, 5)})
BOOL_KEYS = {'ApplyLongDynamicCost', 'LateralTorqueCustom', 'UseLiveSteerRatio', 'TurnVisionControl'}
SPECS = {key: ('int', limits) for key, limits in INT_RANGES.items()}
SPECS.update({key: ('bool', (0, 1)) for key in BOOL_KEYS})
SPECS['OffsetTotal'] = ('float', (-1.0, 1.0))


class ProfileError(Exception):
  pass


def _text(value):
  return value.decode('utf-8') if isinstance(value, bytes) else value


def _known(params, key):
  # C2 raises UnknownKeyName (not KeyError) from check_key. all_keys also works
  # across older branches without importing the native Params module in tests.
  return key in {_text(value) for value in params.all_keys()}


def _value(key, value):
  if value is None:  # missing Params uses the controller's default
    return None
  value = _text(value)
  kind, (lower, upper) = SPECS[key]
  if not isinstance(value, str) or len(value) > 64:
    raise ValueError('invalid value')
  if kind == 'bool' and value not in ('0', '1'):
    raise ValueError('invalid boolean')
  if kind == 'int' and re.fullmatch(r'-?\d+', value) is None:
    raise ValueError('invalid integer')
  number = float(value)
  if not math.isfinite(number) or not lower <= number <= upper:
    raise ValueError('out of range')
  return value


def _offroad(params):
  if not params.get_bool('IsOffroad'):
    raise ProfileError('차량 전원을 끈 상태에서만 설정을 저장·복원할 수 있습니다.')


def _slot_path(root, slot):
  if slot not in ('a', 'b'):
    raise ProfileError('프로필은 A 또는 B만 사용할 수 있습니다.')
  return root / ('profile-' + slot + '.json')


@contextmanager
def _lock(root):
  root.mkdir(parents=True, exist_ok=True)
  with (root / '.lock').open('a') as lock:
    try:
      fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
      raise ProfileError('다른 설정 저장·복원이 진행 중입니다.')
    try:
      yield
    finally:
      fcntl.flock(lock, fcntl.LOCK_UN)


def _sync_directory(root):
  fd = os.open(str(root), os.O_RDONLY)
  try:
    os.fsync(fd)
  finally:
    os.close(fd)


def _atomic_json(path, data):
  # fsync + rename prevents a power loss from truncating a previously saved slot.
  fd, temporary = tempfile.mkstemp(prefix='.profile-', dir=str(path.parent))
  try:
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
      json.dump(data, out, ensure_ascii=False, allow_nan=False, indent=2)
      out.flush()
      os.fsync(out.fileno())
    os.replace(temporary, str(path))
    _sync_directory(path.parent)
  finally:
    if os.path.exists(temporary):
      os.unlink(temporary)


def _read_json(path):
  try:
    with path.open('rb') as source:
      raw = source.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
      raise ValueError('oversized profile')
    payload = json.loads(raw.decode('utf-8'))
    if not isinstance(payload, dict):
      raise ValueError('invalid profile')
    return payload
  except (OSError, ValueError, UnicodeError) as error:
    raise ProfileError('저장된 설정이 없거나 파일을 읽을 수 없습니다.') from error


def _write_param(params, key, value):
  if value is None:
    params.remove(key)
  else:
    params.put(key, value)
  # C2's Params wrapper discards the C++ return code. Read back to detect ENOSPC
  # or another failed write instead of reporting a successful partial restore.
  if _text(params.get(key)) != value:
    raise ProfileError('설정 저장을 확인할 수 없습니다: ' + key)


def _recover_locked(params, root):
  journal = root / 'restore-pending.json'
  if not journal.exists():
    return False
  data = _read_json(journal)
  values = data.get('previous')
  if data.get('format') != FORMAT or data.get('version') != VERSION or not isinstance(values, dict):
    raise ProfileError('중단된 설정 복원 기록을 확인할 수 없습니다.')
  # Recovery retains exact previous bytes, even old values outside the current
  # UI range. Still restrict writes to known tuning keys and bounded strings.
  if not values or any(key not in SPECS or not _known(params, key) or
                       (value is not None and (not isinstance(value, str) or len(value) > 64))
                       for key, value in values.items()):
    raise ProfileError('중단된 설정 복원 기록이 현재 버전과 호환되지 않습니다.')
  for key, value in values.items():
    _write_param(params, key, value)
  journal.unlink()
  _sync_directory(root)
  return True


def recover_pending_restore(params, root=PROFILE_ROOT):
  """Manager startup only, before control processes start or defaults are filled."""
  root = Path(root)
  if not (root / 'restore-pending.json').exists():
    return False
  with _lock(root):
    return _recover_locked(params, root)


def save_profile(params, slot, root=PROFILE_ROOT):
  root = Path(root)
  path = _slot_path(root, slot)
  _offroad(params)
  with _lock(root):
    _recover_locked(params, root)
    values = {}
    skipped = 0
    for key, (kind, _) in SPECS.items():
      if not _known(params, key):
        continue
      try:
        values[key] = {'type': kind, 'value': _value(key, params.get(key))}
      except (ValueError, UnicodeError):
        skipped += 1
    if not values:
      raise ProfileError('저장할 수 있는 튜닝값이 없습니다.')
    _offroad(params)
    _atomic_json(path, {'format': FORMAT, 'version': VERSION, 'slot': slot,
                        'createdAt': datetime.now(timezone.utc).isoformat(),
                        'vehicle': _text(params.get('SelectedCar')),
                        'settings': values})
    return {'count': len(values), 'skipped': skipped}


def restore_profile(params, slot, root=PROFILE_ROOT):
  root = Path(root)
  path = _slot_path(root, slot)
  _offroad(params)
  with _lock(root):
    _recover_locked(params, root)
    data = _read_json(path)
    if (data.get('format') != FORMAT or type(data.get('version')) is not int or
        data['version'] != VERSION or data.get('slot') != slot or not isinstance(data.get('settings'), dict)):
      raise ProfileError('현재 버전과 호환되지 않는 설정 파일입니다.')
    if data.get('vehicle') != _text(params.get('SelectedCar')):
      raise ProfileError('저장 당시 선택한 차량과 현재 차량이 다릅니다.')
    values, skipped = {}, 0
    for key, entry in data['settings'].items():
      try:
        if (key not in SPECS or not _known(params, key) or not isinstance(entry, dict) or
            entry.get('type') != SPECS[key][0] or 'value' not in entry):
          raise ValueError('incompatible key')
        values[key] = _value(key, entry['value'])
      except (ValueError, TypeError, UnicodeError):
        skipped += 1
    if not values:
      raise ProfileError('복원할 수 있는 튜닝값이 없습니다.')
    previous = {key: _text(params.get(key)) for key in values}
    if any(value is not None and (not isinstance(value, str) or len(value) > 64) for value in previous.values()):
      raise ProfileError('현재 튜닝값에 복구할 수 없는 항목이 있어 복원을 중단했습니다.')
    _offroad(params)
    _atomic_json(root / 'restore-pending.json', {'format': FORMAT, 'version': VERSION, 'previous': previous})
    try:
      for key, value in values.items():
        _offroad(params)
        _write_param(params, key, value)
      _offroad(params)
    except Exception as error:
      try:
        _recover_locked(params, root)
      except Exception as recovery_error:
        raise ProfileError('복원이 중단되었습니다. 재부팅하면 이전 설정 복구를 다시 시도합니다.') from recovery_error
      raise ProfileError('복원에 실패하여 이전 설정으로 되돌렸습니다.') from error
    (root / 'restore-pending.json').unlink()
    _sync_directory(root)
    return {'count': len(values), 'skipped': skipped}


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('action', choices=('save', 'restore'))
  parser.add_argument('slot', choices=('a', 'b'))
  args = parser.parse_args()
  from common.params import Params
  try:
    action = save_profile if args.action == 'save' else restore_profile
    result = action(Params(), args.slot)
    message = '설정 %d개를 %s했습니다.' % (result['count'], '저장' if args.action == 'save' else '복원')
    if result['skipped']:
      message += '\n호환되지 않는 항목 %d개는 제외했습니다.' % result['skipped']
    if args.action == 'restore':
      message += '\n시작 시 읽는 설정까지 적용하려면 주행 전에 재부팅하세요.'
    print(json.dumps({'ok': True, 'message': message}, ensure_ascii=False))
    return 0
  except (ProfileError, OSError) as error:
    print(json.dumps({'ok': False, 'message': str(error)}, ensure_ascii=False))
    return 1


if __name__ == '__main__':
  raise SystemExit(main())
