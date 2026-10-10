import sys
import types
from types import SimpleNamespace

# remote_hud imports the native messaging/params bindings at module level. These
# tests only use its pure helpers, so on a checkout without the scons build
# stand in empty modules instead of failing to collect.
for _name, _attrs in (("cereal.messaging", ()), ("common.params", ("Params",))):
  try:
    __import__(_name)
  except ImportError:
    _stub = types.ModuleType(_name)
    for _attr in _attrs:
      setattr(_stub, _attr, object)
    sys.modules[_name] = _stub

from selfdrive.eon_cluster import remote_hud  # noqa: E402


def test_radar_lead_wire_keeps_sensor_provenance():
  radar = SimpleNamespace(leadOne=SimpleNamespace(
    status=True, dRel=21.0, yRel=-0.3, vRel=-1.0, aLeadK=-0.2,
    radar=True, modelProb=0.76,
  ))
  vision = SimpleNamespace(leadOne=SimpleNamespace(
    status=True, dRel=21.0, yRel=-0.3, vRel=-1.0, aLeadK=-0.2,
    radar=False, modelProb=0.76,
  ))

  assert remote_hud._lead(radar, "leadOne")["src"] == "R"
  assert remote_hud._lead(vision, "leadOne")["src"] == "V"


def test_stream_health_requires_alive_and_valid():
  sm = SimpleNamespace(frame=100, rcv_frame={"carState": 95}, valid={"carState": True})
  assert remote_hud._stream_ok(sm, "carState")
  sm.rcv_frame["carState"] = 80  # stale: not received in the last ~1 s of loops
  assert not remote_hud._stream_ok(sm, "carState")
  sm.rcv_frame["carState"] = 95
  sm.valid["carState"] = False
  assert not remote_hud._stream_ok(sm, "carState")
  assert not remote_hud._stream_ok(sm, "missing")

