"""Camera lane count / ego lane, shared by NOO (controls) and the remote HUD.

NOO and the HUD used to estimate this separately with different geometry and
thresholds, so the HUD could show a different lane than the one NOO acted on.
Both now call this module.

TMAP/Naver current_lane is never used as the ego lane (Naver publishes its
recommended lane there). The ego lane comes from modelV2 geometry; the route
lane count is only used to reconcile a shoulder/median over-count or a
road-edge under-count (resolve_current_lane).
"""
import math
import statistics

# Lane lines and road edges are compared at the same forward distances, so a
# curve does not compare unrelated points.
SAMPLE_XS = (5.0, 10.0, 15.0, 20.0, 25.0)
MIN_SECTIONS = 3
# Median absolute deviation of the per-section lane ratios. A fork or merge
# whose geometry changes along the samples fails closed.
GEOMETRY_MAD_MAX = 0.35
LANE_WIDTH_MIN = 2.5
LANE_WIDTH_MAX = 4.5

# roadEdgeStds is a standard deviation in metres, not a probability. 0.5 almost
# always failed on roads with three or more lanes (the far edge is uncertain),
# so it was relaxed; the geometry consistency check above guards the result.
ROAD_EDGE_STD_MAX = 1.2
# Ego lane line probability floor. 0.45 often failed next to a merging lane or
# on faded markings.
LANE_PROB_MIN = 0.35
# A road edge slightly inside the ego lane line is model noise and is snapped
# onto the line (m). A section with an edge further inside is discarded.
EDGE_INSIDE_TOL = 0.5
# An outer model lane line is independent evidence that one adjacent lane
# exists even when roadEdges temporarily collapses onto the ego lane.
ADJACENT_LANE_PROB_MIN = 0.55
ADJACENT_SPACING_RATIO = (0.65, 1.35)

# Reconciliation with the route lane count. A side counts as a phantom
# (shoulder/median rounded up to a lane) when its width fraction is >= 0.5 but
# clearly not a whole lane.
PHANTOM_LANE_MIN_ERROR = 0.10
# When neither side is a clear phantom, the side whose fraction is larger by at
# least this much is taken as the shoulder.
PHANTOM_AMBIGUOUS_MARGIN = 0.15
MAX_LANES = 8


def _finite(value, default=float("nan")):
  try:
    number = float(value)
  except (TypeError, ValueError):
    return default
  return number if math.isfinite(number) else default


def _field(message, name, default=None):
  try:
    return getattr(message, name)
  except Exception:
    return default


def line_y_at(line, x):
  """Interpolate one model line at an exact forward distance (None if out of range)."""
  points = []
  for raw_x, raw_y in zip(list(_field(line, "x", []) or []), list(_field(line, "y", []) or [])):
    px, py = _finite(raw_x), _finite(raw_y)
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
      return y0 + (x - x0) / (x1 - x0) * (y1 - y0)
  return None


def estimate(model):
  """Return (lane_position or None, reason) for the current modelV2 frame.

  lane_position keys: count, current (1 = leftmost), confidence, width,
  left_frac/right_frac (fractional lane widths beyond the ego lane, used to
  spot a shoulder or median counted as a lane), left_adjacent/right_adjacent
  (an outer lane line confirms a lane on that side), edge_std.
  """
  if model is None:
    return None, "no modelV2"
  lanes = list(_field(model, "laneLines", []) or [])
  edges = list(_field(model, "roadEdges", []) or [])
  lane_probs = list(_field(model, "laneLineProbs", []) or [])
  edge_stds = list(_field(model, "roadEdgeStds", []) or [])
  if len(lanes) < 3 or len(edges) < 2 or len(lane_probs) < 3 or len(edge_stds) < 2:
    return None, "laneLines/roadEdges missing"

  inner_conf = min(_finite(lane_probs[1], 0.0), _finite(lane_probs[2], 0.0))
  edge_std = max(_finite(edge_stds[0], 9.9), _finite(edge_stds[1], 9.9))
  if inner_conf < LANE_PROB_MIN:
    return None, "lane prob %.2f < %.2f" % (inner_conf, LANE_PROB_MIN)
  if edge_std > ROAD_EDGE_STD_MAX:
    return None, "roadEdgeStd %.2f > %.2f" % (edge_std, ROAD_EDGE_STD_MAX)

  widths, left_ratios, right_ratios = [], [], []
  rejected = {"missing": 0, "width": 0, "edge_inside": 0}
  for x in SAMPLE_XS:
    inner = [line_y_at(lanes[1], x), line_y_at(lanes[2], x)]
    road = [line_y_at(edges[0], x), line_y_at(edges[1], x)]
    if any(value is None for value in inner + road):
      rejected["missing"] += 1
      continue
    lane_left, lane_right = max(inner), min(inner)
    road_left, road_right = max(road), min(road)
    width = lane_left - lane_right
    if not LANE_WIDTH_MIN <= width <= LANE_WIDTH_MAX:
      rejected["width"] += 1
      continue
    if lane_left - road_left > EDGE_INSIDE_TOL or road_right - lane_right > EDGE_INSIDE_TOL:
      rejected["edge_inside"] += 1
      continue
    road_left = max(road_left, lane_left)
    road_right = min(road_right, lane_right)
    widths.append(width)
    left_ratios.append((road_left - lane_left) / width)
    right_ratios.append((lane_right - road_right) / width)

  if len(widths) < MIN_SECTIONS:
    worst = max(rejected, key=rejected.get)
    return None, "only %d/%d sections usable (%s)" % (len(widths), len(SAMPLE_XS), worst)

  width = statistics.median(widths)
  left_ratio = statistics.median(left_ratios)
  right_ratio = statistics.median(right_ratios)
  mad = max(statistics.median(abs(value - left_ratio) for value in left_ratios),
            statistics.median(abs(value - right_ratio) for value in right_ratios))
  if mad > GEOMETRY_MAD_MAX:
    return None, "inconsistent geometry (fork/merge) mad %.2f" % mad

  def adjacent_visible(outer_index, inner_index):
    if outer_index >= len(lanes) or outer_index >= len(lane_probs):
      return False
    gaps = []
    for x in SAMPLE_XS:
      outer_y = line_y_at(lanes[outer_index], x)
      inner_y = line_y_at(lanes[inner_index], x)
      if outer_y is not None and inner_y is not None:
        gaps.append(abs(outer_y - inner_y))
    if len(gaps) < MIN_SECTIONS:
      return False
    spacing = statistics.median(gaps)
    return (_finite(lane_probs[outer_index], 0.0) >= ADJACENT_LANE_PROB_MIN and
            width * ADJACENT_SPACING_RATIO[0] <= spacing <= width * ADJACENT_SPACING_RATIO[1])

  left_lanes = int(round(left_ratio))
  right_lanes = int(round(right_ratio))
  count = 1 + left_lanes + right_lanes
  current = 1 + left_lanes
  if not 1 <= count <= MAX_LANES:
    return None, "lane count %d out of range" % count

  position = {
    "count": count,
    "current": current,
    "confidence": inner_conf,
    "width": width,
    "left_frac": left_ratio - math.floor(left_ratio),
    "right_frac": right_ratio - math.floor(right_ratio),
    "left_adjacent": adjacent_visible(0, 1),
    "right_adjacent": adjacent_visible(3, 2),
    "edge_std": edge_std,
  }
  return position, "cam %d lanes, in %d (std %.2f, width %.2f, L%.2f R%.2f, adjacent=%s/%s)" % (
    count, current, edge_std, width, left_ratio, right_ratio,
    position["left_adjacent"], position["right_adjacent"])


def camera_lane_position(model):
  return estimate(model)[0]


def resolve_current_lane(ego_lane, route_count):
  """Reconcile the camera ego lane with the navigation lane count.

  Returns the ego lane numbered against route_count, or None when it cannot be
  determined. Korean roads usually have a paved shoulder, so the road edge sits
  about a lane beyond the outermost line and the camera counts one lane too
  many; a median can add another. A road-edge collapse can also undercount.
  """
  if not isinstance(ego_lane, dict):
    return None
  try:
    camera_count = int(ego_lane.get("count", 0))
    current = int(ego_lane.get("current", 0))
    route_count = int(route_count)
  except (TypeError, ValueError):
    return None
  if not 1 <= route_count <= MAX_LANES or not 1 <= current <= camera_count:
    return None
  diff = camera_count - route_count
  if diff == 0:
    return current

  # roadEdges can briefly collapse onto the ego lane and report one lane on a
  # multi-lane road. On a two- or three-lane route the fixed outer lane lines
  # uniquely place the car.
  if diff < 0:
    if route_count not in (2, 3):
      return None
    resolved = ({
      (False, True): 1,
      (True, False): route_count,
      (True, True): 2 if route_count == 3 else None,
    }).get((ego_lane.get("left_adjacent") is True, ego_lane.get("right_adjacent") is True))
    return resolved if resolved is not None and 1 <= resolved <= route_count else None

  # One extra lane (shoulder or median) or two (shoulder and median).
  if diff not in (1, 2):
    return None
  try:
    left_frac = float(ego_lane.get("left_frac", 0.0))
    right_frac = float(ego_lane.get("right_frac", 0.0))
  except (TypeError, ValueError):
    return None

  # A phantom candidate is a side that was rounded up (fraction >= 0.5). A
  # fraction near 1.0 is a real lane width. A side below 0.5 (for example a
  # 1 m median) was never counted, so it cannot be the phantom; without this
  # distinction the common shoulder + median motorway would always fail.
  def phantom(frac):
    return 0.5 <= frac <= 1.0 - PHANTOM_LANE_MIN_ERROR

  phantom_left = phantom(left_frac)
  phantom_right = phantom(right_frac)
  if diff == 2:
    if not (phantom_left and phantom_right):
      return None
    resolved = current - 1
  elif phantom_left != phantom_right:
    resolved = current - 1 if phantom_left else current
  else:
    if abs(left_frac - right_frac) < PHANTOM_AMBIGUOUS_MARGIN:
      return None
    resolved = current - 1 if left_frac > right_frac else current
  return resolved if 1 <= resolved <= route_count else None
