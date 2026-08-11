LANE_PROFILE_ONLY = 0
LANE_PROFILE_LESS = 1
LANE_PROFILE_AUTO = 2

LANE_PROB_ENTER = 0.5
LANE_PROB_EXIT = 0.3

LOW_SPEED_LANELESS_ENTER = 14.0 / 3.6
LOW_SPEED_LANELESS_EXIT = 18.0 / 3.6


def update_low_speed_laneless(v_ego, low_speed_laneless):
  """Apply a 14/18 km/h hysteresis around the legacy 16 km/h boundary."""
  if low_speed_laneless:
    return v_ego < LOW_SPEED_LANELESS_EXIT
  return v_ego < LOW_SPEED_LANELESS_ENTER


def update_dynamic_lane_profile(profile, left_prob, right_prob, lane_change_active,
                                lane_change_off, low_speed, laneless_buffer):
  """Return (status_laneless, profile_laneless, updated_buffer)."""
  if profile == LANE_PROFILE_LESS:
    profile_laneless = True
    laneless_buffer = True
  elif profile == LANE_PROFILE_ONLY:
    profile_laneless = False
    laneless_buffer = False
  elif profile == LANE_PROFILE_AUTO:
    if lane_change_active:
      # Stay laneless until both new lane lines are confidently detected.
      laneless_buffer = True
    elif lane_change_off:
      if left_prob < LANE_PROB_EXIT and right_prob < LANE_PROB_EXIT:
        laneless_buffer = True
      elif left_prob > LANE_PROB_ENTER and right_prob > LANE_PROB_ENTER:
        laneless_buffer = False

    # preLaneChange keeps the previous automatic selection. The actual lane
    # change states above force laneless operation.
    profile_laneless = laneless_buffer
  else:
    # Invalid persisted values fall back to Lane only, matching profile 0.
    profile_laneless = False
    laneless_buffer = False

  # Low speed remains visible as laneless, but profile_laneless is kept
  # separate so Lane-only and confident Auto can use LanePlanner's gradual
  # 5-10 km/h lane blend instead of switching paths at 16 km/h.
  return low_speed or profile_laneless, profile_laneless, laneless_buffer


def select_lateral_path(model_path_xyz, lane_path_xyz, use_laneless):
  """Select a path without aliasing either source array."""
  selected_path = model_path_xyz if use_laneless else lane_path_xyz
  return selected_path.copy()
