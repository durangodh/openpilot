import math

from selfdrive.eon_web_navigation import navigation_preview
from selfdrive.controls.lib.navigation_route import NavigationPoseTrust, NavigationRouteData


def route_state():
  return {"fresh": True, "guidance_fresh": True, "route_fresh": True,
          "kind": "turn", "direction": -1, "distance": 100.0,
          "vehicle": {"lat": 37.0, "lon": 127.0, "heading_deg": 0},
          "route": {"polyline": [{"lat": 37.0 + i * 10.0 / 111195.0, "lon": 127.0}
                                 for i in range(30)]}}


def test_route_preview_is_vehicle_up_and_bounded():
  result = navigation_preview(route_state())
  assert result["reason"] == "ready"
  assert len(result["points"]) <= 512
  assert result["points"][0] == [128.0, 192.0]
  assert result["points"][-1][1] < result["points"][0][1]
  assert all(abs(x - 128.0) < 0.01 for x, _ in result["points"])


def test_stale_offroute_invalid_heading_and_distant_position_clear_the_map():
  for change, expected in (({"off_route": True}, "off_route"),
                           ({"guidance_fresh": False}, "instruction"),
                           ({"route_fresh": False}, "route"),
                           ({"vehicle": {"heading_deg": float("nan")}}, "heading"),
                           ({"vehicle": {"lat": 38, "lon": 128, "heading_deg": 0}}, "route_position")):
    result = navigation_preview(dict(route_state(), **change))
    assert result["reason"] == expected
    assert result["points"] == []


def test_non_finite_guidance_and_out_of_range_position_cannot_become_valid():
  for distance in (math.inf, math.nan, -math.inf):
    state = NavigationRouteData.guidance_state({"turn_type": 12, "distance_m": distance}, True)
    assert not state["fresh"]
  for lat, lon in ((91, 127), (37, 181), (0, 0), (math.inf, 127)):
    trust = NavigationPoseTrust()
    state = dict(route_state(), vehicle={"lat": lat, "lon": lon})
    for _ in range(60):
      assert not trust.update(state, 0.0, 0.05)
    assert NavigationRouteData._lat_lon(state["vehicle"]) is None
