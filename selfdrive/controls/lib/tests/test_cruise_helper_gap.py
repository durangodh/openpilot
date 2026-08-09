from selfdrive.controls.lib.gap_sync import select_physical_gap


def test_disengage_fallback_does_not_overwrite_saved_gap():
  gap, changed = select_physical_gap(2, 4, controls_enabled=False, gap_button_event=False)
  assert gap == 2
  assert not changed


def test_engaged_physical_gap_change_is_persisted():
  gap, changed = select_physical_gap(4, 2, controls_enabled=True, gap_button_event=False)
  assert gap == 2
  assert changed


def test_explicit_gap_button_is_honored_while_disengaged():
  gap, changed = select_physical_gap(2, 3, controls_enabled=False, gap_button_event=True)
  assert gap == 3
  assert changed
