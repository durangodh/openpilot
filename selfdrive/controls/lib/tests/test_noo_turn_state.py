"""NOO 회전 상태기계(turnState) 테스트.

DesireHelper.__init__ 은 Params 를 읽으므로 __new__ 로 인스턴스만 만들고
회전 관련 필드만 채워서 _advance_noo_turn 을 직접 돌린다.
"""
from common.realtime import DT_MDL
from selfdrive.controls.lib.desire_helper import DesireHelper


def test_expired_guidance_clears_latched_turn_in_update(tmp_path, monkeypatch):
  import json
  import time
  from types import SimpleNamespace
  from selfdrive.controls.lib import desire_helper
  from selfdrive.controls.lib.navigation_route import NavigationRouteData

  class TestParams:
    def get_bool(self, key):
      return key == "NavigationOnOpenpilot"

    def get(self, key, **kwargs):
      return "1" if key == "NooMode" else "0"

  monkeypatch.setattr(desire_helper, "Params", TestParams)
  monkeypatch.setattr(desire_helper, "RemoteLaneChangeSource",
                      lambda: SimpleNamespace(poll=lambda: 0))
  clock = [100.0]
  monkeypatch.setattr(time, "time", lambda: clock[0])
  monkeypatch.setattr(time, "monotonic", lambda: clock[0])
  path = tmp_path / "guide.json"
  path.write_text(json.dumps({"updated_at_ms": 100000,
                            "guidance_current": {"turn_type": 12, "distance_m": 30}}))
  helper = DesireHelper()
  helper.navigation_route = NavigationRouteData(str(path))
  car = SimpleNamespace(vEgo=5.0, brakePressed=False, steeringPressed=False,
                        steeringTorque=0.0, steeringAngleDeg=0.0,
                        leftBlinker=False, rightBlinker=False,
                        leftBlindspot=False, rightBlindspot=False)
  helper.update(car, True, 0.0)
  assert helper.turn_state == 1
  assert helper.noo_turn_direction == -1
  # Same on-disk payload; the turn latch must not survive the stream timeout.
  clock[0] = 103.1
  helper.update(car, True, 0.0)
  assert helper.turn_state == 0
  assert helper.noo_turn_direction == 0
  assert helper.desire == desire_helper.log.LateralPlan.Desire.none


def machine():
  helper = DesireHelper.__new__(DesireHelper)
  helper.turn_state = 0
  helper.turn_state_timer = 0.0
  helper.turn_direction_latched = 0
  helper.turn_ll_prob = 1.0
  return helper


def frames(seconds):
  return range(int(seconds / DT_MDL))


def test_turn_is_held_while_the_model_has_not_picked_it_up_yet():
  # 큰 교차로: 거리창은 1초 만에 닫히지만 실제 선회는 그 뒤에 시작된다.
  helper = machine()
  for _ in frames(1.0):
    assert helper._advance_noo_turn(1, 0.0) == 1
  for _ in frames(2.0):
    assert helper._advance_noo_turn(0, 0.0) == 1
  assert helper.turn_state == 1


def test_model_pickup_moves_to_state_two_and_probability_ends_the_turn():
  helper = machine()
  helper._advance_noo_turn(1, 0.0)
  assert helper._advance_noo_turn(0, 0.5) == 1
  assert helper.turn_state == 2
  for _ in frames(3.0):
    assert helper._advance_noo_turn(0, 0.6) == 1
  # 확률이 떨어지면 0.5 s 페이드 뒤 해제.
  assert helper._advance_noo_turn(0, 0.0) == 1
  for _ in frames(0.6):
    helper._advance_noo_turn(0, 0.0)
  assert helper.turn_state == 0
  assert helper.turn_direction_latched == 0


def test_state_one_times_out_when_the_model_never_turns():
  helper = machine()
  helper._advance_noo_turn(1, 0.0)
  for _ in frames(DesireHelper.NOO_TURN_ARM_TIMEOUT - 0.2):
    assert helper._advance_noo_turn(0, 0.0) == 1
  for _ in frames(0.5):
    helper._advance_noo_turn(0, 0.0)
  assert helper.turn_state == 0


def test_overall_time_limit_releases_a_stuck_turn():
  helper = machine()
  helper._advance_noo_turn(1, 0.0)
  helper._advance_noo_turn(0, 0.5)
  for _ in frames(DesireHelper.NOO_TURN_MAX_TIME + 0.5):
    helper._advance_noo_turn(0, 0.9)
  assert helper.turn_state == 0


def test_a_new_request_restarts_the_machine_after_release():
  helper = machine()
  helper._advance_noo_turn(1, 0.0)
  helper._reset_noo_turn()
  assert helper._advance_noo_turn(-1, 0.0) == -1
  assert helper.turn_state == 1
  assert helper.turn_direction_latched == -1


def test_turn_hard_cancel_covers_brake_disengagement_and_non_steering_modes():
  check = DesireHelper._noo_turn_hard_cancel
  assert not check(True, 0, True, False, 0)
  assert not check(True, 1, True, False, 0)
  assert check(True, 0, True, True, 0)
  assert check(True, 0, False, False, 0)
  assert check(False, 0, True, False, 0)
  assert check(True, 2, True, False, 0)
  assert check(True, 3, True, False, 0)
  assert check(True, 0, True, False, 0, guidance_fresh=False)
