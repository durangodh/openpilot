import math
import statistics


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


def _near_line_y(line, maximum_x=30.0):
  """Robust near-field lateral position for HUD-only lane placement."""
  xs = list(_field(line, "x", []) or [])
  ys = list(_field(line, "y", []) or [])
  values = [_finite_float(y, float("nan")) for x, y in zip(xs, ys)
            if math.isfinite(_finite_float(x, float("nan"))) and
            0.0 <= _finite_float(x) <= maximum_x and
            math.isfinite(_finite_float(y, float("nan")))]
  return statistics.median(values) if len(values) >= 2 else None


LANE_SAMPLE_XS = (5.0, 10.0, 15.0, 20.0, 25.0)
LANE_GEOMETRY_MAD_MAX = 0.35


def _line_y_at(line, x):
  """Interpolate one model line at an exact forward distance."""
  xs = list(_field(line, "x", []) or [])
  ys = list(_field(line, "y", []) or [])
  points = []
  for raw_x, raw_y in zip(xs, ys):
    px = _finite_float(raw_x, float("nan"))
    py = _finite_float(raw_y, float("nan"))
    if math.isfinite(px) and math.isfinite(py):
      points.append((px, py))
  if len(points) < 2:
    return None
  points.sort(key=lambda point: point[0])
  if x < points[0][0] or x > points[-1][0]:
    return None
  for (x0, y0), (x1, y1) in zip(points, points[1:]):
    if x <= x1:
      if x1 <= x0:
        continue
      ratio = (x - x0) / (x1 - x0)
      return y0 + ratio * (y1 - y0)
  return None


ROAD_EDGE_STD_MAX = 0.5
PHANTOM_LANE_MIN_ERROR = 0.10
MAX_GEOMETRY_SHIFT_M = 2.0


def camera_lane_position(model):
  """Estimate the ego lane from matched modelV2 cross-sections.

  Lane lines and road edges are sampled at the same forward distances.  This
  avoids comparing unrelated points on curves.  Medians reject a single noisy
  section, while inconsistent merge/fork geometry fails closed instead of
  publishing a wrong current lane.  The result is display-only.
  """
  lanes = list(_field(model, "laneLines", []) or [])
  edges = list(_field(model, "roadEdges", []) or [])
  lane_probs = list(_field(model, "laneLineProbs", []) or [])
  edge_stds = list(_field(model, "roadEdgeStds", []) or [])
  if len(lanes) < 3 or len(edges) < 2:
    return None

  try:
    inner_conf = min(_finite_float(lane_probs[1], 0.0),
                     _finite_float(lane_probs[2], 0.0))
    # roadEdgeStds is a standard deviation in metres, not a probability.
    edge_std = max(_finite_float(edge_stds[0], 9.9),
                   _finite_float(edge_stds[1], 9.9))
  except (IndexError, TypeError):
    return None
  if inner_conf < 0.45 or edge_std > ROAD_EDGE_STD_MAX:
    return None

  lane_widths = []
  left_ratios = []
  right_ratios = []
  for x in LANE_SAMPLE_XS:
    inner = [_line_y_at(lanes[1], x), _line_y_at(lanes[2], x)]
    road = [_line_y_at(edges[0], x), _line_y_at(edges[1], x)]
    if any(value is None for value in inner + road):
      continue

    lane_left, lane_right = max(inner), min(inner)
    road_left, road_right = max(road), min(road)
    lane_width = lane_left - lane_right
    if not 2.5 <= lane_width <= 4.5 or road_left < lane_left or road_right > lane_right:
      continue
    lane_widths.append(lane_width)
    left_ratios.append(max(0.0, road_left - lane_left) / lane_width)
    right_ratios.append(max(0.0, lane_right - road_right) / lane_width)

  if len(lane_widths) < 3:
    return None

  lane_width = statistics.median(lane_widths)
  left_ratio = statistics.median(left_ratios)
  right_ratio = statistics.median(right_ratios)
  left_mad = statistics.median(abs(value - left_ratio) for value in left_ratios)
  right_mad = statistics.median(abs(value - right_ratio) for value in right_ratios)
  if max(left_mad, right_mad) > LANE_GEOMETRY_MAD_MAX:
    return None

  left_lanes = int(round(left_ratio))
  right_lanes = int(round(right_ratio))
  total = 1 + left_lanes + right_lanes
  current = 1 + left_lanes
  if not 1 <= total <= 8 or not 1 <= current <= total:
    return None
  return {
    "n": total,
    "cur": current,
    "confidence": round(inner_conf, 2),
    "laneWidth": round(lane_width, 2),
    "leftFrac": round(left_ratio - math.floor(left_ratio), 4),
    "rightFrac": round(right_ratio - math.floor(right_ratio), 4),
  }


def reconcile_lane_position(position, route_count):
  """Remove one camera-only shoulder/median lane using the TMAP lane count.

  This is display-only.  An exact count is accepted as-is; a single extra
  camera lane is removed only when exactly one side was rounded up from a
  clearly partial lane width.  Ambiguous geometry continues to fail closed.
  """
  if not isinstance(position, dict):
    return None
  try:
    camera_count = int(position.get("n", 0))
    current = int(position.get("cur", 0))
    route_count = int(route_count)
  except (TypeError, ValueError):
    return None
  if not 1 <= route_count <= 8 or not 1 <= current <= camera_count:
    return None

  resolved = dict(position)
  if camera_count == route_count:
    return resolved
  if camera_count - route_count != 1:
    return None

  try:
    left_frac = float(position.get("leftFrac", 0.0))
    right_frac = float(position.get("rightFrac", 0.0))
  except (TypeError, ValueError):
    return None

  def phantom(frac):
    return 0.5 <= frac <= 1.0 - PHANTOM_LANE_MIN_ERROR

  phantom_left = phantom(left_frac)
  phantom_right = phantom(right_frac)
  if phantom_left == phantom_right:
    return None
  current -= 1 if phantom_left else 0
  if not 1 <= current <= route_count:
    return None
  resolved["n"] = route_count
  resolved["cur"] = current
  resolved["reconciled"] = True
  return resolved


def _sample_points_y(points, x):
  """Linearly sample compact HUD [[x, y, ...], ...] points."""
  if not isinstance(points, list) or len(points) < 2:
    return None
  usable = [point for point in points
            if isinstance(point, (list, tuple)) and len(point) >= 2]
  if len(usable) < 2:
    return None
  if x <= _finite_float(usable[0][0]):
    return _finite_float(usable[0][1])
  for left, right in zip(usable, usable[1:]):
    x0 = _finite_float(left[0])
    x1 = _finite_float(right[0])
    if x <= x1:
      y0 = _finite_float(left[1])
      y1 = _finite_float(right[1])
      ratio = (x - x0) / (x1 - x0) if x1 > x0 else 0.0
      return y0 + ratio * (y1 - y0)
  return _finite_float(usable[-1][1])


def _cached_points_y(points):
  """Return an x sampler that interpolates each distinct x only once."""
  cache = {}

  def sample(x):
    if x not in cache:
      cache[x] = _sample_points_y(points, x)
    return cache[x]

  return sample


def align_scene_geometry(path, lanes, edges):
  """Anchor model lane/edge geometry to the final MPC path centre.

  modelV2 lane lines and the optimized MPC path can use slightly different
  lateral centres after offsets or NOO map blending.  the driving scene previously drew
  both unchanged, making the ribbon and road slide apart.  Preserve every
  observed width and shape, but translate each cross-section so all geometry
  shares the path centre used by control and lead rendering.
  """
  if not isinstance(path, list) or len(path) < 2 or not isinstance(lanes, list) or len(lanes) < 3:
    return lanes, edges
  left = lanes[1].get("p") if isinstance(lanes[1], dict) else None
  right = lanes[2].get("p") if isinstance(lanes[2], dict) else None
  if not isinstance(left, list) or len(left) < 2 or not isinstance(right, list) or len(right) < 2:
    return lanes, edges
  if min(_finite_float(lanes[1].get("c", 0.0)),
         _finite_float(lanes[2].get("c", 0.0))) < 0.45:
    return lanes, edges

  # modelV2 lines normally share the same 33 x positions.  Cache those
  # cross-sections instead of scanning path/inner lanes again for every one
  # of the six lane/edge lines (the old hot path repeated it ~200 times).
  path_y_at = _cached_points_y(path)
  left_y_at = _cached_points_y(left)
  right_y_at = _cached_points_y(right)
  shift_cache = {}

  def shift_at(x):
    if x in shift_cache:
      return shift_cache[x]
    path_y = path_y_at(x)
    left_y = left_y_at(x)
    right_y = right_y_at(x)
    if path_y is None or left_y is None or right_y is None:
      shift = 0.0
    else:
      shift = max(-MAX_GEOMETRY_SHIFT_M,
                  min(MAX_GEOMETRY_SHIFT_M, path_y - (left_y + right_y) * 0.5))
    shift_cache[x] = shift
    return shift

  def aligned(lines):
    output = []
    for line in lines or []:
      if not isinstance(line, dict):
        output.append(line)
        continue
      copy = dict(line)
      points = line.get("p")
      if isinstance(points, list):
        copy["p"] = [[point[0], round(_finite_float(point[1]) + shift_at(_finite_float(point[0])), 2)]
                     for point in points if isinstance(point, (list, tuple)) and len(point) >= 2]
      output.append(copy)
    return output

  return aligned(lanes), aligned(edges)


def scale_scene_width(path, lines, scale, centre_cache=None):
  """Scale HUD-only lateral geometry around the final path centre."""
  try:
    scale = max(0.5, min(1.6, float(scale)))
  except (TypeError, ValueError):
    scale = 1.0
  if not isinstance(path, list) or len(path) < 2 or not isinstance(lines, list):
    return lines
  if centre_cache is None:
    centre_cache = {}
  path_y_at = _cached_points_y(path)

  def centre_at(x):
    if x not in centre_cache:
      centre = path_y_at(x)
      centre_cache[x] = 0.0 if centre is None else centre
    return centre_cache[x]

  output = []
  for line in lines:
    if not isinstance(line, dict):
      output.append(line)
      continue
    copy = dict(line)
    points = line.get("p")
    if isinstance(points, list):
      scaled = []
      for point in points:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
          continue
        x = _finite_float(point[0])
        y = _finite_float(point[1])
        centre = centre_at(x)
        scaled.append([point[0], round(centre + (y - centre) * scale, 2)])
      copy["p"] = scaled
    output.append(copy)
  return output


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
