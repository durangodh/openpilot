from types import SimpleNamespace

from selfdrive.eon_cluster import remote_hud


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
  sm = SimpleNamespace(alive={"carState": True}, valid={"carState": True})
  assert remote_hud._stream_ok(sm, "carState")
  sm.valid["carState"] = False
  assert not remote_hud._stream_ok(sm, "carState")
  assert not remote_hud._stream_ok(sm, "missing")

