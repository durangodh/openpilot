"""Manual external-model selection permits motion only while disengaged."""
import sys
from types import SimpleNamespace as NS

import pytest

from selfdrive import eon_web_live as web


def state(monkeypatch, gear='drive', speed=20., enabled=False, active=False,
          can=True, alive=True, valid=True):
  import cereal
  data = dict(carState=NS(canValid=can, vEgo=speed, gearShifter=gear),
              controlsState=NS(enabled=enabled, active=active))
  class Messages:
    def __init__(self, names):
      self.alive = dict.fromkeys(names, alive)
      self.valid = dict.fromkeys(names, valid)
    def update(self, timeout): pass
    def __getitem__(self, key): return data[key]
  fake = NS(SubMaster=Messages)
  monkeypatch.setitem(sys.modules, 'cereal.messaging', fake)
  monkeypatch.setattr(cereal, 'messaging', fake, raising=False)
  clock = [0.]
  def tick():
    clock[0] += 1.
    return clock[0]
  monkeypatch.setattr(web.time, 'monotonic', tick)


@pytest.mark.parametrize('gear,speed', [('park',0.),('drive',0.),('drive',10.),('drive',30.)])
def test_disengaged_selection_permits_drive_and_motion(monkeypatch, gear, speed):
  state(monkeypatch, gear=gear, speed=speed)
  assert web.activation_block_reason() is None


@pytest.mark.parametrize('enabled,active', [(True,False),(False,True),(True,True)])
def test_motion_never_bypasses_disengagement(monkeypatch, enabled, active):
  state(monkeypatch, enabled=enabled, active=active)
  assert '인게이지' in web.activation_block_reason()


@pytest.mark.parametrize('bad', [dict(can=False),dict(speed=float('nan')),dict(speed=float('inf')),
                               dict(alive=False),dict(valid=False)])
def test_motion_permission_keeps_vehicle_and_message_health_checks(monkeypatch, bad):
  state(monkeypatch, **bad)
  assert web.activation_block_reason() is not None
