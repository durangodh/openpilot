import math


MAX_POLYLINE_POINTS = 24
MAX_RADAR_POINTS = 16


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


def _point_series(polyline):
  raw_xs = _field(polyline, "x", [])
  raw_ys = _field(polyline, "y", [])
  xs = list(raw_xs) if raw_xs is not None else []
  ys = list(raw_ys) if raw_ys is not None else []
  count = min(len(xs), len(ys))
  if count <= 0:
    return []
  stride = max(1, int(math.ceil(float(count) / MAX_POLYLINE_POINTS)))
  points = []
  for index in range(0, count, stride):
    x = _finite_float(xs[index], -1.0)
    y = _finite_float(ys[index])
    if 0.0 <= x <= 160.0:
      points.append((x, y))
  if count > 1 and (count - 1) % stride and len(points) < MAX_POLYLINE_POINTS:
    x = _finite_float(xs[-1], -1.0)
    y = _finite_float(ys[-1])
    if 0.0 <= x <= 160.0:
      points.append((x, y))
  return points


def _smooth_polyline(points):
  """Locally smooth display geometry without a global-curve overshoot."""
  if len(points) < 3:
    return points

  ordered = sorted(points, key=lambda point: point[0])
  smoothed = []
  for index, (x, y) in enumerate(ordered):
    start = max(0, index - 2)
    end = min(len(ordered), index + 3)
    weighted_sum = 0.0
    weight_total = 0.0
    for neighbor in range(start, end):
      distance = abs(neighbor - index)
      weight = (3.0, 2.0, 1.0)[min(2, distance)]
      weighted_sum += float(ordered[neighbor][1]) * weight
      weight_total += weight
    local_y = weighted_sum / max(1.0, weight_total)
    # A convex local average cannot swing outside the nearby model points,
    # unlike the previous whole-line quadratic fit on long road curves.
    smoothed.append((float(x), local_y * 0.78 + float(y) * 0.22))
  return smoothed


def _probability(values, index, default=1.0):
  try:
    return max(0.0, min(1.0, _finite_float(values[index], default)))
  except (IndexError, TypeError):
    return default


def _lead(radar_state, name):
  lead = _field(radar_state, name)
  if lead is None or not bool(_field(lead, "status", False)):
    return None
  distance = _finite_float(_field(lead, "dRel", -1.0), -1.0)
  if distance <= 0.0 or distance > 160.0:
    return None
  return {
    "distance": distance,
    "lateral": _finite_float(_field(lead, "yRel", 0.0)),
    "relative_speed": _finite_float(_field(lead, "vRel", 0.0)),
  }


def extract_radar_points(live_tracks):
  points = []
  try:
    tracks = list(live_tracks or [])
  except TypeError:
    tracks = []
  for track in tracks:
    distance = _finite_float(_field(track, "dRel", -1.0), -1.0)
    lateral = _finite_float(_field(track, "yRel", 0.0))
    if distance <= 0.0 or distance > 160.0 or abs(lateral) > 12.0:
      continue
    points.append({
      "distance": distance,
      "lateral": lateral,
      "relative_speed": _finite_float(_field(track, "vRel", 0.0)),
      "stationary": bool(_field(track, "stationary", False)),
      "track_id": int(_finite_float(_field(track, "trackId", -1), -1)),
    })
  points.sort(key=lambda point: point["distance"])
  return points[:MAX_RADAR_POINTS]


def extract_hud_scene(model, radar_state, include_debug_counts=False):
  """Extract only geometry that the external HUD actually paints."""
  leads = []
  for name in ("leadOne", "leadTwo"):
    lead = _lead(radar_state, name)
    if lead is not None:
      leads.append(lead)

  scene = {
    "path": _point_series(_field(model, "position")),
    "lanes": [],
    "edges": [],
    "leads": leads,
  }
  if include_debug_counts:
    try:
      scene["lane_count"] = len(_field(model, "laneLines", []) or [])
    except TypeError:
      scene["lane_count"] = 0
    try:
      scene["edge_count"] = len(_field(model, "roadEdges", []) or [])
    except TypeError:
      scene["edge_count"] = 0
  return scene


def extract_driving_scene(model, radar_state):
  raw_lane_probabilities = _field(model, "laneLineProbs", [])
  lane_probabilities = list(raw_lane_probabilities) if raw_lane_probabilities is not None else []
  raw_lanes = _field(model, "laneLines", [])
  lanes = []
  for index, lane in enumerate(list(raw_lanes) if raw_lanes is not None else []):
    probability = _probability(lane_probabilities, index)
    points = _point_series(lane)
    if len(points) >= 2 and probability >= 0.45:
      # Keep the modelV2 index: laneLines[1] and [2] bound the ego lane, and
      # that mapping is lost once a weak line is filtered out of the list.
      lanes.append({"points": _smooth_polyline(points), "probability": probability,
                    "index": index})

  edges = []
  raw_edge_stds = _field(model, "roadEdgeStds", [])
  edge_stds = list(raw_edge_stds) if raw_edge_stds is not None else []
  raw_edges = _field(model, "roadEdges", [])
  for index, edge in enumerate(list(raw_edges) if raw_edges is not None else []):
    points = _point_series(edge)
    std = max(0.0, _finite_float(edge_stds[index], 0.0)) if index < len(edge_stds) else 0.0
    probability = max(0.0, min(1.0, 1.0 - std))
    if len(points) >= 2 and probability >= 0.40:
      # Road edges need the same display-only spatial smoothing as lane lines.
      # Otherwise noisy model points become exaggerated bends after the
      # perspective projection on the shallow 9.2-inch HUD.
      edges.append({"points": _smooth_polyline(points), "probability": probability,
                    "index": index})

  leads = []
  for name in ("leadOne", "leadTwo"):
    lead = _lead(radar_state, name)
    if lead is not None:
      leads.append(lead)

  return {
    "path": _point_series(_field(model, "position")),
    "lanes": lanes,
    "edges": edges,
    "leads": leads,
  }
