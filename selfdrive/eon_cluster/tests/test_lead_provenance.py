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



class _FakeSM(dict):
  pass


def test_battery_voltage_comes_from_panda_supply_measurement():
  sm = _FakeSM(peripheralState=SimpleNamespace(voltage=14230))
  sm.frame, sm.rcv_frame = 100, {"peripheralState": 95}
  assert remote_hud._battery_voltage(sm) == 14.2
  sm.rcv_frame["peripheralState"] = 50   # stale (> ~3 s at 10 Hz)
  assert remote_hud._battery_voltage(sm) == -1.0
  sm.rcv_frame["peripheralState"] = 95
  sm["peripheralState"] = SimpleNamespace(voltage=0)   # no measurement
  assert remote_hud._battery_voltage(sm) == -1.0


def test_weather_area_keeps_only_eup_myeon_dong():
  assert remote_hud.area_name("경기도 오산시 궐동 123-4") == "궐동"
  assert remote_hud.area_name("경기 화성시 동탄1동") == "동탄1동"
  assert remote_hud.area_name("경기도 화성시 봉담읍 와우리") == "봉담읍"
  assert remote_hud.area_name("경기도 양평군 서종면") == "서종면"
  assert remote_hud.area_name("경기 오산시 경기동로 59") == ""
  assert remote_hud.area_name("서울 송파구 잠실동 아파트 101동") == "잠실동"
  assert remote_hud.area_name(None) == ""
