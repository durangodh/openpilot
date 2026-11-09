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

import numpy as np

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
_SAMPLE_XS_ARRAY = np.asarray(SAMPLE_XS, dtype=float)
_NAN_SAMPLES = np.full(len(SAMPLE_XS), np.nan)


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


def _median(values):
  # np.median costs more than the rest of this module on <= 5 samples.
  v = sorted(float(x) for x in values)
  n = len(v)
  return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def _line_samples(line):
  """One model line read once, interpolated at SAMPLE_XS (nan outside its range).

  Reading each capnp line once (instead of once per sample) keeps this as
  cheap as the per-line medians NOO used before the HUD/NOO merge; plannerd
  shares a CPU core with radard on EON.
  """
  try:
    xs = np.asarray(list(_field(line, "x", []) or []), dtype=float)
    ys = np.asarray(list(_field(line, "y", []) or []), dtype=float)
  except (TypeError, ValueError):
    return _NAN_SAMPLES
  n = min(len(xs), len(ys))
  if n < 2:
    return _NAN_SAMPLES
  xs, ys = xs[:n], ys[:n]
  ok = np.isfinite(xs) & np.isfinite(ys)
  if np.count_nonzero(ok) < 2:
    return _NAN_SAMPLES
  xs, ys = xs[ok], ys[ok]
  order = np.argsort(xs, kind="stable")
  xs, ys = xs[order], ys[order]
  return np.interp(_SAMPLE_XS_ARRAY, xs, ys, left=np.nan, right=np.nan)


def line_y_at(line, x):
  """Interpolate one model line at an exact forward distance (None if out of range)."""
  try:
    xs = np.asarray(list(_field(line, "x", []) or []), dtype=float)
    ys = np.asarray(list(_field(line, "y", []) or []), dtype=float)
  except (TypeError, ValueError):
    return None
  n = min(len(xs), len(ys))
  ok = np.isfinite(xs[:n]) & np.isfinite(ys[:n])
  xs, ys = xs[:n][ok], ys[:n][ok]
  if len(xs) < 2:
    return None
  order = np.argsort(xs, kind="stable")
  value = float(np.interp(x, xs[order], ys[order], left=np.nan, right=np.nan))
  return value if math.isfinite(value) else None


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

  inner_a, inner_b = _line_samples(lanes[1]), _line_samples(lanes[2])
  road_a, road_b = _line_samples(edges[0]), _line_samples(edges[1])
  lane_left, lane_right = np.fmax(inner_a, inner_b), np.fmin(inner_a, inner_b)
  road_left, road_right = np.fmax(road_a, road_b), np.fmin(road_a, road_b)
  present = np.isfinite(inner_a) & np.isfinite(inner_b) & np.isfinite(road_a) & np.isfinite(road_b)
  width = lane_left - lane_right
  width_ok = present & (width >= LANE_WIDTH_MIN) & (width <= LANE_WIDTH_MAX)
  inside_ok = width_ok & (lane_left - road_left <= EDGE_INSIDE_TOL) & (road_right - lane_right <= EDGE_INSIDE_TOL)
  rejected = {"missing": int(np.count_nonzero(~present)),
              "width": int(np.count_nonzero(present & ~width_ok)),
              "edge_inside": int(np.count_nonzero(width_ok & ~inside_ok))}
  if np.count_nonzero(inside_ok) < MIN_SECTIONS:
    worst = max(rejected, key=rejected.get)
    return None, "only %d/%d sections usable (%s)" % (int(np.count_nonzero(inside_ok)), len(SAMPLE_XS), worst)

  widths = width[inside_ok]
  left_ratios = (np.fmax(road_left, lane_left)[inside_ok] - lane_left[inside_ok]) / widths
  right_ratios = (lane_right[inside_ok] - np.fmin(road_right, lane_right)[inside_ok]) / widths

  width = _median(widths)
  left_ratio = _median(left_ratios)
  right_ratio = _median(right_ratios)
  mad = max(_median(np.abs(left_ratios - left_ratio)),
            _median(np.abs(right_ratios - right_ratio)))
  if mad > GEOMETRY_MAD_MAX:
    return None, "inconsistent geometry (fork/merge) mad %.2f" % mad

  def adjacent_visible(outer_index, inner_samples):
    if outer_index >= len(lanes) or outer_index >= len(lane_probs):
      return False
    gaps = np.abs(_line_samples(lanes[outer_index]) - inner_samples)
    gaps = gaps[np.isfinite(gaps)]
    if len(gaps) < MIN_SECTIONS:
      return False
    spacing = _median(gaps)
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
    "left_adjacent": adjacent_visible(0, inner_a),
    "right_adjacent": adjacent_visible(3, inner_b),
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
