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
  """Fit a stable quadratic lateral curve for display-only lane rendering."""
  if len(points) < 4:
    return points

  max_x = max(point[0] for point in points)
  if max_x <= 1.0:
    return points

  # Weighted least squares for y = a + b*u + c*u^2. Near-field points have
  # slightly more weight because their projection occupies most HUD pixels.
  matrix = [[0.0] * 4 for _ in range(3)]
  for x, y in points:
    u = max(0.0, min(1.0, float(x) / max_x))
    basis = (1.0, u, u * u)
    weight = 1.0 / (1.0 + 0.75 * u)
    for row in range(3):
      for col in range(3):
        matrix[row][col] += weight * basis[row] * basis[col]
      matrix[row][3] += weight * basis[row] * float(y)

  for diagonal in range(3):
    matrix[diagonal][diagonal] += 1e-7

  # Small pivoted Gaussian elimination avoids adding NumPy load to EON.
  for pivot in range(3):
    best = max(range(pivot, 3), key=lambda row: abs(matrix[row][pivot]))
    if abs(matrix[best][pivot]) < 1e-9:
      return points
    if best != pivot:
      matrix[pivot], matrix[best] = matrix[best], matrix[pivot]
    divisor = matrix[pivot][pivot]
    matrix[pivot] = [value / divisor for value in matrix[pivot]]
    for row in range(3):
      if row == pivot:
        continue
      factor = matrix[row][pivot]
      matrix[row] = [matrix[row][col] - factor * matrix[pivot][col] for col in range(4)]

  a, b, c = (matrix[index][3] for index in range(3))
  return [(x, a + b * (float(x) / max_x) + c * math.pow(float(x) / max_x, 2.0))
          for x, _ in points]


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


def extract_driving_scene(model, radar_state):
  raw_lane_probabilities = _field(model, "laneLineProbs", [])
  lane_probabilities = list(raw_lane_probabilities) if raw_lane_probabilities is not None else []
  raw_lanes = _field(model, "laneLines", [])
  lanes = []
  for index, lane in enumerate(list(raw_lanes) if raw_lanes is not None else []):
    probability = _probability(lane_probabilities, index)
    points = _point_series(lane)
    if len(points) >= 2 and probability >= 0.45:
      lanes.append({"points": _smooth_polyline(points), "probability": probability})

  edges = []
  raw_edge_stds = _field(model, "roadEdgeStds", [])
  edge_stds = list(raw_edge_stds) if raw_edge_stds is not None else []
  raw_edges = _field(model, "roadEdges", [])
  for index, edge in enumerate(list(raw_edges) if raw_edges is not None else []):
    points = _point_series(edge)
    if len(points) >= 2:
      std = max(0.0, _finite_float(edge_stds[index], 0.0)) if index < len(edge_stds) else 0.0
      edges.append({"points": points, "probability": max(0.2, min(1.0, 1.0 - std))})

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
