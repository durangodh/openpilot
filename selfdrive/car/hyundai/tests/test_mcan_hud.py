"""Stock HUD TBT frames must match what the Genesis DH AVN sends (2026-10-31 M-CAN logs)."""
from selfdrive.car.hyundai.mcan_hud import (McanHud, isotp_frames, text_payload, tbt_payload,
                                            BAR_ADDR, TBT_ADDR, TEXT_ADDR, MCAN_BUS)


def test_tbt_payload_matches_avn_frames():
  assert tbt_payload(13, 290).hex() == "0d08110c01220090"   # right, 290 m (AVN 0d08110c....0090)
  assert tbt_payload(14, 300).hex() == "1f000024012c00a0"   # U-turn 300 m, AVN frame verbatim
  assert tbt_payload(11, 600).hex() == "4600000f025800f0"   # keep going 600 m, AVN frame verbatim
  assert tbt_payload(2, 900).hex() == "70000000038400f0"    # destination ahead 900 m, AVN frame verbatim
  assert tbt_payload(2, 5)[0] == 3                          # arrived
  assert tbt_payload(12, 400)[3] == 36                      # left = 270 deg / 7.5
  assert tbt_payload(0, 100) is None                        # unknown: leave the AVN frame


def test_bar_steps_like_avn():
  bars = {d: tbt_payload(13, d)[7] >> 4 for d in (400, 290, 260, 200, 110, 50, 20, 0)}
  assert bars == {400: 10, 290: 9, 260: 8, 200: 6, 110: 3, 50: 1, 20: 1, 0: 1}


def test_text_frames_match_avn():
  assert [f.hex() for f in isotp_frames(text_payload("서동탄역"))] == ["1009f01cc1d9b3c4", "21d0edc5aaaaaaaa"]
  assert [f.hex() for f in isotp_frames(text_payload("노작로"))] == ["07f078b191c75cb8"]
  assert [f.hex() for f in isotp_frames(text_payload(""))] == ["03f00000aaaaaaaa"]


def nav(turn_type=13, dist=200.0, text="반석초", fresh=True):
  return {"guidance_fresh": fresh, "off_route": False, "turn_type": turn_type, "distance": dist, "text": text}


def run(hud, state, frames, start=0):
  out = []
  for f in range(start, start + frames):
    out += [(m[0], bytes(m[2]), m[3]) for m in hud.update(f, state)]
  return out


def test_update_sends_tbt_at_50hz_and_text_once():
  hud = McanHud()
  msgs = run(hud, nav(text="서동탄역"), 100)
  assert all(bus == MCAN_BUS for _, _, bus in msgs)
  assert sum(1 for a, _, _ in msgs if a == TBT_ADDR) == 50
  assert sum(1 for a, _, _ in msgs if a == BAR_ADDR) == 50
  text = [d.hex() for a, d, _ in msgs if a == TEXT_ADDR]
  assert text == ["1009f01cc1d9b3c4", "21d0edc5aaaaaaaa"]


def test_stale_guidance_clears_text_and_stops():
  hud = McanHud()
  run(hud, nav(text="노작로"), 10)
  msgs = run(hud, nav(fresh=False), 10, start=10)
  assert [d.hex() for a, d, _ in msgs if a == TEXT_ADDR] == ["03f00000aaaaaaaa"]
  assert not any(a == TBT_ADDR for a, _, _ in msgs)


def test_disabled_without_guidance():
  assert run(McanHud(), {}, 20) == []
