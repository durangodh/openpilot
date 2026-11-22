"""Recorded-scene-shaped fixtures, not a claim of ego-signal association."""
import importlib.util
from pathlib import Path
import unittest
import tempfile
import time

spec = importlib.util.spec_from_file_location('candidate_server', Path(__file__).resolve().parents[1] / 'server.py')
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)


def detection(label='red', score=.74, x=.57, y=.60):
  return {'label': label, 'score': score, 'box': [x-.015, y-.012, x+.015, y+.012]}


class CandidateTests(unittest.TestCase):
  def setUp(self):
    self.f = srv.CandidateFilter()

  def confirm(self, ds, start=10):
    for dt in (0, .35, .7):
      out = self.f.update(ds, start+dt)
    return out

  def test_recorded_two_heads_and_lower_candidate(self):
    ds = [detection(), detection(x=.67, score=.69), detection(x=.46, y=.80, score=.44)]
    out = self.confirm(ds)
    self.assertEqual(out['status'], 'stable')
    self.assertEqual(out['detections'], [ds[0]])
    self.assertFalse(out['ego_signal_confirmed'])

  def test_three_frames_and_elapsed_time_both_required(self):
    for now in (10, 10.1, 10.2, 10.3):
      self.assertEqual(self.f.update([detection()], now)['status'], 'warming')
    self.assertEqual(self.f.update([detection()], 10.7)['status'], 'stable')

  def test_single_frame_spike_not_highlighted(self):
    self.assertEqual(self.f.update([detection()], 10)['detections'], [])
    self.assertEqual(self.f.update([], 10.35)['status'], 'none')

  def test_colour_change_immediately_clears_previous_red(self):
    self.confirm([detection()])
    green = [detection('green', score=.38)]
    self.assertEqual(self.f.update(green, 11)['detections'], [])
    self.assertEqual(self.f.update(green, 11.35)['status'], 'warming')
    out = self.f.update(green, 11.7)
    self.assertEqual(out['detections'], green)

  def test_low_green_is_not_artificially_boosted(self):
    out = self.confirm([detection('green', score=.38)])
    self.assertEqual(out['detections'][0]['score'], .38)

  def test_red_and_left_are_preserved(self):
    ds = [detection(), detection('left', score=.7)]
    self.assertEqual(len(self.confirm(ds)['detections']), 2)

  def test_left_does_not_become_green(self):
    out = self.confirm([detection('left')])
    self.assertEqual(out['detections'][0]['label'], 'left')

  def test_opposing_colours_are_uncertain(self):
    for labels in [('red', 'green'), ('yellow', 'green'), ('yellow', 'red')]:
      out = self.confirm([detection(label) for label in labels])
      self.assertEqual(out['status'], 'conflict')
      self.assertEqual(out['detections'], [])

  def test_equally_central_heads_are_not_guessed(self):
    self.assertEqual(self.confirm([detection(x=.46), detection(x=.54)])['status'], 'ambiguous')

  def test_missing_candidate_clears_colour_immediately(self):
    self.confirm([detection()])
    self.assertEqual(self.f.update([], 11)['detections'], [])
    self.assertEqual(self.f.update([detection()], 11.35)['status'], 'warming')

  def test_processing_gap_requires_new_confirmation(self):
    self.confirm([detection()])
    self.assertEqual(self.f.update([detection()], 12)['status'], 'warming')

  def test_repeated_timestamp_cannot_confirm(self):
    for _ in range(10):
      self.assertEqual(self.f.update([detection()], 10)['status'], 'warming')

  def test_spatial_target_switch_requires_new_confirmation(self):
    self.confirm([detection(x=.45)])
    self.assertEqual(self.f.update([detection(x=.55)], 11)['status'], 'warming')

  def test_duplicate_tiles_do_not_count_as_three_frames(self):
    d = detection()
    out = self.f.update([d, dict(d), dict(d)], 10)
    self.assertEqual(out['status'], 'warming')
    self.assertEqual(len(self.confirm([d, dict(d)], start=11)['detections']), 1)

  def test_outside_band_and_low_scores_not_selected(self):
    for d in [detection(x=.1), detection(y=.9), detection(score=.34)]:
      self.assertEqual(self.confirm([d])['status'], 'none')

  def test_receiver_file_and_hud_only_use_stable_selection(self):
    observer_spec = importlib.util.spec_from_file_location('candidate_observer', Path(__file__).resolve().parents[1] / 'observer.py')
    observer = importlib.util.module_from_spec(observer_spec)
    observer_spec.loader.exec_module(observer)
    clock = [10.0]
    with tempfile.TemporaryDirectory() as folder:
      output = Path(folder) / 'result.json'
      r = srv.Results(clock=lambda: clock[0], output=str(output))
      for t in (10, 10.35, 10.7):
        clock[0] = t
        result = r.accept({'session':r.session,'frame_id':r.issue(),'model_sha256':srv.MODEL_SHA,
                          'detections':[detection(), detection(x=.67)]})
        shown = observer.read_observer(str(output), now=time.time)
        self.assertEqual(len(shown['detections']), 1 if t == 10.7 else 0)
        self.assertEqual(len(result['detections']), 2)  # Raw evidence stays available.
      clock[0] = 11
      r.accept({'session':r.session,'frame_id':r.issue(),'model_sha256':srv.MODEL_SHA,
                'detections':[detection('green', score=.38)]})
      self.assertEqual(observer.read_observer(str(output))['detections'], [])
      clock[0] = 13
      self.assertEqual(r.snapshot()['selection']['detections'], [])

  def test_rejected_post_cannot_warm_filter(self):
    clock = [10.0]
    r = srv.Results(clock=lambda: clock[0])
    for t in (10, 10.35, 10.7):
      clock[0] = t
      m = {'session':r.session,'frame_id':r.issue(),'model_sha256':srv.MODEL_SHA,
           'detections':[detection()]}
      if t < 10.7:
        m['session'] = 'bad'
        with self.assertRaises(srv.BadResult): r.accept(m)
      else:
        self.assertEqual(r.accept(m)['selection']['status'], 'warming')


if __name__ == '__main__':
  unittest.main()
