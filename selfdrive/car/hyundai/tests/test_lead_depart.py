from selfdrive.car.hyundai.lead_depart import LeadDepartDetector


def run(d, v, lead, frames):
  return [d.update(v, lead) for _ in range(frames)]


def test_fires_once_when_the_lead_moves_off_while_ego_stands():
  d = LeadDepartDetector()
  assert not any(run(d, 0.0, 6.0, 150))
  out = run(d, 0.0, 9.0, 50)
  assert out.count(True) == 1
  assert not any(run(d, 0.0, 15.0, 50))       # only once per stop


def test_no_alert_while_moving_or_without_a_close_lead():
  d = LeadDepartDetector()
  assert not any(run(d, 5.0, 6.0, 200) + run(d, 5.0, 12.0, 50))
  d = LeadDepartDetector()
  assert not any(run(d, 0.0, 30.0, 150) + run(d, 0.0, 40.0, 50))
  d = LeadDepartDetector()
  run(d, 0.0, 6.0, 150)
  run(d, 2.0, 6.0, 10)                         # ego moved: a new stop starts over
  assert not any(run(d, 0.0, 9.0, 50))


def test_only_in_drive():
  d = LeadDepartDetector()
  assert not any(d.update(0.0, 6.0, in_drive=False) for _ in range(150))
  assert not any(d.update(0.0, 9.0, in_drive=False) for _ in range(50))   # P/N: never
  # stopped in D, shifted to N while the lead leaves, back to D: the stop starts over
  d = LeadDepartDetector()
  run(d, 0.0, 6.0, 150)
  d.update(0.0, 7.0, in_drive=False)
  assert not any(run(d, 0.0, 9.0, 50))
