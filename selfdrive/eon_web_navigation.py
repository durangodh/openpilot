"""On-demand route preview for EON Web; never a steering or model input."""
import math

from selfdrive.controls.lib.navigation_route import NavigationRouteData


REASONS = {
  "ready": "내비 경로 수신 중",
  "off_route": "경로 이탈 · 경로 재탐색 대기",
  "instruction": "최신 회전 안내 대기",
  "route": "최신 경로·차량 위치 대기",
  "heading": "차량 진행 방향 대기",
  "route_position": "현재 위치 주변의 경로 없음",
}


def navigation_preview(state):
  """256 m route window, ego-up, with the vehicle at (128, 192).

  Route selection/local projection uses the same bounded geometry reader as
  curve deceleration. No tile download, new daemon or background renderer.
  Missing or stale inputs return no points, so the browser clears its map.
  """
  result = {"reason": "instruction", "points": [], "current": None, "next": None}
  for name, guidance in (("current", state), ("next", state.get("next"))):
    if isinstance(guidance, dict) and guidance.get("fresh", False):
      distance = guidance.get("distance", -1.0)
      if name == "next":
        segment = guidance.get("segment", -1.0)
        current = state.get("distance", -1.0)
        distance = current + segment if current >= 0 and segment >= 0 and state.get("fresh") else -1.0
      if isinstance(distance, (int, float)) and math.isfinite(distance) and distance >= 0:
        result[name] = {"text": str(guidance.get("text", ""))[:200],
                        "kind": guidance.get("kind", "none"),
                        "direction": guidance.get("direction", 0), "distance": distance}
  if state.get("off_route", False):
    result["reason"] = "off_route"
  elif not state.get("guidance_fresh", state.get("fresh", False)):
    pass
  elif not state.get("route_fresh", False):
    result["reason"] = "route"
  else:
    vehicle = state.get("vehicle") or {}
    try:
      heading = float(vehicle.get("heading_deg"))
    except (TypeError, ValueError):
      heading = float("nan")
    if not math.isfinite(heading) or not 0.0 <= heading <= 360.0:
      result["reason"] = "heading"
    else:
      route = NavigationRouteData._local_route_points(state, 220.0)
      result["reason"] = "route_position"
      if route is not None:
        sine, cosine = math.sin(math.radians(heading)), math.cos(math.radians(heading))
        result["points"] = [[round(128.0 + east * cosine - north * sine, 2),
                             round(192.0 - east * sine - north * cosine, 2)]
                            for east, north in route[0]]
        result["reason"] = "ready"
  result["status"] = REASONS[result["reason"]]
  return result
