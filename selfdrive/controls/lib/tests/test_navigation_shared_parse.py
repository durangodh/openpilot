"""plannerd's two navigation readers parse each written guide file once."""
import json
from unittest.mock import patch

from selfdrive.controls.lib.navigation_route import NavigationRouteData


def test_two_readers_share_one_parse(tmp_path):
  path = tmp_path / "guide.json"
  path.write_text(json.dumps({"guidance_current": {"turn_type": 12, "distance_m": 100}}))
  first, second = NavigationRouteData(str(path)), NavigationRouteData(str(path))
  with patch("json.load", wraps=json.load) as load:
    first.update()
    second.update()
    assert load.call_count == 1
    path.write_text(json.dumps({"guidance_current": {"turn_type": 13, "distance_m": 90}}))
    first.last_read = second.last_read = 0.0
    first.update()
    second.update()
    assert load.call_count == 2
  assert first.state["turn_type"] == second.state["turn_type"] == 13
