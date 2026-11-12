"""CGW1 turn-signal request (cgw_blinker.py): command bits and when they are sent."""
from selfdrive.car.hyundai.cgw_blinker import (CgwBlinker, command_frame, CGW1_ADDR, FLASH_HALF_FRAMES,
                                               LANE_MAX_FRAMES, TEST_FRAMES, TX_STEP, LEFT, RIGHT)


def run(blinker, frames, start=0, **kw):
  args = dict(test_selection=0, parked=False, lane_dir=0, hazard=False, lever_left=False, lever_right=False)
  args.update(kw)
  out = {}
  for f in range(start, start + frames):
    sent = blinker.update(f, **args)
    if sent:
      out[f] = sent[0]
  return out


def test_command_bits_match_cgw1_signals():
  # TSigLHSw bit 42 = byte 5 0x04, TurnSigLh bits 19..20 value 1 = byte 2 0x08
  assert command_frame(LEFT, True) == [CGW1_ADDR, 0, bytes([0, 0, 0x08, 0, 0, 0x04, 0, 0]), 0]
  assert command_frame(LEFT, False)[2] == bytes([0, 0, 0, 0, 0, 0x04, 0, 0])
  # TSigRHSw bit 41 = byte 5 0x02, TurnSigRh bits 62..63 value 1 = byte 7 0x40
  assert command_frame(RIGHT, True)[2] == bytes([0, 0, 0, 0, 0, 0x02, 0, 0x40])
  assert command_frame(RIGHT, False)[2] == bytes([0, 0, 0, 0, 0, 0x02, 0, 0])


def test_lane_change_flashes_like_stock_at_20hz():
  out = run(CgwBlinker(), 200, lane_dir=LEFT)
  assert sorted(out) == list(range(0, 200, TX_STEP))
  assert out[0][2][2] == 0x08                            # lamp on first
  assert out[FLASH_HALF_FRAMES + 5][2][2] == 0           # off in the second 0.35 s
  assert out[2 * FLASH_HALF_FRAMES][2][2] == 0x08        # on again
  assert all(m[2][5] == 0x04 for m in out.values())      # lever side held throughout


def test_lane_change_capped_and_restarts_on_new_request():
  b = CgwBlinker()
  out = run(b, LANE_MAX_FRAMES + 100, lane_dir=RIGHT)
  assert max(out) < LANE_MAX_FRAMES
  assert not run(b, 1, start=LANE_MAX_FRAMES + 100, lane_dir=0)
  assert run(b, 10, start=LANE_MAX_FRAMES + 200, lane_dir=RIGHT)  # next lane change


def test_never_adds_to_hazard_or_driver_lever():
  assert not run(CgwBlinker(), 50, lane_dir=LEFT, hazard=True)
  assert not run(CgwBlinker(), 50, lane_dir=LEFT, lever_left=True)
  assert not run(CgwBlinker(), 50, lane_dir=LEFT, lever_right=True)


def test_parked_test_is_one_shot_three_seconds():
  b = CgwBlinker()
  run(b, 1, test_selection=0, parked=True)   # armed at 0 (CLEAR_ON_MANAGER_START)
  out = run(b, TEST_FRAMES + 100, start=1, test_selection=RIGHT, parked=True)
  assert out and max(out) <= TEST_FRAMES
  assert all(m[2][5] == 0x02 for m in out.values())
  # still selected: no repeat until set back to 0
  assert not run(b, 100, start=TEST_FRAMES + 200, test_selection=RIGHT, parked=True)
  run(b, 1, start=TEST_FRAMES + 300, test_selection=0, parked=True)
  assert run(b, 10, start=TEST_FRAMES + 301, test_selection=LEFT, parked=True)


def test_parked_test_stops_when_not_parked():
  b = CgwBlinker()
  run(b, 1, parked=True)
  assert run(b, 10, start=1, test_selection=LEFT, parked=True)
  assert not run(b, 50, start=11, test_selection=LEFT, parked=False)
  assert not run(b, 50, start=61, test_selection=LEFT, parked=True)  # stays off until re-armed
