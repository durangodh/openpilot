def select_physical_gap(current_gap, physical_gap, controls_enabled, gap_button_event):
  """Return the gap to keep and whether it represents a driver-selected change."""
  if not controls_enabled and not gap_button_event:
    return current_gap, False

  gap = int(physical_gap)
  if 1 <= gap <= 4 and gap != current_gap:
    return gap, True
  return current_gap, False
