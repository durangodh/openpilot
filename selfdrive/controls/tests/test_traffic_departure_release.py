"""Guard immediate green-signal departure without weakening stopped-lead safety."""
from pathlib import Path


def test_green_departure_bypasses_only_no_lead_delay():
  source = (Path(__file__).resolve().parents[1] / "lib" / "longcontrol.py").read_text()
  assert "traffic_departure = int(getattr(long_plan, 'trafficState', 0)) % 100 == 2" in source
  assert "driver_override or lead_release or traffic_departure or" in source

  lead_branch = source[source.index("if self.standstill_lead_latched and not radar_fallback:"):
                       source.index("else:", source.index("if self.standstill_lead_latched and not radar_fallback:"))]
  assert "traffic_departure" not in lead_branch


if __name__ == "__main__":
  test_green_departure_bypasses_only_no_lead_delay()
  print("traffic departure release check passed")
