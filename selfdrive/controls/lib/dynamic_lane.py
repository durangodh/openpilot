LANE_PROFILE_ONLY = 0
LANE_PROFILE_LESS = 1
LANE_PROFILE_AUTO = 2

LANE_PROB_ENTER = 0.5
LANE_PROB_EXIT = 0.3


def update_dynamic_lane_profile(profile, left_prob, right_prob, lane_change_active,
                                lane_change_off, low_speed, laneless_buffer):
  """Return (use_laneless, updated_buffer) for the selected lane profile."""
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

  return low_speed or profile_laneless, laneless_buffer


def select_lateral_path(model_path_xyz, lane_path_xyz, use_laneless):
  """Select a path without aliasing either source array."""
  selected_path = model_path_xyz if use_laneless else lane_path_xyz
  return selected_path.copy()
