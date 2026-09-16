def select_physical_gap(current_gap, physical_gap, short_gap_release):
  """Persist the physical SCC gap only after a completed short GAP press."""
  if not short_gap_release:
    return current_gap, False

  gap = int(physical_gap)
  if 1 <= gap <= 4 and gap != current_gap:
    return gap, True
  return current_gap, False


def select_software_gap(current_gap, short_gap_release):
  """Cycle Hyundai gap 1→2→3→4→1 after a completed short GAP press."""
  gap = int(current_gap)
  if not 1 <= gap <= 4:
    gap = 4
  if not short_gap_release:
    return gap, False
  return (gap + 1 if gap < 4 else 1), True
