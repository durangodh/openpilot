#!/usr/bin/env python3
import math
import numpy as np
from common.numpy_fast import clip, interp

import cereal.messaging as messaging
from cereal import car
from common.conversions import Conversions as CV
from common.filter_simple import FirstOrderFilter
from common.params import Params
from common.realtime import DT_MDL
from selfdrive.modeld.constants import T_IDXS
from selfdrive.controls.lib.longcontrol import LongCtrlState
from selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import LongitudinalMpc, MIN_ACCEL, MAX_ACCEL, N
from selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import T_IDXS as T_IDXS_MPC
from selfdrive.controls.lib.drive_helpers import V_CRUISE_MAX, CONTROL_N
from selfdrive.swaglog import cloudlog
from selfdrive.controls.lib.vision_turn_controller import VisionTurnController, VisionTurnControllerState
from selfdrive.controls.lib.events import Events
# ?? CarrotPilot Auto-Tuner (commit 9dd5e2c port) ??
from selfdrive.controls.lib.carrot_learning import CarrotLearner, read_learned_accel_vals, read_learned_tfollow, read_learned_auto_tr

GearShifter = car.CarState.GearShifter

LON_MPC_STEP = 0.2  # first step is 0.2s
AWARENESS_DECEL = -0.2  # car smoothly decel at .2m/s^2 when user is distracted
A_CRUISE_MIN = -1.0
A_CRUISE_MAX_VALS = [1.8, 1.2, 0.8, 0.6]
A_CRUISE_MAX_BP = [0., 10., 25., 40.]

# ?? MyDrivingMode (1:ECO 2:SAFE 3:NORM 4:FAST) ????????????????????????????
# UI ??紐⑤뱶 諛뺤뒪瑜???븯硫?1???????? 濡??쒗솚?쒕떎 (onroad.cc).
# 媛?쾭?쇱? ?쒖젙 SCC 媛?湲곕뒫 洹몃?濡??먭퀬, 紐⑤뱶??洹??꾩뿉 諛곗쑉濡쒕쭔 ?밸뒗??
#   ACCEL : 理쒕?媛??諛곗쑉 (媛먯냽 ?쒓퀎???덉쟾??嫄대뱶由ъ? ?딆쓬)
#   TF    : 異붿쥌嫄곕━ 諛곗쑉 (GAP1~3 怨좎젙媛?/ GAP4 AUTO 怨≪꽑 紐⑤몢???숈씪 ?곸슜)
#
# GAP4(AUTO) 湲곗? t_follow ??AUTO_TR_V=[1.1, 1.25, 1.35, 1.5] @ [0,30,70,110]km/h
#   ECO  : 1.16 / 1.31 / 1.42 / 1.58
#   SAFE : 1.43 / 1.63 / 1.76 / 1.95
#   NORM : 1.10 / 1.25 / 1.35 / 1.50
#   FAST : 0.97 / 1.10 / 1.19 / 1.32
MY_DRIVING_MODE_ACCEL = {1: 0.75, 2: 0.90, 3: 1.00, 4: 1.25}
MY_DRIVING_MODE_TF    = {1: 1.05, 2: 1.30, 3: 1.00, 4: 0.88}
# ??????????????????????????????????????????????????????????????????????????

# ?? Jerk ease-in (commit d897f06): 媛媛먯냽 onset?먯꽌 jerk瑜??먯쬆?쒖폒 S-curve濡?留뚮뱺????
# ?쇱젙 jerk ?곹븳? onset?먯꽌 jerk媛 0?믪긽?쒖쑝濡?'怨꾨떒'泥섎읆 ???=jounce ?ㅽ뙆?댄겕) ?쒖옉
# jolt瑜??④릿?? ?쒖옉 吏곹썑 jerk瑜??먯쬆?쒗궎硫?媛?띾룄媛 S?먮줈 遺?쒕읇寃?遺숇뒗??
JERK_EASE_TIME  = 0.4   # ??maneuver ?쒖옉 ??jerk瑜?100%濡??ㅼ슦???쒓컙(s)
JERK_EASE_FLOOR = 0.3   # ?쒖옉 ??jerk 鍮꾩쑉 ?섑븳(媛먯냽 onset ??
# (commit dff7287) 媛?띿? ?좏뻾李?異붿쥌 ?ш???嫄곕━ 醫곹엳湲????먮━吏 ?딄쾶 ?쒖옉 jerk瑜?
# ???믨쾶 ?붾떎. (媛먯냽蹂대떎 ?믪? floor ??珥덉쨷諛?媛?띾젰?? ??0?먯꽌 ?쒖옉?섎뒗 ease ?먯껜??
# ?좎???湲됯??띻컧 諛⑹?)
JERK_EASE_FLOOR_ACCEL = 0.55
# (commit dff7287) 媛??ease-out: 媛?띿쓣 留덈Т由ы븯硫??꾩옱 媛??以? ?묒쓽 媛?띿쓣 0
# 洹쇱쿂濡?以꾩씠??援ш컙) 紐⑺몴 李④컙嫄곕━???대ŉ???꾨떖?섎룄濡?遺?쒕윭??jerk瑜??대떎
# (?앸?遺꾩? ??遺?쒕읇寃????ㅼ젣 ?쒕룞???꾨땲誘濡?.
ACCEL_EASEOUT_JERK = 1.2
# 怨좎냽 ?쒕룞 ?덉쟾 ?고쉶: 怨좎냽?먯꽌 ?좏뻾李??묎렐 以묒씠硫?ease瑜????=利됱쓳) 媛먯? 珥덇린遺??
# 異⑸텇???쒕룞??誘몃━ ?ㅼ뼱媛寃??쒕떎(怨좎냽 ??? 媛먯?濡??명븳 異⑸룎 ?곕젮 ???.
HIGH_SPEED_BRAKE_KPH = 70.0
HIGH_SPEED_BRAKE_TTC = 8.0  # ??TTC(珥? ?대궡濡??묎렐 以묒씠硫??쒕룞 ease ?댁젣

# Lookup table for turns
_A_TOTAL_MAX_V = [1.7, 3.2]
_A_TOTAL_MAX_BP = [20., 40.]


def get_max_accel(v_ego):
  return interp(v_ego, A_CRUISE_MAX_BP, A_CRUISE_MAX_VALS)


def limit_accel_in_turns(v_ego, angle_steers, a_target, CP):
  """
  This function returns a limited long acceleration allowed, depending on the existing lateral acceleration
  this should avoid accelerating when losing the target in turns
  """

  # FIXME: This function to calculate lateral accel is incorrect and should use the VehicleModel
  # The lookup table for turns should also be updated if we do this
  a_total_max = interp(v_ego, _A_TOTAL_MAX_BP, _A_TOTAL_MAX_V)
  a_y = v_ego ** 2 * angle_steers * CV.DEG_TO_RAD / (CP.steerRatio * CP.wheelbase)
  a_x_allowed = math.sqrt(max(a_total_max ** 2 - a_y ** 2, 0.))

  return [a_target[0], min(a_target[1], a_x_allowed)]


class LongitudinalPlanner:
  def __init__(self, CP, init_v=0.0, init_a=0.0):
    self.CP = CP
    self.params = Params()
    self.param_read_counter = 0

    self.mpc = LongitudinalMpc()

    # ExperimentalMode enables automatic ACC/e2e selection.  Keep the mode in
    # ACC for normal cruising and temporarily use the blended cost when the
    # model is preparing to leave a model-predicted stop, when a distant stop
    # first needs to be planned, or when a vision-only lead is consistently
    # detected.  This mirrors apilot's TrafficStopMode switching without
    # depending on its vehicle-specific xState state machine.
    self.auto_e2e_enabled = False
    self.auto_e2e_stopping = False
    self.auto_e2e_prepare = False
    self.auto_e2e_vision_lead_count = 0

    # ?? Auto-Tuner: ?숈뒿湲?+ ?숈뒿??媛???뚯씠釉???
    self.carrot_learner = CarrotLearner()
    self.learned_accel_vals = list(A_CRUISE_MAX_VALS)

    # MyDrivingMode
    self.my_driving_mode = 3
    self.my_driving_mode_accel = 1.0

    self.read_param()

    self.fcw = False

    self.a_desired = init_a
    # ?? Jerk ease-in ?곹깭 (commit d897f06) ??
    self._jerk_ramp_t = 0.0   # jerk ease-in 寃쎄낵?쒓컙(??媛媛먯냽 ?쒖옉遺??
    self._jerk_dir = 0        # 吏곸쟾 媛媛먯냽 諛⑺뼢(+1 媛??/ -1 媛먯냽 / 0)
    self.v_desired_filter = FirstOrderFilter(init_v, 2.0, DT_MDL)

    self.v_desired_trajectory = np.zeros(CONTROL_N)
    self.a_desired_trajectory = np.zeros(CONTROL_N)
    self.j_desired_trajectory = np.zeros(CONTROL_N)
    self.solverExecutionTime = 0.0

    self.use_cluster_speed = Params().get_bool('UseClusterSpeed')
    self.cruise_source = 'cruise'
    self.vision_turn_controller = VisionTurnController(CP)
    self.events = Events()

  def read_param(self):
    self.auto_e2e_enabled = self.params.get_bool('ExperimentalMode') and self.CP.openpilotLongitudinalControl
    if not self.auto_e2e_enabled:
      self.mpc.mode = 'acc'
    self.mpc.human_following = self.params.get_bool("HumanFollowing")

    # ?? MyDrivingMode ??
    mode = self.params.get("MyDrivingMode", encoding='utf8')
    try:
      mode = int(mode)
    except (TypeError, ValueError):
      mode = 3
    if not 1 <= mode <= 4:
      mode = 3
    self.my_driving_mode = mode
    self.my_driving_mode_accel = MY_DRIVING_MODE_ACCEL[mode]
    self.mpc.driving_mode_tf = MY_DRIVING_MODE_TF[mode]
    # ???????????????????

    # ?? Auto-Tuner: ?숈뒿???뚮씪誘명꽣瑜?planner/mpc??諛섏쁺 (5珥?二쇨린 媛깆떊) ??
    if self.params.get_bool("CarrotLearningActive"):
      self.learned_accel_vals = read_learned_accel_vals(self.params)
      self.mpc.tfollow_gaps = read_learned_tfollow(self.params)
      self.mpc.auto_tr_values = read_learned_auto_tr(self.params)
    else:
      self.learned_accel_vals = list(A_CRUISE_MAX_VALS)
      self.mpc.tfollow_gaps = None
      self.mpc.auto_tr_values = None

  def update_auto_e2e_mode(self, car_state, radar_state, model_msg):
    if not self.auto_e2e_enabled:
      self.auto_e2e_stopping = False
      self.auto_e2e_prepare = False
      self.auto_e2e_vision_lead_count = 0
      return 'acc'

    model_valid = (len(model_msg.position.x) == 33 and
                   len(model_msg.position.y) == 33 and
                   len(model_msg.velocity.x) == 33)
    if not model_valid:
      self.auto_e2e_stopping = False
      self.auto_e2e_prepare = False
      self.auto_e2e_vision_lead_count = 0
      return 'acc'

    model_x = model_msg.position.x[-1]
    model_y = model_msg.position.y[-1]
    model_v0 = model_msg.velocity.x[0]
    model_v = model_msg.velocity.x[-1]
    v_ego_kph = car_state.vEgo * CV.MS_TO_KPH

    if v_ego_kph < 1.0:
      stop_sign = model_x < 20.0 and model_v < 10.0
    elif v_ego_kph < 80.0:
      stop_sign = model_x < 120.0 and (model_v < 3.0 or model_v < model_v0 * 0.7) and abs(model_y) < 5.0
    else:
      stop_sign = False
    start_sign = not stop_sign and (model_v > 5.0 or model_v > model_v0 + 2.0)

    if self.auto_e2e_stopping and (start_sign or car_state.gasPressed):
      self.auto_e2e_prepare = True
      self.auto_e2e_stopping = False
    if self.auto_e2e_prepare and (v_ego_kph > 5.0 and model_x > 60.0):
      self.auto_e2e_prepare = False
    if stop_sign:
      self.auto_e2e_stopping = True

    lead = radar_state.leadOne
    vision_lead = lead.status and lead.dRel < 90.0 and not lead.radar
    self.auto_e2e_vision_lead_count = self.auto_e2e_vision_lead_count + 1 if vision_lead else 0
    vision_lead_confirmed = self.auto_e2e_vision_lead_count * DT_MDL >= 0.5

    # apilot's default mode blends for a far model stop (>40 m) and while
    # preparing to depart; its experimental mix additionally blends for a
    # stable vision-only lead.
    far_model_stop = stop_sign and model_x > 40.0
    return 'blended' if self.auto_e2e_prepare or far_model_stop or vision_lead_confirmed else 'acc'

  def get_max_accel_learned(self, v_ego):
    return interp(v_ego, A_CRUISE_MAX_BP, self.learned_accel_vals)

  def parse_model(self, model_msg):
    if (len(model_msg.position.x) == 33 and
       len(model_msg.velocity.x) == 33 and
       len(model_msg.acceleration.x) == 33):
      x = np.interp(T_IDXS_MPC, T_IDXS, model_msg.position.x)
      v = np.interp(T_IDXS_MPC, T_IDXS, model_msg.velocity.x)
      a = np.interp(T_IDXS_MPC, T_IDXS, model_msg.acceleration.x)
      j = np.zeros(len(T_IDXS_MPC))
    else:
      x = np.zeros(len(T_IDXS_MPC))
      v = np.zeros(len(T_IDXS_MPC))
      a = np.zeros(len(T_IDXS_MPC))
      j = np.zeros(len(T_IDXS_MPC))
    return x, v, a, j

  def update(self, sm, read=True):
    if self.param_read_counter % 100 == 0 and read:
      self.read_param()
    self.param_read_counter += 1

    v_ego = sm['carState'].vEgo

    self.mpc.mode = self.update_auto_e2e_mode(sm['carState'], sm['radarState'], sm['modelV2'])

    v_cruise_kph = sm['controlsState'].vCruise
    v_cruise_kph = min(v_cruise_kph, V_CRUISE_MAX)
    v_cruise = v_cruise_kph * CV.KPH_TO_MS

    # neokii
    if not self.use_cluster_speed:
      vCluRatio = sm['carState'].vCluRatio
      if vCluRatio > 0.5:
        v_cruise *= vCluRatio
        v_cruise = int(v_cruise * CV.MS_TO_KPH + 0.25) * CV.KPH_TO_MS

    long_control_off = sm['controlsState'].longControlState == LongCtrlState.off
    force_slow_decel = sm['controlsState'].forceDecel

    # Reset current state when not engaged, or user is controlling the speed
    reset_state = long_control_off if self.CP.openpilotLongitudinalControl else not sm['controlsState'].enabled

    # No change cost when user is controlling the speed, or when standstill
    prev_accel_constraint = not (reset_state or sm['carState'].standstill)

    if self.mpc.mode == 'acc':
      # ?? Auto-Tuner: ?숈뒿???띾룄???퀎 理쒕?媛???ъ슜 ??
      accel_limits = [A_CRUISE_MIN, self.get_max_accel_learned(v_ego) * self.my_driving_mode_accel]
      accel_limits_turns = limit_accel_in_turns(v_ego, sm['carState'].steeringAngleDeg, accel_limits, self.CP)
    else:
      accel_limits = [MIN_ACCEL, MAX_ACCEL]
      accel_limits_turns = [MIN_ACCEL, MAX_ACCEL]

    if reset_state:
      self.v_desired_filter.x = v_ego
      # Clip aEgo to cruise limits to prevent large accelerations when becoming active
      self.a_desired = clip(sm['carState'].aEgo, accel_limits[0], accel_limits[1])
      self.mpc.prev_a = np.full(N+1, self.a_desired)  # pid off?뭥n ?꾪솚??constraint ???臾몄젣 諛⑹?
      accel_limits_turns[0] = 0.0  # ?ы솢?깊솕 ??湲됯컧??諛⑹?

    # Prevent divergence, smooth in current v_ego
    self.v_desired_filter.x = max(0.0, self.v_desired_filter.update(v_ego))

    # Get acceleration and active solutions for custom long mpc.
    self.cruise_source, a_min_sol, v_cruise_sol = self.cruise_solutions(not reset_state, self.v_desired_filter.x,
                                                                        self.a_desired, v_cruise, sm)

    if force_slow_decel:
      # if required so, force a smooth deceleration
      accel_limits_turns[1] = min(accel_limits_turns[1], AWARENESS_DECEL)
      accel_limits_turns[0] = min(accel_limits_turns[0], accel_limits_turns[1])
    # clip limits, cannot init MPC outside of bounds
    accel_limits_turns[0] = min(accel_limits_turns[0], self.a_desired + 0.05, a_min_sol)
    accel_limits_turns[1] = max(accel_limits_turns[1], self.a_desired - 0.05)

    self.mpc.set_accel_limits(accel_limits_turns[0], accel_limits_turns[1])
    self.mpc.set_cur_state(self.v_desired_filter.x, self.a_desired)
    x, v, a, j = self.parse_model(sm['modelV2'])
    self.mpc.update(sm['carState'], sm['radarState'], v_cruise_sol, x, v, a, j,
                    prev_accel_constraint=prev_accel_constraint)

    self.v_desired_trajectory = np.interp(T_IDXS[:CONTROL_N], T_IDXS_MPC, self.mpc.v_solution)
    self.a_desired_trajectory = np.interp(T_IDXS[:CONTROL_N], T_IDXS_MPC, self.mpc.a_solution)
    self.j_desired_trajectory = np.interp(T_IDXS[:CONTROL_N], T_IDXS_MPC[:-1], self.mpc.j_solution)

    # TODO counter is only needed because radar is glitchy, remove once radar is gone
    self.fcw = self.mpc.crash_cnt > 2 and not sm['carState'].standstill and not reset_state
    if self.fcw:
      cloudlog.info("FCW triggered")

    # Interpolate 0.05 seconds and save as starting point for next iteration
    a_prev = self.a_desired
    a_target = float(interp(DT_MDL, T_IDXS[:CONTROL_N], self.a_desired_trajectory))
    v_ego_kph = v_ego * CV.MS_TO_KPH

    # ?? Jerk ease-in (commit d897f06): 媛媛먯냽 ?쒖옉 ??jerk瑜??먯쬆(S-curve)?쒖폒 onset jolt ?꾪솕 ??
    # maneuver phase瑜?a_target '遺??濡??먯젙?섍퀬 ?곕뱶諛대뱶(짹0.15)濡?誘몄꽭 吏꾨룞??臾댁떆?쒕떎.
    # 媛?띯넄媛먯냽 '?꾪솚'?먯꽌留?ramp瑜??ъ떆?묓븯誘濡? 吏??媛媛먯냽?먯꽌??jerk媛 100%源뚯? ?먮씪
    # ?쏀빐吏吏 ?딅뒗??異붿쥌 媛???뺤? 媛먯냽???붾럩吏??臾몄젣 ?닿껐).
    if a_target > 0.15:
      phase = 1
    elif a_target < -0.15:
      phase = -1
    else:
      phase = self._jerk_dir   # ?곕뱶諛대뱶: 吏곸쟾 phase ?좎?
    if phase != self._jerk_dir:
      self._jerk_ramp_t = 0.0
    self._jerk_dir = phase
    self._jerk_ramp_t += DT_MDL
    ease = float(np.clip(self._jerk_ramp_t / JERK_EASE_TIME, JERK_EASE_FLOOR, 1.0))

    if a_target > a_prev:
      if a_prev < 0.0:
        # 媛먯냽 ?댁젣(brake release): ?좏뻾李④? ?ㅼ떆 硫?댁쭏 ??怨꾩냽 媛먯냽?섎떎 ?뺤? 吏곸쟾
        # 湲됯??랁븯??臾몄젣瑜?留됯린 ?꾪빐, ?뚯닔 媛?띾룄瑜?0?쇰줈 ?몃뒗 援ш컙? jerk瑜??ш쾶 ?덉슜?쒕떎.
        max_positive_jerk = 3.0
      else:
        # (commit dff7287) 吏꾩쭨 媛??build-up. ?좏뻾李?異붿쥌 ?ш???嫄곕━ 醫곹엳湲????먮━吏
        # ?딅룄濡????jerk瑜??щ━怨?0.6??.85) 媛???꾩슜 ease floor(0.55)濡?珥덉쨷諛?
        # 媛?띾젰???믪씤??湲됯??띻컧? ease濡?諛⑹?). 媛먯냽??怨듭슜 ease媛 ?꾨땲??媛??
        # ?꾩슜 ease_acc(?섑븳 JERK_EASE_FLOOR_ACCEL)瑜??ъ슜?쒕떎.
        jerk_speed = float(np.interp(v_ego_kph, [0.0, 30.0, 80.0], [0.85, 1.15, 1.4]))
        jerk_accel = float(np.interp(a_prev, [0.0, 1.0], [1.0, 0.7]))
        ease_acc = float(np.clip(self._jerk_ramp_t / JERK_EASE_TIME, JERK_EASE_FLOOR_ACCEL, 1.0))
        max_positive_jerk = jerk_speed * jerk_accel * ease_acc
      a_target = min(a_target, a_prev + max_positive_jerk * DT_MDL)
    elif a_target < a_prev:
      if a_prev > 0.1 and a_target > -0.4:
        # (commit dff7287) 媛??ease-out: 媛??以?紐⑺몴 李④컙嫄곕━??媛源뚯썙??媛?띿쓣
        # 0 洹쇱쿂濡?嫄곕몢??援ш컙? ?ㅼ젣 ?쒕룞???꾨땲誘濡?遺?쒕윭??jerk濡??대ŉ??留덈Т由?
        max_negative_jerk = ACCEL_EASEOUT_JERK
      else:
        # ?? ?쒕룞 吏꾩엯(braking build-up) jerk ?쒗븳 (commit 1e95637, dff7287) ??
        # ?좏뻾李?媛먯? ?깆쑝濡?a_target?????ㅽ뀦??湲됯컯?섑븷 ??珥덇린 ?쒕룞??遺?쒕읇寃??섏뿬
        # '釉뚮젅?댄겕瑜???諛잙뒗' ?댁쭏媛먯쓣 ?꾪솕?쒕떎. ?? 紐⑺몴 媛먯냽??源딆쓣?섎줉(湲닿툒) ?쒕룄瑜?
        # ?ㅼ썙 ?덉쟾 ?쒕룞? 洹몃?濡??뺣낫?쒕떎.
        #   -1.2m/s^2(?꾨쭔): 2.0m/s^3 ??0~-1.2源뚯? 0.6s??遺?쒕읇寃?
        #   -2.5m/s^2(媛뺥븿): 5.0m/s^3
        #   -4.0m/s^2(湲닿툒): 12.0m/s^3 ???ъ떎??臾댁젣??0~-4源뚯? 0.33s)
        max_negative_jerk = float(np.interp(a_target, [-4.0, -2.5, -1.2], [12.0, 5.0, 2.0]))
        # ?쒕룞 ease-in 鍮꾩쑉 (commit d897f06): 湲곕낯? 媛?띻낵 媛숈씠 ?먯쬆?섎릺, ?꾧툒?섎㈃ ???=1.0) 利됱쓳.
        ease_dec = ease
        # (commit dff7287) 源딆? 媛먯냽 ease ?댁젣 ????'怨좎냽?먯꽌留?(?띾룄寃뚯씠??25??0kph).
        # ?????5km/h)? ?대룞?먮꼫吏媛 ??븘 源딆? 媛먯냽??遺?쒕읇寃?ease ?좎?)??
        # 湲됰툕?덉씠?뱀쓣 ?꾪솕?쒕떎(30???⑥뼱吏???湲됱젣???꾪솕, ?ㅼ감 異붾룎 ?곕젮??.
        # 怨좎냽 ?묎렐쨌?뺤? 利됱쓳 ?고쉶???꾨옒?먯꽌 洹몃?濡??좎?.
        deep = float(np.interp(a_target, [-3.0, -1.5], [1.0, 0.0]))
        deep *= float(np.interp(v_ego_kph, [25.0, 50.0], [0.0, 1.0]))
        ease_dec = max(ease_dec, deep)
        # (2) 怨좎냽 + ?좏뻾李??묎렐(TTC ??쓬): ??? 媛먯? ?鍮? 媛먯? 珥덇린遺??異⑸텇 ?쒕룞??
        #     誘몃━ ?ㅼ뼱媛?꾨줉 ease瑜??꾩쟾???댁젣?쒕떎(怨좎냽 異⑸룎 ?곕젮 ???.
        if v_ego_kph >= HIGH_SPEED_BRAKE_KPH:
          try:
            lead = sm['radarState'].leadOne
            if lead.status and lead.dRel > 0.0 and lead.vRel < 0.0:
              ttc = lead.dRel / -lead.vRel
              if ttc < HIGH_SPEED_BRAKE_TTC:
                ease_dec = 1.0
          except Exception:
            pass
        # (3) 紐⑤뜽 ?뺤? ?묎렐 ???쒕룞??ease ?놁씠 利됱쓳?쒖폒 ?뺤???珥덇낵瑜?留됰뒗??
        #     (???ы겕??modelV2??action.shouldStop 媛 ?놁쑝硫?except濡?臾댁떆?섏뼱 臾대룞??
        #      ?먮낯 而ㅻ컠??carrot.xState==3(e2eStop) 泥댄겕?????ы겕??carrot 媛앹껜媛
        #      update()濡??꾨떖?섏? ?딆븘 ?????ぉ ?놁쓬 ???ы똿 ???꾩슂?섎㈃ update(sm, carrot)
        #      濡??쒓렇?덉쿂 蹂寃???異붽? 媛??
        try:
          if sm['modelV2'].action.shouldStop:
            ease_dec = 1.0
        except Exception:
          pass
        max_negative_jerk *= ease_dec
      a_target = max(a_target, a_prev - max_negative_jerk * DT_MDL)

    self.a_desired = a_target
    self.v_desired_filter.x = self.v_desired_filter.x + DT_MDL * (self.a_desired + a_prev) / 2.0

    # ?? Auto-Tuner: ?숈뒿 ?곗씠???섏쭛 (commit 9dd5e2c carrot_functions ?듯빀遺 ?ы똿) ??
    # Auto-Tuner??鍮꾪빑???숈뒿 湲곕뒫?대?濡? ?ш린???덉쇅媛 ?섎룄 ?덉쟾?꾩닔
    # 醫낅갑???뚮옒??plannerd)媛 二쎌? ?딅룄濡?諛섎뱶??寃⑸━?쒕떎. (commit e06a7dd robustness)
    try:
      cs = sm['carState']
      lead = sm['radarState'].leadOne
      gear_park = cs.gearShifter == GearShifter.park
      engaged = sm['controlsState'].enabled
      cruise_gap = int(clip(cs.cruiseGap, 1., 4.)) if cs.cruiseGap > 0 else 4
      self.carrot_learner.set_current_gap(cruise_gap)
      # liveParameters.steerRatio (paramsd 移쇰쭔 異붿젙) ??steerRatio ?숈뒿 ?낅젰.
      # plannerd SubMaster??'liveParameters'媛 援щ룆???덉뼱???쒕떎(誘멸뎄??誘몄닔????臾댁떆).
      sr_live, sr_valid = 0.0, False
      try:
        lp = sm['liveParameters']
        sr_live = float(lp.steerRatio)
        sr_valid = bool(getattr(lp, 'valid', True)) and (10.0 <= sr_live <= 20.0)
      except Exception:
        pass
      # ?? Auto-Tuner Phase 6: 鍮꾩쟾 而ㅻ툕 媛먯냽 ?숈뒿 ?낅젰 (VisionTurnController ?곹깭) ??
      # cruise_solutions()?먯꽌 ?대? 留??꾨젅??vision_turn_controller.update()媛
      # ?몄텧?섏뿀?쇰?濡??ш린?쒕뒗 洹?寃곌낵 ?곹깭留??쎈뒗??
      tvc = self.vision_turn_controller
      tvc_entering = tvc.state == VisionTurnControllerState.entering
      tvc_turning = tvc.state == VisionTurnControllerState.turning
      tvc_leaving = tvc.state == VisionTurnControllerState.leaving
      # ?????????????????????????????????????????????????????????????????????
      self.carrot_learner.update(
        v_ego_kph=v_ego * CV.MS_TO_KPH,
        gas_pressed=cs.gasPressed,
        engaged=engaged,
        gear_park=gear_park,
        steer_deg=cs.steeringAngleDeg,
        steer_pressed=cs.steeringPressed,
        brake_pressed=cs.brakePressed,
        lead_drel=lead.dRel if lead.status else 0.0,
        lead_v_kph=lead.vLead * CV.MS_TO_KPH if lead.status else 0.0,
        a_ego=cs.aEgo,
        v_cruise_kph=v_cruise_kph,
        gas_val=cs.gas,
        blinker=(cs.leftBlinker or cs.rightBlinker),
        steer_torque=cs.steeringTorque,
        steer_deg_corr=sm['controlsState'].angleSteers,
        steer_ratio_live=sr_live,
        steer_ratio_valid=sr_valid,
        tvc_entering=tvc_entering,
        tvc_turning=tvc_turning,
        tvc_leaving=tvc_leaving,
        tvc_current_lat_acc=tvc.current_lat_acc,
        tvc_max_pred_lat_acc=tvc.max_pred_lat_acc,
      )
    except Exception:
      cloudlog.exception("CarrotLearner update failed")

  def publish(self, sm, pm):
    plan_send = messaging.new_message('longitudinalPlan')

    plan_send.valid = sm.all_checks(service_list=['carState', 'controlsState'])

    longitudinalPlan = plan_send.longitudinalPlan
    longitudinalPlan.modelMonoTime = sm.logMonoTime['modelV2']
    longitudinalPlan.processingDelay = (plan_send.logMonoTime / 1e9) - sm.logMonoTime['modelV2']

    longitudinalPlan.speeds = self.v_desired_trajectory.tolist()
    longitudinalPlan.accels = self.a_desired_trajectory.tolist()
    longitudinalPlan.jerks = self.j_desired_trajectory.tolist()

    longitudinalPlan.hasLead = sm['radarState'].leadOne.status
    longitudinalPlan.longitudinalPlanSource = self.mpc.source if self.mpc.source != 'cruise' else self.cruise_source
    longitudinalPlan.tFollow = float(self.mpc.t_follow)
    longitudinalPlan.desiredDistance = float(self.mpc.desired_distance)
    longitudinalPlan.visionTurnControllerState = self.vision_turn_controller.state
    longitudinalPlan.visionTurnSpeed = float(self.vision_turn_controller.v_turn)   # m/s, UI vturn ?쒖떆??
    longitudinalPlan.visionCurrentLatAcc = float(self.vision_turn_controller.current_lat_acc)
    longitudinalPlan.visionMaxPredLatAcc = float(self.vision_turn_controller.max_pred_lat_acc)
    longitudinalPlan.eventsDEPRECATED = self.events.to_msg()
    longitudinalPlan.fcw = self.fcw

    longitudinalPlan.solverExecutionTime = self.mpc.solve_time

    pm.send('longitudinalPlan', plan_send)

  def cruise_solutions(self, enabled, v_ego, a_ego, v_cruise, sm):
    # Update controllers
    self.vision_turn_controller.update(enabled, v_ego, a_ego, v_cruise, sm)
    self.events = Events()

    # Pick solution with lowest velocity target.
    a_solutions = {'cruise': float("inf")}
    v_solutions = {'cruise': v_cruise}

    if self.vision_turn_controller.is_active:
      a_solutions['turn'] = self.vision_turn_controller.a_target
      v_solutions['turn'] = self.vision_turn_controller.v_turn

    source = min(v_solutions, key=v_solutions.get)

    return source, a_solutions[source], v_solutions[source]

