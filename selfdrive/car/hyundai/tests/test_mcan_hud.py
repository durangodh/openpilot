"""Stock HUD TBT frames must match what the Genesis DH AVN sends (2026-10-31 M-CAN logs)."""
from selfdrive.car.hyundai.mcan_hud import (McanHud, lane_side, isotp_frames, text_payload, tbt_payload, camera_payload, section_payload, bar_percent,
                                            BAR_ADDR, TBT_ADDR, TEXT_ADDR, CAMERA_ADDR, MCAN_BUS)


def test_tbt_payload_matches_avn_frames():
  assert tbt_payload(13, 290).hex() == "0d08110c01220090"   # right, 290 m (AVN 0d08110c....0090)
  assert tbt_payload(14, 300).hex() == "1f000024012c00a0"   # U-turn 300 m, AVN frame verbatim
  assert tbt_payload(11, 600, next_turn_type=12).hex() == "4600000f025800f0"  # keep going, left next (AVN)
  assert tbt_payload(11, 400, next_turn_type=18).hex() == "4200000f019000f0"  # bear right next (AVN 07:16)
  assert tbt_payload(11, 12700)[:1].hex() == "41"                              # plain straight
  assert tbt_payload(18, 400).hex() == "0d000303019000a0"   # right exit / IC, AVN frame verbatim
  # Tollgates: TG card beyond 1 km, then the gate with a 1 km bar (on request, 남논산 AVN 11-04)
  assert tbt_payload(11, 1500, "북오산톨게이트").hex() == "b200000f000110f5"    # TG card 1.5 km, no bar
  assert tbt_payload(11, 700, "북오산톨게이트").hex() == "b200000f02bc0070"     # gate, bar 7
  assert tbt_payload(11, 300, "북오산톨게이트").hex() == "b200000f012c0030"     # gate, bar 3
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
  msgs = run(hud, nav(fresh=False), 30, start=10)
  assert [d.hex() for a, d, _ in msgs if a == TEXT_ADDR] == ["03f00000aaaaaaaa"]
  assert not any(a == TBT_ADDR for a, _, _ in msgs)


def test_disabled_without_guidance():
  assert run(McanHud(), {}, 20) == []


def test_camera_frames_match_avn():
  assert camera_payload({"type": 1, "distance": 300, "limit": 40}).hex() == "04000000f0000fff"    # 07:17 photo
  assert camera_payload({"type": 1, "distance": 300, "limit": 100}).hex() == "0a000000f0000fff"   # 07:19
  assert camera_payload({"type": 0, "distance": 300, "limit": 50}).hex() == "00500000f0000fff"    # 07:16
  assert camera_payload({"type": 1, "distance": 300, "limit": 40}, 48)[1] == 0x04               # over the limit
  assert camera_payload({"type": 1, "distance": 670, "limit": 60}) is not None                  # as soon as listed
  assert camera_payload({"type": 1, "distance": 0, "limit": 60}) is None                        # passed
  assert camera_payload({"type": 22, "distance": 100, "limit": 30}) is None                     # speed bump


def test_camera_sent_without_turn_guidance():
  state = {"speed_fresh": True, "off_route": False,
           "speed": {"sdi": {"type": 1, "distance_m": 250, "speed_limit_kph": 40}}}
  msgs = run(McanHud(), state, 10)
  assert [d.hex() for a, d, _ in msgs if a == CAMERA_ADDR] == ["04000000f0000fff"] * 2


def test_highway_exit_and_left_fork_match_avn():
  assert tbt_payload(18, 2200, "동탄, 북오산IC").hex() == "a400000f000210f2"  # EXIT sign 2.2 km (AVN)
  assert tbt_payload(18, 900, "동탄, 북오산IC")[:7].hex() == "0d000303038400"  # near: "r" arrow (AVN)
  assert tbt_payload(18, 2200, "")[0] == 13                                   # city keep right
  assert tbt_payload(17, 50).hex() == "0d40012d00320010"                      # left fork 50 m (AVN)


def test_rerouting_shows_avn_reroute_frame_and_clears_text():
  hud = McanHud()
  run(hud, nav(text="노작로"), 10)
  state = {"guidance_fresh": False, "guidance_stream_fresh": True, "off_route": True,
           "turn_type": 13, "distance": 200.0, "text": "노작로"}
  msgs = run(hud, state, 30, start=10)
  assert {d.hex() for a, d, _ in msgs if a == TBT_ADDR} == {"0900000f000500f0"}
  assert [d.hex() for a, d, _ in msgs if a == TEXT_ADDR] == ["03f00000aaaaaaaa"]


def test_bar_follows_announce_distance_like_avn():
  far = 1000
  assert [tbt_payload(13, d, scale_m=far)[7] >> 4 for d in (1200, 900, 800, 400, 290, 80)] == [10, 9, 8, 4, 2, 1]
  assert [bar_percent(d, far) for d in (900, 500, 300, 90)] == [90, 50, 30, 9]   # AVN 0x123 (07:43)
  hud = McanHud()
  run(hud, nav(turn_type=18, dist=500.0, text="동탄, 북오산IC"), 5)
  run(hud, nav(turn_type=13, dist=1200.0, text="병점, 정남"), 5, start=5)        # ramp turn, drawn at once
  msgs = run(hud, nav(turn_type=13, dist=400.0, text="병점, 정남"), 5, start=10)
  assert [d[7] for a, d, _ in msgs if a == BAR_ADDR] == [40]                        # far scale kept
  msgs = run(hud, nav(turn_type=12, dist=300.0, text="수원"), 5, start=15)
  assert [d[7] for a, d, _ in msgs if a == BAR_ADDR] == [100]                       # new maneuver, near


def test_tollgate_card_then_gate_like_avn():
  hud = McanHud()
  far = run(hud, nav(turn_type=11, dist=1500.0, text="북오산톨게이트"), 5)
  assert [d.hex() for a, d, _ in far if a == BAR_ADDR] == ["000000c0ffff0f00"]   # TG card
  near = run(hud, nav(turn_type=11, dist=290.0, text="북오산톨게이트"), 5, start=5)
  assert [d.hex() for a, d, _ in near if a == BAR_ADDR] == ["000000400000001d"]  # gate, 1 km bar


def test_t_junction_and_ramp_turn_shapes_like_avn():
  # The T junction (AVN 0d081024 at 07:44:51) comes from the lanes, not from a 삼거리 name.
  assert tbt_payload(12, 300, "문화센터삼거리")[:4].hex() == "0d081124"
  assert tbt_payload(13, 300, "", after_exit=True)[:4].hex() == "1100000c"      # ramp right (AVN 07:43:44)
  assert tbt_payload(13, 300, "")[:4].hex() == "0d08110c"                       # otherwise crossroads
  hud = McanHud()
  run(hud, nav(turn_type=18, dist=500.0, text="동탄, 북오산IC"), 5)
  msgs = run(hud, nav(turn_type=13, dist=1200.0, text="병점, 정남"), 5, start=5)
  assert [d[:4].hex() for a, d, _ in msgs if a == TBT_ADDR] == ["1100000c"]
  msgs = run(hud, nav(turn_type=12, dist=300.0, text="수원"), 5, start=10)
  assert [d[:4].hex() for a, d, _ in msgs if a == TBT_ADDR] == ["0d081124"]    # next turn: normal again


def test_toll_fee_matches_photo_and_does_not_leak_to_next_guidance():
  from selfdrive.controls.lib.navigation_route import NavigationRouteData
  assert NavigationRouteData.guidance_state(
    {"turn_type": 11, "distance_m": 400, "toll_fee_won": 900}, True)["toll_fee_won"] == 900
  assert NavigationRouteData.guidance_state({"toll_fee_won": 900}, False)["toll_fee_won"] is None
  hud = McanHud()
  state = nav(turn_type=11, dist=400, text="정남톨게이트")
  state["toll_fee_won"] = 900
  msgs = run(hud, state, 5)
  assert [d.hex() for a, d, _ in msgs if a == BAR_ADDR] == ["00000040005a0128"]
  for amount in (None, 0, -10, 901, 655350, True, "nan", "inf", "bad"):
    state["toll_fee_won"] = amount
    msgs = run(hud, state, 5, start=5)
    assert [d.hex() for a, d, _ in msgs if a == BAR_ADDR] == ["0000004000000028"]
  state["toll_fee_won"] = 900
  state["distance"] = 1500
  msgs = run(hud, state, 5, start=10)
  assert [d.hex() for a, d, _ in msgs if a == BAR_ADDR] == ["000000c0ffff0f00"]   # beyond 1 km: TG card
  state.update(turn_type=12, text="수원", distance=210)
  msgs = run(hud, state, 5, start=15)
  assert all(d[:7].hex() == "000000c0ffff0f" for a, d, _ in msgs if a == BAR_ADDR)


def test_tollgate_flag_without_name_or_straight_code():
  from selfdrive.controls.lib.navigation_route import NavigationRouteData
  # TMAP tollgate code 153 or a Naver/Kakao tollgate whose direction text has no "톨게이트".
  assert tbt_payload(153, 300, "서울", tollgate=True).hex() == "b200000f012c0030"
  assert tbt_payload(11, 300, "서울", tollgate=True).hex() == "b200000f012c0030"
  assert tbt_payload(11, 300, "서울")[0] != 0xB2
  assert NavigationRouteData.guidance_state({"turn_type": 153, "distance_m": 300, "tollgate": True}, True)["tollgate"]
  # A turn toward a tollgate (name only, no flag) keeps its turn arrow.
  assert not NavigationRouteData.guidance_state({"turn_type": 13, "distance_m": 300, "main_text": "정남톨게이트"}, True)["tollgate"]
  assert tbt_payload(13, 300, "정남톨게이트")[0] == 13
  assert not NavigationRouteData.guidance_state({"turn_type": 153, "tollgate": True}, False)["tollgate"]
  hud = McanHud()
  state = nav(turn_type=153, dist=300, text="서울")
  state["tollgate"] = True
  state["toll_fee_won"] = 900
  msgs = run(hud, state, 5)
  assert [d.hex() for a, d, _ in msgs if a == TBT_ADDR] == ["b200000f012c0030"]
  assert [d.hex() for a, d, _ in msgs if a == BAR_ADDR] == ["00000040005a011e"]


def test_far_town_turn_keeps_going_with_preview_until_lanes_like_avn():
  hud = McanHud()
  # AVN 11-02 12:08:10: 700 m right turn = keep going with the right preview (67), no bar.
  msgs = run(hud, nav(turn_type=13, dist=700.0, text="북오산IC, 반송초.중학교"), 5)
  assert [d.hex() for a, d, _ in msgs if a == TBT_ADDR] == ["4300000f02bc00f0"]
  assert [d.hex() for a, d, _ in msgs if a == BAR_ADDR] == ["000000c0ffff0f00"]   # AVN 12:08:10
  # 450 m, no lanes yet: still the preview. Left far away: 70 (12:09:29).
  assert run(hud, nav(turn_type=13, dist=450.0, text="북오산IC, 반송초.중학교"), 5, start=5)[0][1][0] == 67
  assert tbt_payload(12, 900, preview=True)[0] == 70
  # The navi's lanes for this turn come up at 400 m: the intersection (12:08:28 0d08110c..a0).
  state = nav(turn_type=13, dist=400.0, text="북오산IC, 반송초.중학교")
  state["lane_current"] = {"distance_m": 405}
  msgs = run(hud, state, 5, start=10)
  assert [d.hex() for a, d, _ in msgs if a == TBT_ADDR] == ["0d08110c019000a0"]
  # Lanes gone again: stays drawn.
  msgs = run(hud, nav(turn_type=13, dist=380.0, text="북오산IC, 반송초.중학교"), 5, start=15)
  assert [d[0] for a, d, _ in msgs if a == TBT_ADDR] == [13]
  # Without lanes a town turn is drawn from 300 m; lanes for another junction do not count.
  hud = McanHud()
  state = nav(turn_type=12, dist=450.0, text="수원")
  state["lane_current"] = {"distance_m": 120}
  assert run(hud, state, 5)[0][1][0] == 70
  assert run(hud, nav(turn_type=12, dist=300.0, text="수원"), 5, start=5)[0][1][0] == 13


def test_junction_shape_from_the_navi_lanes_like_avn():
  # lane "dirs": 1 straight, 2 left, 4 right
  assert lane_side(13, {"dirs": [2, 1, 5]}) == 0x0811        # crossroads (AVN 12:08:28)
  assert lane_side(12, {"dirs": [2, 2, 4]}) == 0x0810        # T junction, no straight lane (07:44)
  assert lane_side(13, {"dirs": [1, 1, 5]}) == 0x0011        # straight + right (AVN 12:20, 12:26)
  assert lane_side(12, {"dirs": [2, 1, 1]}) == 0x0801        # straight + left
  assert lane_side(13, {"dirs": [4, 4]}) is None             # only the turn's own arm: crossroads
  assert lane_side(13, {"dirs": []}) is None and lane_side(13, {"count": 2}) is None
  assert lane_side(14, {"dirs": [8, 1]}) is None             # U-turn keeps the AVN's U-turn picture
  hud = McanHud()
  state = nav(turn_type=13, dist=300.0, text="오산, 용인")
  state["lane_current"] = {"distance_m": 310, "dirs": [1, 1, 5]}
  msgs = run(hud, state, 5)
  assert [d[:4].hex() for a, d, _ in msgs if a == TBT_ADDR] == ["0d00110c"]
  # The lanes disappear near the junction: the shape stays.
  msgs = run(hud, nav(turn_type=13, dist=40.0, text="오산, 용인"), 5, start=5)
  assert [d[:4].hex() for a, d, _ in msgs if a == TBT_ADDR] == ["0d00110c"]
  # Lanes of another junction (distance off) do not shape this turn; a new turn starts over.
  state = nav(turn_type=12, dist=300.0, text="수원")
  state["lane_current"] = {"distance_m": 120, "dirs": [2, 2, 4]}
  msgs = run(hud, state, 5, start=10)
  assert [d[:4].hex() for a, d, _ in msgs if a == TBT_ADDR] == ["0d081124"]
  # After a highway exit the ramp turn keeps its own picture.
  hud = McanHud()
  run(hud, nav(turn_type=18, dist=500.0, text="동탄, 북오산IC"), 5)
  state = nav(turn_type=13, dist=400.0, text="병점, 정남")
  state["lane_current"] = {"distance_m": 400, "dirs": [1, 5]}
  msgs = run(hud, state, 5, start=5)
  assert [d[:4].hex() for a, d, _ in msgs if a == TBT_ADDR] == ["1100000c"]


def test_rear_camera_says_so_on_the_text_line():
  from selfdrive.car.hyundai.mcan_hud import REAR_CAMERA_TEXT
  hud = McanHud()
  state = nav(turn_type=13, dist=200.0, text="북오산IC")
  state.update(speed_fresh=True, speed={"sdi": {"type": 1, "distance_m": 300, "speed_limit_kph": 50, "rear": True}})
  msgs = run(hud, state, 20)
  assert [d.hex() for a, d, _ in msgs if a == CAMERA_ADDR][0] == "05000000f0000fff"   # limit circle as usual
  sent = b"".join(d for a, d, _ in msgs if a == TEXT_ADDR)
  assert REAR_CAMERA_TEXT.encode("utf-16-le")[:4] in sent
  # Past the camera: back to the direction name.
  msgs = run(hud, nav(turn_type=13, dist=150.0, text="북오산IC"), 20, start=20)
  assert "북".encode("utf-16-le") in b"".join(d for a, d, _ in msgs if a == TEXT_ADDR)
  # A front camera keeps the direction name.
  hud = McanHud()
  state["speed"]["sdi"]["rear"] = False
  msgs = run(hud, state, 20)
  assert "북".encode("utf-16-le") in b"".join(d for a, d, _ in msgs if a == TEXT_ADDR)


def test_traffic_info_camera_like_avn():
  hud = McanHud()
  state = {"speed_fresh": True, "speed": {"notice": {"kind": "traffic_info", "distance_m": 250}}}
  msgs = run(hud, state, 5)
  assert [d.hex() for a, d, _ in msgs if a == CAMERA_ADDR] == ["fff00900f00f0fff"]   # AVN 11-02 12:08:09
  # A speed camera wins over the notice; nothing once the notice is gone.
  state["speed"]["sdi"] = {"type": 1, "distance_m": 300, "speed_limit_kph": 50}
  assert [d.hex() for a, d, _ in run(hud, state, 5) if a == CAMERA_ADDR] == ["05000000f0000fff"]
  assert run(hud, {"speed_fresh": True, "speed": {}}, 5) == []
  assert run(hud, {"speed_fresh": True, "speed": {"notice": {"kind": "traffic_info", "distance_m": 0}}}, 5) == []


def test_section_zone_shows_the_limit_like_avn():
  # 2026-11-04 08:15:50 AVN in a 100 section (avg 89, 10 km left): 0x1E5 = fixed 100 the whole zone
  assert section_payload({"distance": 10000, "limit": 100, "average": 89}, 98).hex() == "0a000000f0000fff"
  assert section_payload({"distance": 10000, "limit": 100}, 105)[1] == 0x04   # over the limit
  assert section_payload(None) is None
  state = dict(nav(turn_type=11, dist=11400, text=""), speed_fresh=True,
               speed={"section": {"active": True, "speed_limit_kph": 100, "remaining_distance_m": 10000,
                                  "average_speed_kph": 89}})
  msgs = run(McanHud(), state, 30)
  assert [d.hex() for a, d, _ in msgs if a == CAMERA_ADDR] == ["0a000000f0000fff"] * 6
  assert [d.hex() for a, d, _ in msgs if a == TEXT_ADDR] == [f.hex() for f in isotp_frames(text_payload("구간단속"))]


def test_tollgate_next_after_rest_area_then_gate_from_1km():
  from selfdrive.controls.lib.navigation_route import NavigationRouteData
  # 2026-11-04: Naver current = 이인휴게소 3.5 km, next 47.1 km after it; the AVN showed 풍세톨게이트.
  hud = McanHud()
  state = nav(turn_type=11, dist=3500, text="이인휴게소")
  state["next"] = NavigationRouteData.guidance_state(
    {"turn_type": 11, "distance_m": 47100, "main_text": "풍세톨게이트"}, True)
  msgs = run(hud, state, 30)
  assert [d.hex() for a, d, _ in msgs if a == TBT_ADDR][0] == tbt_payload(11, 50600, "풍세톨게이트").hex()
  assert {d.hex() for a, d, _ in msgs if a == BAR_ADDR} == {"000000c0ffff0f00"}           # TG card
  assert [d.hex() for a, d, _ in msgs if a == TEXT_ADDR] == [f.hex() for f in isotp_frames(text_payload("풍세톨게이트"))]
  # TMAP keeps distance_m from the car; its segment is display_distance_m.
  assert NavigationRouteData.guidance_state({"turn_type": 11, "distance_m": 50600,
                                             "display_distance_m": 47100}, True)["segment"] == 47100
  # A real turn first keeps its own arrow.
  turn = nav(turn_type=13, dist=300, text="수원")
  turn["next"] = state["next"]
  assert [d[0] for a, d, _ in run(McanHud(), turn, 5) if a == TBT_ADDR] == [13]


def test_tollgate_gate_from_1km_with_fee_like_avn():
  # 2026-11-04 08:24 남논산 5,800원 AVN: gate + fee, bar over 1 km (900 m frames verbatim)
  hud = McanHud()
  state = nav(turn_type=11, dist=2400, text="남논산톨게이트")
  state["toll_fee_won"] = 5800
  assert {d.hex() for a, d, _ in run(hud, state, 5) if a == BAR_ADDR} == {"000000c0ffff0f00"}
  state["distance"] = 900
  msgs = run(hud, state, 5, start=5)
  assert [d.hex() for a, d, _ in msgs if a == TBT_ADDR] == ["b200000f03840090"]
  assert [d.hex() for a, d, _ in msgs if a == BAR_ADDR] == ["000000400244015a"]


def test_text_goes_out_after_the_new_maneuver_frame_like_avn():
  # AVN 2026-11-04 08:24:42: 0x115 -> B2 at .832, then 0x4A9 "남논산톨게이트" at .952.
  hud = McanHud()
  run(hud, nav(turn_type=11, dist=5000, text="이인휴게소"), 40)
  for start in range(41, 46):   # any phase of the 5-frame 0x115 cycle
    h = McanHud()
    run(h, nav(turn_type=11, dist=5000, text="이인휴게소"), start)
    msgs = run(h, nav(turn_type=11, dist=2400, text="남논산톨게이트"), 30, start=start)
    order = [a for a, d, _ in msgs if a == TEXT_ADDR or (a == TBT_ADDR and d[0] == 0xB2)]
    assert order[0] == TBT_ADDR and order.count(TEXT_ADDR) == 3


def test_underpass_like_avn():
  # 2026-11-05 12:50 AVN frames on the road Naver drove on 11-07 07:25-07:30
  assert tbt_payload(13, 1500, preview=True, underpass="side_right").hex() == "5400000f000110f5"
  assert tbt_payload(13, 400, underpass="side_right").hex() == "2e00000c019000a0"
  assert tbt_payload(11, 1800, preview=True, underpass="access_straight").hex() == "5b00000f000110f8"
  assert tbt_payload(11, 400, underpass="access_straight").hex() == "2e000024019000a0"
  hud = McanHud()
  state = nav(turn_type=18, dist=1500.0, text="신리IC, 동탄호수공원, 북오산IC")
  state["underpass"] = "side_right"
  assert [d.hex() for a, d, _ in run(hud, state, 5) if a == TBT_ADDR] == ["5400000f000110f5"]
  state["distance"] = 400.0
  assert [d.hex() for a, d, _ in run(hud, state, 5, start=5) if a == TBT_ADDR] == ["2e00000c019000a0"]
  # unknown underpass kinds keep the normal turn
  assert tbt_payload(13, 400, underpass="side_left")[0] == 13


def test_arrived_flag_when_naver_ends_guidance_near_the_goal():
  hud = McanHud()
  run(hud, nav(turn_type=2, dist=60.0, text="목적지"), 50)
  msgs = run(hud, nav(fresh=False), 500, start=50)
  tbt = [d for a, d, _ in msgs if a == TBT_ADDR]
  assert tbt and all(d.hex() == "03000000000000f0" for d in tbt)
  assert len(tbt) == 100  # 5 s at 20 Hz
  # then the AVN's screen again
  assert not any(a == TBT_ADDR for a, _, _ in run(hud, nav(fresh=False), 200, start=550))


def test_no_arrived_flag_when_guidance_ends_far_from_the_goal():
  hud = McanHud()
  run(hud, nav(turn_type=2, dist=900.0, text="목적지"), 50)
  msgs = run(hud, nav(fresh=False), 100, start=50)
  assert not any(a == TBT_ADDR for a, _, _ in msgs)
  hud = McanHud()
  run(hud, nav(turn_type=13, dist=60.0), 50)
  msgs = run(hud, nav(fresh=False), 100, start=50)
  assert not any(a == TBT_ADDR for a, _, _ in msgs)


def test_t_junction_from_lanes_without_a_lane_distance():
  # 2026-11-13 photo: lanes U-turn+left, left, left, right before a right turn, no straight.
  hud = McanHud()
  state = dict(nav(turn_type=13, dist=113.0, text="오산"),
               lane_current={"distance_m": 0, "dirs": [10, 2, 2, 4]})
  tbt = [d for a, d, _ in run(hud, state, 10) if a == TBT_ADDR]
  assert tbt and tbt[-1][1:3] == bytes([0x08, 0x10])
  # far from the turn an undistanced lane list is not trusted: crossroads
  hud = McanHud()
  state = dict(nav(turn_type=13, dist=800.0, text="오산"),
               lane_current={"distance_m": 0, "dirs": [1, 2, 4]})
  run(hud, state, 10)
  assert hud.junction is None
