"""Stock HUD TBT frames must match what the Genesis DH AVN sends (2026-10-31 M-CAN logs)."""
from selfdrive.car.hyundai.mcan_hud import (McanHud, isotp_frames, text_payload, tbt_payload, camera_payload,
                                            BAR_ADDR, TBT_ADDR, TEXT_ADDR, CAMERA_ADDR, MCAN_BUS)


def test_tbt_payload_matches_avn_frames():
  assert tbt_payload(13, 290).hex() == "0d08110c01220090"   # right, 290 m (AVN 0d08110c....0090)
  assert tbt_payload(14, 300).hex() == "1f000024012c00a0"   # U-turn 300 m, AVN frame verbatim
  assert tbt_payload(11, 600, next_turn_type=12).hex() == "4600000f025800f0"  # keep going, left next (AVN)
  assert tbt_payload(11, 400, next_turn_type=18).hex() == "4200000f019000f0"  # bear right next (AVN 07:16)
  assert tbt_payload(11, 12700)[:1].hex() == "41"                              # plain straight
  assert tbt_payload(18, 400).hex() == "0d000303019000a0"   # right exit / IC, AVN frame verbatim
  assert tbt_payload(11, 700, "북오산톨게이트").hex() == "b200000f02bc00f0"     # tollgate 700 m, AVN verbatim
  assert tbt_payload(11, 300, "북오산톨게이트").hex() == "b200000f012c00a0"     # tollgate 300 m, AVN verbatim
  assert tbt_payload(2, 900).hex() == "70000000038400f0"    # destination ahead 900 m, AVN frame verbatim
  assert tbt_payload(2, 5)[0] == 3                          # arrived
  assert tbt_payload(201, 900) == tbt_payload(2, 900)       # TMAP destination code
  assert tbt_payload(12, 400)[3] == 36                      # left = 270 deg / 7.5
  assert tbt_payload(0, 100) is None                        # unknown: leave the AVN frame


def test_km_distances_like_avn_start_screen():
  assert tbt_payload(11, 20000)[4:8].hex() == "001410f0"    # AVN: "20.0km"
  assert tbt_payload(11, 66700)[4:8].hex() == "004210f7"    # AVN: "66.7km"
  assert tbt_payload(11, 6200)[4:8].hex() == "000610f2"     # AVN: "6.2km"
  assert tbt_payload(11, 999)[4:8].hex() == "03e700f0"      # metres below 1 km


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


def test_update_sends_tbt_at_20hz_and_text_once():
  hud = McanHud()
  msgs = run(hud, nav(text="서동탄역"), 100)
  assert all(bus == MCAN_BUS for _, _, bus in msgs)
  assert sum(1 for a, _, _ in msgs if a == TBT_ADDR) == 20
  assert sum(1 for a, _, _ in msgs if a == BAR_ADDR) == 20
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


def test_camera_frames_match_avn():
  assert camera_payload({"type": 1, "distance": 300, "limit": 40}).hex() == "04000000f0000fff"    # 07:17 photo
  assert camera_payload({"type": 1, "distance": 300, "limit": 100}).hex() == "0a000000f0000fff"   # 07:19
  assert camera_payload({"type": 0, "distance": 300, "limit": 50}).hex() == "00500000f0000fff"    # 07:16
  assert camera_payload({"type": 1, "distance": 300, "limit": 40}, 48)[1] == 0x04               # over the limit
  assert camera_payload({"type": 1, "distance": 900, "limit": 40}) is None                      # too far
  assert camera_payload({"type": 22, "distance": 100, "limit": 30}) is None                     # speed bump


def test_camera_sent_without_turn_guidance():
  state = {"speed_fresh": True, "off_route": False,
           "speed": {"sdi": {"type": 1, "distance_m": 250, "speed_limit_kph": 40}}}
  msgs = run(McanHud(), state, 10)
  assert [d.hex() for a, d, _ in msgs if a == CAMERA_ADDR] == ["04000000f0000fff"] * 2
