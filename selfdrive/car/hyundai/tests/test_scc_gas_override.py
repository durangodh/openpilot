"""SCC12/14 ACCMode during driver gas/brake override and AutoHold (aPilot c2-master)."""
import ast
import copy
from pathlib import Path


class FakePacker:
  def make_can_msg(self, name, bus, values):
    return (name, 0, bytes(8), dict(values))


def _load():
  source = Path(__file__).resolve().parents[1] / 'hyundaican.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('create_scc12', 'create_scc14')]
  env = {'copy': copy}
  exec(compile(ast.Module(body=fns, type_ignores=[]), str(source), 'exec'), env)
  return env['create_scc12'], env['create_scc14']


scc12, scc14 = _load()


def _m12(**kw):
  args = dict(packer=FakePacker(), apply_accel=0.0, enabled=True, cnt=0, scc_live=True, scc12={},
              gaspressed=False, brakepressed=False, standstill=False, car_fingerprint=None)
  args.update(kw)
  return scc12(**args)[3]


def _m14(**kw):
  args = dict(packer=FakePacker(), enabled=True, e_vgo=10.0, standstill=False, accel=0.0, gaspressed=False,
              objgap=0, objgap2=0, scc14={})
  args.update(kw)
  return scc14(**args)[3]


def test_gas_override_reports_driver_accel_with_zero_request():
  m = _m12(gaspressed=True, long_override=True, apply_accel=0.5)
  assert m['ACCMode'] == 2 and m['aReqRaw'] == 0.0 and m['aReqValue'] == 0.0 and m['StopReq'] == 0
  assert _m14(gaspressed=True, long_override=True)['ACCMode'] == 4


def test_active_and_inactive_unchanged():
  assert _m12(long_active=True, apply_accel=0.8)['ACCMode'] == 1
  assert _m12(long_active=True, apply_accel=0.8)['aReqValue'] == 0.8
  assert _m12()['ACCMode'] == 0
  assert _m14(long_active=True)['ACCMode'] == 1
  assert _m14()['ACCMode'] == 0
  assert _m12(enabled=False, long_override=True)['ACCMode'] == 0
  assert _m14(enabled=False, long_override=True)['ACCMode'] == 0


def test_brake_reports_override_on_scc14_only():
  # aPilot: brake -> SCC12 0, SCC14 4
  assert _m12(brakepressed=True)['ACCMode'] == 0
  assert _m14(brakepressed=True)['ACCMode'] == 4
  assert _m14(brakepressed=True, enabled=False)['ACCMode'] == 0


def test_autohold_reports_scc12_0_scc14_4_with_zero_request():
  m = _m12(long_active=True, apply_accel=-0.5, brake_hold=True)
  assert m['ACCMode'] == 0 and m['aReqValue'] == 0.0 and m['StopReq'] == 0
  assert _m14(long_active=True, brake_hold=True)['ACCMode'] == 4


def test_soft_hold_mode2_keeps_control_while_braking():
  m = _m12(brakepressed=True, soft_hold_active=True, standstill=True, apply_accel=-0.5)
  assert m['ACCMode'] == 1 and m['StopReq'] == 1
  assert _m14(brakepressed=True, soft_hold_active=True)['ACCMode'] == 1
