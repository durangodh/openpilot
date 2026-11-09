import math

from selfdrive.controls.lib import lane_count


def _field(message, name, default=None):
  try:
    return getattr(message, name)
  except Exception:
    return default


def _finite_float(value, default=0.0):
  try:
    number = float(value)
  except (TypeError, ValueError):
    return default
  return number if math.isfinite(number) else default


def _hud_lane_position(position):
  """Shared lane estimate (controls/lib/lane_count.py) in the HUD JSON keys."""
  if not isinstance(position, dict):
    return None
  return {
    "n": int(position["count"]),
    "cur": int(position["current"]),
    "confidence": round(float(position["confidence"]), 2),
    "laneWidth": round(float(position["width"]), 2),
    "leftFrac": round(float(position["left_frac"]), 4),
    "rightFrac": round(float(position["right_frac"]), 4),
    "leftAdjacent": bool(position["left_adjacent"]),
    "rightAdjacent": bool(position["right_adjacent"]),
  }


def camera_lane_position(model):
  """Ego lane from modelV2 geometry, the same estimate NOO uses. Display-only."""
  return _hud_lane_position(lane_count.camera_lane_position(model))


def reconcile_lane_position(position, route_count):
  """Reconcile the camera lane with the TMAP/Naver lane count, as NOO does.

  An over-count from a shoulder/median is removed and a road-edge under-count
  is expanded from the outer lane lines; ambiguous geometry fails closed.
  """
  if not isinstance(position, dict):
    return None
  try:
    ego_lane = {
      "count": int(position.get("n", 0)),
      "current": int(position.get("cur", 0)),
      "left_frac": float(position.get("leftFrac", 0.0)),
      "right_frac": float(position.get("rightFrac", 0.0)),
      "left_adjacent": position.get("leftAdjacent") is True,
      "right_adjacent": position.get("rightAdjacent") is True,
    }
    route_count = int(route_count)
  except (TypeError, ValueError):
    return None
  current = lane_count.resolve_current_lane(ego_lane, route_count)
  if current is None:
    return None
  resolved = dict(position)
  if ego_lane["count"] == route_count:
    return resolved
  resolved["n"] = route_count
  resolved["cur"] = current
  resolved["reconciled"] = True
  return resolved


def final_lateral_path(lateral_plan, model, time_indices, limit=33):
  """Build the optimized MPC path and retain model road elevation.

  dPathPoints is only the reference passed into the solver. LateralPlanner
  publishes the solver's x/y state trajectory as mpcPathX/mpcPathY. Keep model
  Z at the same shooting-node index so the renderer retains road elevation.
  """
  if not bool(_field(lateral_plan, "mpcSolutionValid", False)):
    return []
  raw_xs = _field(lateral_plan, "mpcPathX", [])
  raw_ys = _field(lateral_plan, "mpcPathY", [])
  xs = list(raw_xs) if raw_xs is not None else []
  ys = list(raw_ys) if raw_ys is not None else []
  if not xs or not ys:
    return []

  position = _field(model, "position")
  zs = list(_field(position, "z", []) or [])
  count = min(len(xs), len(ys), len(time_indices), int(limit))
  if count < 2:
    return []
  return [[round(_finite_float(xs[i]), 2),
           round(_finite_float(ys[i]), 2),
           round(_finite_float(zs[i] if i < len(zs) else 0.0), 2)]
          for i in range(count)]
