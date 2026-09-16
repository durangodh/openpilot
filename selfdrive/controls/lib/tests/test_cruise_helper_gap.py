from selfdrive.controls.lib.gap_sync import select_physical_gap, select_software_gap


def test_disengage_fallback_does_not_overwrite_saved_gap():
  gap, changed = select_physical_gap(2, 4, short_gap_release=False)
  assert gap == 2
  assert not changed


def test_passive_physical_gap_change_is_not_persisted():
  gap, changed = select_physical_gap(4, 2, short_gap_release=False)
  assert gap == 4
  assert not changed


def test_completed_short_physical_gap_press_is_persisted():
  gap, changed = select_physical_gap(4, 2, short_gap_release=True)
  assert gap == 2
  assert changed


def test_openpilot_long_ignores_stock_startup_fallback():
  gap, changed = select_software_gap(2, short_gap_release=False)
  assert gap == 2
  assert not changed


def test_openpilot_long_cycles_from_saved_gap():
  gap = 2
  expected = [3, 4, 1, 2]
  for target in expected:
    gap, changed = select_software_gap(gap, short_gap_release=True)
    assert gap == target
    assert changed


def test_navigation_long_press_keeps_saved_gap_two():
  gap = 2
  for _ in range(150):
    gap, changed = select_software_gap(gap, short_gap_release=False)
    assert not changed
  # The release is consumed by navigation switching, so it is not a short
  # gap release and must not overwrite PrevCruiseGap.
  gap, changed = select_software_gap(gap, short_gap_release=False)
  assert gap == 2
  assert not changed
