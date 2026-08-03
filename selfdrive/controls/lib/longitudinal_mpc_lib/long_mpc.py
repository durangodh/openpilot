#!/usr/bin/env python3
import os
import numpy as np

from common.realtime import sec_since_boot
from common.numpy_fast import clip, interp
from selfdrive.swaglog import cloudlog
from selfdrive.modeld.constants import index_function
from selfdrive.controls.lib.radar_helpers import _LEAD_ACCEL_TAU
from common.conversions import Conversions as CV

if __name__ == '__main__':  # generating code
  from third_party.acados.acados_template import AcadosModel, AcadosOcp, AcadosOcpSolver
else:
  from selfdrive.controls.lib.longitudinal_mpc_lib.c_generated_code.acados_ocp_solver_pyx import AcadosOcpSolverCython  # pylint: disable=no-name-in-module, import-error

from casadi import SX, vertcat

MODEL_NAME = 'long'
LONG_MPC_DIR = os.path.dirname(os.path.abspath(__file__))
EXPORT_DIR = os.path.join(LONG_MPC_DIR, "c_generated_code")
JSON_FILE = os.path.join(LONG_MPC_DIR, "acados_ocp_long.json")

SOURCES = ['lead0', 'lead1', 'cruise', 'e2e']

X_DIM = 3
U_DIM = 1
PARAM_DIM = 7
COST_E_DIM = 5
COST_DIM = COST_E_DIM + 1
CONSTR_DIM = 4

X_EGO_OBSTACLE_COST = 3.
X_EGO_COST = 0.
V_EGO_COST = 0.
A_EGO_COST = 0.
J_EGO_COST = 5.0
A_CHANGE_COST = 100.
DANGER_ZONE_COST = 100.
CRASH_DISTANCE = .25
LEAD_DANGER_FACTOR = 0.75
LIMIT_COST = 1e6
ACADOS_SOLVER_TYPE = 'SQP_RTI'


CRUISE_GAP_BP = [1., 2., 3., 4.]
CRUISE_GAP_V = [1.0, 1.4, 2.0, 2.0]
CRUISE_GAP_E2E_V = [1.3, 1.45, 1.6, 1.8]

AUTO_TR_BP = [0., 30.*CV.KPH_TO_MS, 70.*CV.KPH_TO_MS, 110.*CV.KPH_TO_MS]
AUTO_TR_V = [1.1, 1.25, 1.35, 1.5]

AUTO_TR_CRUISE_GAP = 4
DIFF_RADAR_VISION = 1.0


N = 12
MAX_T = 10.0
T_IDXS_LST = [index_function(idx, max_val=MAX_T, max_idx=N) for idx in range(N+1)]

T_IDXS = np.array(T_IDXS_LST)
FCW_IDXS = T_IDXS < 5.0
T_DIFFS = np.diff(T_IDXS, prepend=[0.])
MIN_ACCEL = -3.0
MAX_ACCEL = 2.0
T_FOLLOW = 1.45
COMFORT_BRAKE = 2.6
STOP_DISTANCE = 6.25
STOP_DISTANCE_E2E = 6.0

# ── Lead 부드러운 전환 파라미터 ──────────────────────────────────────────────
LEAD_DETECT_RAMP_T = 1.5   # lead 새 인식 후 영향력 서서히 증가 (초)
LEAD_SMOOTH_ALPHA  = 0.12  # lead 거리/속도 지수이동평균 계수
T_FOLLOW_MAX_RATE  = 0.20  # t_follow 최대 변화속도 (s/s) — rate limiter
# ────────────────────────────────────────────────────────────────────────────

# ── Lever A (commit d897f06): 고속 + 선행차 접근 시 추종거리 선제 확대 ─────────
# 고속 늦은 감지로 인한 충돌 우려 대응. t_follow를 미리 키워 제동 명령을 앞당긴다.
# vRel<0(접근) & TTC<임계 & 고속(≥70km/h) 삼중 게이트라 정속 추종·저속에선 무동작.
# (원본 Lever C=JLeadFactor3 속도연동 증폭은 이 포크에 jLead 신호가 없어 제외)
HIGH_SPEED_BRAKE_KPH = 70.0          # 이 속도(km/h) 이상에서만 선제 확대 적용
HIGH_SPEED_BRAKE_TTC = 7.0           # 접근 TTC(초)가 이 값 미만이면 활성
HIGH_SPEED_TF_BOOST  = 0.3          # t_follow 최대 선제 확대량(초)
# ────────────────────────────────────────────────────────────────────────────

# ── 속도-가변 차간거리 (commit dff7287 포팅, 원본 carrot_functions.py get_T_FOLLOW) ──
# 고정 stop_distance가 저속 time-gap을 부풀려 'time-gap 역전'이 생긴다
# (예: 5-15kph 3.4s vs 45kph+ 1.6s → 저속이 과도하게 넓고 중고속은 좁음). 속도가
# 낮을수록 t_follow를 약간 줄여(≤30 좁게) 저속 간격을 당기고, 높을수록 늘려
# (≥30 넓게) time-gap을 정상화한다 (저속 좁게 / 고속 넓게).
# 원본은 GAP별 tr 산출 함수(get_T_FOLLOW) 내부 clip 이후에 적용해 clip 하한에
# 막히지 않게 했다. 이 포크는 그 함수가 없으므로 update()에서 GAP별 tr(=tr_base)
# 계산 직후, HF/rate-limiter 적용 전에 동일하게 적용한다.
_SPDTF_BP    = [20.0, 32.0, 50.0, 80.0]   # 속도 보간점(km/h)
_SPDTF_DELTA = [-0.20, 0.0, 0.18, 0.28]   # 위 속도에서 t_follow 가감(초)
_SPDTF_MIN   = 0.55                        # 보정 후 t_follow 안전 하한(초)
# ────────────────────────────────────────────────────────────────────────────


def get_stopped_equivalence_factor(v_lead, v_ego=0., t_follow=T_FOLLOW, stop_dist=STOP_DISTANCE, krkeegan=False):
  if not krkeegan:
    return (v_lead**2) / (2 * COMFORT_BRAKE)

  # KRKeegan: lead 거리값을 고의로 늘려 solver가 더 빠른 가속을 유발하도록 함
  v_diff_offset = 0
  if np.all(v_lead - v_ego > 0):
    v_diff_offset = ((v_lead - v_ego) * 1.)
    v_diff_offset = np.clip(v_diff_offset, 0, stop_dist / 2)
    v_diff_offset = np.maximum(v_diff_offset * ((10 - v_ego) / 10), 0)
    low_speed_scale = np.clip(v_ego / 5.0, 0.0, 1.0)
    v_diff_offset *= low_speed_scale

  distance = (v_lead**2) / (2 * COMFORT_BRAKE) + v_diff_offset
  return distance


def get_safe_obstacle_distance(v_ego, t_follow=T_FOLLOW, stop_dist=STOP_DISTANCE):
  return (v_ego**2) / (2 * COMFORT_BRAKE) + t_follow * v_ego + stop_dist

def desired_follow_distance(v_ego, v_lead):
  return get_safe_obstacle_distance(v_ego) - get_stopped_equivalence_factor(v_lead)


def gen_long_model():
  model = AcadosModel()
  model.name = MODEL_NAME

  # set up states & controls
  x_ego = SX.sym('x_ego')
  v_ego = SX.sym('v_ego')
  a_ego = SX.sym('a_ego')
  model.x = vertcat(x_ego, v_ego, a_ego)

  # controls
  j_ego = SX.sym('j_ego')
  model.u = vertcat(j_ego)

  # xdot
  x_ego_dot = SX.sym('x_ego_dot')
  v_ego_dot = SX.sym('v_ego_dot')
  a_ego_dot = SX.sym('a_ego_dot')
  model.xdot = vertcat(x_ego_dot, v_ego_dot, a_ego_dot)

  # live parameters
  a_min = SX.sym('a_min')
  a_max = SX.sym('a_max')
  x_obstacle = SX.sym('x_obstacle')
  prev_a = SX.sym('prev_a')
  lead_t_follow = SX.sym('lead_t_follow')
  lead_danger_factor = SX.sym('lead_danger_factor')
  stop_dist = SX.sym('stop_dist')

  model.p = vertcat(a_min, a_max, x_obstacle, prev_a, lead_t_follow, lead_danger_factor, stop_dist)

  # dynamics model
  f_expl = vertcat(v_ego, a_ego, j_ego)
  model.f_impl_expr = model.xdot - f_expl
  model.f_expl_expr = f_expl
  return model


def gen_long_ocp():
  ocp = AcadosOcp()
  ocp.model = gen_long_model()

  Tf = T_IDXS[-1]

  # set dimensions
  ocp.dims.N = N

  # set cost module
  ocp.cost.cost_type = 'NONLINEAR_LS'
  ocp.cost.cost_type_e = 'NONLINEAR_LS'

  QR = np.zeros((COST_DIM, COST_DIM))
  Q = np.zeros((COST_E_DIM, COST_E_DIM))

  ocp.cost.W = QR
  ocp.cost.W_e = Q

  x_ego, v_ego, a_ego = ocp.model.x[0], ocp.model.x[1], ocp.model.x[2]
  j_ego = ocp.model.u[0]

  a_min, a_max = ocp.model.p[0], ocp.model.p[1]
  x_obstacle = ocp.model.p[2]
  prev_a = ocp.model.p[3]
  lead_t_follow = ocp.model.p[4]
  lead_danger_factor = ocp.model.p[5]
  stop_dist = ocp.model.p[6]

  ocp.cost.yref = np.zeros((COST_DIM, ))
  ocp.cost.yref_e = np.zeros((COST_E_DIM, ))

  desired_dist_comfort = get_safe_obstacle_distance(v_ego, lead_t_follow, stop_dist)

  costs = [((x_obstacle - x_ego) - (desired_dist_comfort)) / (v_ego + 10.),
           x_ego,
           v_ego,
           a_ego,
           a_ego - prev_a,
           j_ego]
  ocp.model.cost_y_expr = vertcat(*costs)
  ocp.model.cost_y_expr_e = vertcat(*costs[:-1])

  constraints = vertcat(v_ego,
                        (a_ego - a_min),
                        (a_max - a_ego),
                        ((x_obstacle - x_ego) - lead_danger_factor * (desired_dist_comfort)) / (v_ego + 10.))
  ocp.model.con_h_expr = constraints

  x0 = np.zeros(X_DIM)
  ocp.constraints.x0 = x0
  ocp.parameter_values = np.array([-1.2, 1.2, 0.0, 0.0, T_FOLLOW, LEAD_DANGER_FACTOR, STOP_DISTANCE])

  cost_weights = np.zeros(CONSTR_DIM)
  ocp.cost.zl = cost_weights
  ocp.cost.Zl = cost_weights
  ocp.cost.Zu = cost_weights
  ocp.cost.zu = cost_weights

  ocp.constraints.lh = np.zeros(CONSTR_DIM)
  ocp.constraints.uh = 1e4*np.ones(CONSTR_DIM)
  ocp.constraints.idxsh = np.arange(CONSTR_DIM)

  ocp.solver_options.qp_solver = 'PARTIAL_CONDENSING_HPIPM'
  ocp.solver_options.hessian_approx = 'GAUSS_NEWTON'
  ocp.solver_options.integrator_type = 'ERK'
  ocp.solver_options.nlp_solver_type = ACADOS_SOLVER_TYPE
  ocp.solver_options.qp_solver_cond_N = 1

  ocp.solver_options.qp_solver_iter_max = 10
  ocp.solver_options.qp_tol = 1e-3

  # set prediction horizon
  ocp.solver_options.tf = Tf
  ocp.solver_options.shooting_nodes = T_IDXS

  ocp.code_export_directory = EXPORT_DIR
  return ocp


class LongitudinalMpc:
  def __init__(self, mode='acc'):
    self.mode = mode
    self.applyLongDynamicCost = True
    self.prev_accel_constraint = True
    self.a_desired = 0.
    self.solver = AcadosOcpSolverCython(MODEL_NAME, ACADOS_SOLVER_TYPE, N)

    self.human_following = False
    self.lead_tracking_prob = 0.0
    self._hf_j_multiplier = 1.0
    self._hf_a_change_multiplier = 1.0
    self._hf_danger_zone_multiplier = 1.0
    self._hf_danger_factor = LEAD_DANGER_FACTOR

    # ── CarrotPilot Auto-Tuner: 학습된 GAP별 추종거리 (초 리스트, None=미사용) ──
    # longitudinal_planner.read_param()에서 5초 주기로 갱신됨.
    self.tfollow_gaps = None
    # ────────────────────────────────────────────────────────────────────

    # ── Lead 인식 부드러운 전환 상태 변수 ──────────────────────────────────
    self._lead_detected   = False
    self._lead_detect_t   = 0.0       # 인식 후 경과 시간 (ramp 용)
    self._lead_d_filt     = 50.0      # 지수평균 필터링된 선행차 거리
    self._lead_v_filt     = 0.0       # 지수평균 필터링된 선행차 속도
    self._t_follow_smooth = T_FOLLOW  # rate-limited t_follow
    self.driving_mode_tf = 1.0        # MyDrivingMode 추종거리 배율 (planner 가 갱신)
    self.desired_distance = 0.0       # UI 표시용 목표 차간거리(m)
    # ────────────────────────────────────────────────────────────────────

    self.reset()
    self.source = SOURCES[2]

  def reset(self):
    self.solver.reset()
    self.v_solution = np.zeros(N+1)
    self.a_solution = np.zeros(N+1)
    self.prev_a = np.array(self.a_solution)
    self.j_solution = np.zeros(N)
    self.yref = np.zeros((N+1, COST_DIM))
    for i in range(N):
      self.solver.cost_set(i, "yref", self.yref[i])
    self.solver.cost_set(N, "yref", self.yref[N][:COST_E_DIM])
    self.x_sol = np.zeros((N+1, X_DIM))
    self.u_sol = np.zeros((N,1))
    self.params = np.zeros((N+1, PARAM_DIM))
    self.t_follow = T_FOLLOW
    self.stop_dist = STOP_DISTANCE
    for i in range(N+1):
      self.solver.set(i, 'x', np.zeros(X_DIM))
    self.last_cloudlog_t = 0
    self.status = False
    self.crash_cnt = 0.0
    self.solution_status = 0
    # timers
    self.solve_time = 0.0
    self.time_qp_solution = 0.0
    self.time_linearization = 0.0
    self.time_integrator = 0.0
    self.x0 = np.zeros(X_DIM)

    # ── Lead 필터 상태 리셋 ────────────────────────────────────────────────
    self._lead_detected   = False
    self._lead_detect_t   = 0.0
    self._lead_d_filt     = 50.0
    self._lead_v_filt     = 0.0
    self._t_follow_smooth = T_FOLLOW
    # ────────────────────────────────────────────────────────────────────

    self.set_weights()

  # ── Human-Like Following: 핵심 오프셋 계산 ──────────────────────────────
  def _apply_human_following(self, lead_distance, v_ego, v_lead, lead_ramp=1.0, t_follow_base=T_FOLLOW):
    """
    frogpilot_following.py 의 update_follow_values() 로직을
    long_mpc 내부 변수(t_follow, params[:,5], cost 배율)에 직접 반영.

    lead_ramp     : 0(새 인식) → 1(안정 추종) — 효과 점진 적용
    t_follow_base : HF 적용 전 base 추종거리. ramp 보간의 기준점.
                    (Auto-Tuner 학습 GAP값 사용 시에도 학습된 base로 수렴하도록
                     상수 T_FOLLOW 대신 base를 기준으로 보간)
    offset 계산을 ** 0.6 거듭제곱으로 완만하게,
    danger/speed 반응도 /150 으로 줄여 부드럽게 처리.

    변수 매핑
      acceleration_jerk / speed_jerk  →  _hf_j_multiplier (J_EGO_COST 배율)
      danger_jerk(미사용)              →  _hf_danger_zone_multiplier
      a_change                        →  _hf_a_change_multiplier (A_CHANGE_COST 배율)
      danger_factor                   →  _hf_danger_factor → params[:,5]
      t_follow                        →  self.t_follow
    """
    # 매 호출 전 초기화
    j_mult        = 1.0
    a_ch_mult     = 1.0
    dz_mult       = 1.0
    danger_factor = LEAD_DANGER_FACTOR

    # ── 빠른 선행차: 자연스럽게 따라붙기 ──
    if v_lead > v_ego:
      distance_factor     = max(lead_distance - (v_ego * self.t_follow), 1)
      raw_offset          = float(np.clip(STOP_DISTANCE - v_ego, 1, distance_factor))
      # ** 0.6 으로 스케일 완화 (급격한 나눗셈 방지)
      accelerating_offset = max(1.0 + (raw_offset - 1.0) ** 0.6, 1.0)

      self.t_follow   /= accelerating_offset
      j_mult          /= accelerating_offset
      a_ch_mult       /= accelerating_offset
      danger_factor   -= (v_lead - v_ego) / 150.0  # 원본 100 → 150: 더 완만하게

    # ── 느린 선행차: 자연스럽게 감속 ──
    if v_lead < v_ego:
      distance_factor = max(lead_distance - (v_lead * self.t_follow), 1)
      raw_offset      = float(np.clip(
        min(v_ego - v_lead, v_lead) - COMFORT_BRAKE, 1, distance_factor))
      # ** 0.6 으로 스케일 완화
      braking_offset  = max(1.0 + (raw_offset - 1.0) ** 0.6, 1.0)

      if lead_distance >= 100:
        far_lead_offset  = max(lead_distance - (v_ego * self.t_follow) - STOP_DISTANCE, 0)
        braking_offset  += far_lead_offset * 0.4  # 원본 1.0 → 0.4

      # 레이더 신뢰도가 높을 때만 적용
      if self.lead_tracking_prob >= 0.9:
        danger_factor  += (v_ego - v_lead) / 150.0  # 원본 100 → 150
        self.t_follow  /= braking_offset

    # t_follow 는 안전 하한선 보장
    self.t_follow = max(self.t_follow, 0.9)

    # ── lead_ramp: 새 인식 직후 HF 효과를 서서히 적용 ────────────────────
    # ramp < 1.0 구간에서는 base 추종거리 방향으로 선형 보간
    # (Auto-Tuner 학습 GAP 사용 시 상수 T_FOLLOW로 끌려가는 출렁임 방지)
    if lead_ramp < 1.0:
      self.t_follow = t_follow_base + (self.t_follow - t_follow_base) * lead_ramp
      danger_factor = LEAD_DANGER_FACTOR + (danger_factor - LEAD_DANGER_FACTOR) * lead_ramp
      j_mult        = 1.0 + (j_mult    - 1.0) * lead_ramp
      a_ch_mult     = 1.0 + (a_ch_mult - 1.0) * lead_ramp
    # ─────────────────────────────────────────────────────────────────────

    # danger_factor 범위 제한 (0.5 ~ 1.1)
    self._hf_danger_factor          = float(np.clip(danger_factor, 0.5, 1.1))
    self._hf_j_multiplier           = float(np.clip(j_mult,    0.05, 2.0))
    self._hf_a_change_multiplier    = float(np.clip(a_ch_mult, 0.05, 2.0))
    self._hf_danger_zone_multiplier = float(np.clip(dz_mult,   0.5,  2.0))
  # ──────────────────────────────────────────────────────────────────────

  def set_cost_weights(self, cost_weights, constraint_cost_weights):
    W = np.asfortranarray(np.diag(cost_weights))
    for i in range(N):
      # reduce the cost on (a-a_prev) later in the horizon.
      W[4,4] = cost_weights[4] * np.interp(T_IDXS[i], [0.0, 1.0, 2.0], [1.0, 1.0, 0.0])
      self.solver.cost_set(i, 'W', W)
    self.solver.cost_set(N, 'W', np.copy(W[:COST_E_DIM, :COST_E_DIM]))

    Zl = np.array(constraint_cost_weights)
    for i in range(N):
      self.solver.cost_set(i, 'Zl', Zl)

  def get_cost_multipliers(self, v_lead0, v_lead1):
    v_ego = self.x0[1]
    v_ego_bps = [0, 10]
    TFs = [1.2, 1.45, 1.8]
    # TF에 의한 a, j, d cost 변경
    a_change_tf = interp(self.t_follow, TFs, [.8, 1., 1.1])
    j_ego_tf    = interp(self.t_follow, TFs, [.8, 1., 1.1])
    d_zone_tf   = interp(self.t_follow, TFs, [1.3, 1., 1.])

    j_ego_v_ego    = 1
    a_change_v_ego = 1
    if (v_lead0 - v_ego >= 0) and (v_lead1 - v_ego >= 0):
      v_ego_bps = [0, 3, 10]
      j_ego_v_ego    = interp(v_ego, v_ego_bps, [0.15, 0.45, 1.0])
      a_change_v_ego = interp(v_ego, v_ego_bps, [0.15, 0.45, 1.0])

    j_ego    = min(j_ego_tf, j_ego_v_ego)
    a_change = min(a_change_tf, a_change_v_ego)
    return (a_change, j_ego, d_zone_tf)

  def set_weights(self, v_ego=0., a_desired=0., prev_accel_constraint=True, v_lead0=0, v_lead1=0):
    self.prev_accel_constraint = prev_accel_constraint
    self.a_desired = a_desired

    if not prev_accel_constraint:
      self.prev_a = np.full(N+1, a_desired)

    if self.mode == 'acc':
      a_change_cost = A_CHANGE_COST if prev_accel_constraint else 0

      if self.applyLongDynamicCost:
        cost_multipliers = self.get_cost_multipliers(v_lead0, v_lead1)

        # ── Human-Like Following: 기존 multiplier에 추가 배율 적용 ──
        j_mult    = cost_multipliers[1] * self._hf_j_multiplier
        a_ch_mult = cost_multipliers[0] * self._hf_a_change_multiplier
        dz_mult   = cost_multipliers[2] * self._hf_danger_zone_multiplier

        cost_weights = [X_EGO_OBSTACLE_COST, X_EGO_COST, V_EGO_COST, A_EGO_COST,
                        a_change_cost * a_ch_mult,
                        J_EGO_COST * j_mult]
        constraint_cost_weights = [LIMIT_COST, LIMIT_COST, LIMIT_COST,
                                   DANGER_ZONE_COST * dz_mult]
        # ────────────────────────────────────────────────────────────────
      else:
        if v_ego < 0.1 or a_desired > 0.:
          x_cost = interp(v_ego, [1., 10.], [0.1, X_EGO_COST])
          v_cost = interp(v_ego, [1., 10.], [0.2, V_EGO_COST])
          a_cost = interp(v_ego, [1., 10.], [5.0, A_EGO_COST])
        else:
          x_cost, v_cost, a_cost = 0., 0., 0.
        cost_weights = [X_EGO_OBSTACLE_COST, x_cost, v_cost, a_cost, a_change_cost, J_EGO_COST]
        constraint_cost_weights = [LIMIT_COST, LIMIT_COST, LIMIT_COST, DANGER_ZONE_COST]

    elif self.mode == 'blended':
      cost_weights = [0., 0.1, 0.2, 5.0, 0.0, 1.0]
      constraint_cost_weights = [LIMIT_COST, LIMIT_COST, LIMIT_COST, 50.0]
    else:
      raise NotImplementedError(f'Planner mode {self.mode} not recognized in planner cost set')
    self.set_cost_weights(cost_weights, constraint_cost_weights)

  def set_cur_state(self, v, a):
    v_prev = self.x0[1]
    self.x0[1] = v
    self.x0[2] = a
    if abs(v_prev - v) > 2.:
      for i in range(0, N+1):
        self.solver.set(i, 'x', self.x0)

  @staticmethod
  def extrapolate_lead(x_lead, v_lead, a_lead, a_lead_tau):
    a_lead_traj = a_lead * np.exp(-a_lead_tau * (T_IDXS**2)/2.)
    v_lead_traj = np.clip(v_lead + np.cumsum(T_DIFFS * a_lead_traj), 0.0, 1e8)
    x_lead_traj = x_lead + np.cumsum(T_DIFFS * v_lead_traj)
    lead_xv = np.column_stack((x_lead_traj, v_lead_traj))
    return lead_xv

  def process_lead(self, lead):
    v_ego = self.x0[1]
    if lead is not None and lead.status:
      x_lead = lead.dRel if lead.radar else max(lead.dRel - DIFF_RADAR_VISION, 0.)
      v_lead = lead.vLead
      a_lead = lead.aLeadK
      a_lead_tau = lead.aLeadTau
    else:
      x_lead = 50.0
      v_lead = v_ego + 10.0
      a_lead = 0.0
      a_lead_tau = _LEAD_ACCEL_TAU

    min_x_lead = ((v_ego + v_lead)/2) * (v_ego - v_lead) / (-MIN_ACCEL * 2)
    x_lead = clip(x_lead, min_x_lead, 1e8)
    v_lead = clip(v_lead, 0.0, 1e8)
    a_lead = clip(a_lead, -10., 5.)
    lead_xv = self.extrapolate_lead(x_lead, v_lead, a_lead, a_lead_tau)
    return lead_xv

  def set_accel_limits(self, min_a, max_a):
    self.cruise_min_a = min_a
    self.max_a = max_a

  def update(self, carstate, radarstate, v_cruise, x, v, a, j, prev_accel_constraint=True):
    # engage 직후에는 직전 가속도 유지 비용(A_CHANGE_COST)을 빼서
    # 필요한 감속으로 곧바로 갈 수 있게 한다. (upstream 동작 복원)
    self.prev_accel_constraint = prev_accel_constraint
    v_ego = self.x0[1]
    self.status = radarstate.leadOne.status or radarstate.leadTwo.status

    # ── Lead 인식 전환 부드럽게 (지수이동평균 + ramp) ──────────────────────
    DT_UPDATE   = 0.05  # update 주기 50 ms
    lead_status = radarstate.leadOne.status

    if lead_status:
      if not self._lead_detected:
        # 새로 인식: 필터 초기값을 현재 raw값으로 세팅 (첫 프레임 튐 방지)
        self._lead_d_filt   = radarstate.leadOne.dRel
        self._lead_v_filt   = radarstate.leadOne.vLead
        self._lead_detect_t = 0.0
        self._lead_detected = True
      # 지수이동평균으로 거리/속도 노이즈 필터링
      self._lead_d_filt = ((1.0 - LEAD_SMOOTH_ALPHA) * self._lead_d_filt
                           + LEAD_SMOOTH_ALPHA * radarstate.leadOne.dRel)
      self._lead_v_filt = ((1.0 - LEAD_SMOOTH_ALPHA) * self._lead_v_filt
                           + LEAD_SMOOTH_ALPHA * radarstate.leadOne.vLead)
      self._lead_detect_t = min(self._lead_detect_t + DT_UPDATE, LEAD_DETECT_RAMP_T)
    else:
      if self._lead_detected:
        self._lead_detected = False
        self._lead_detect_t = 0.0
      # lead 소실 후에도 필터 상태를 서서히 리셋 (갑작스러운 소실 완화)
      self._lead_d_filt = ((1.0 - LEAD_SMOOTH_ALPHA) * self._lead_d_filt
                           + LEAD_SMOOTH_ALPHA * 50.0)
      self._lead_v_filt = ((1.0 - LEAD_SMOOTH_ALPHA) * self._lead_v_filt
                           + LEAD_SMOOTH_ALPHA * (v_ego + 10.0))

    # 0.0(새 인식) → 1.0(안정 추종)
    lead_ramp = self._lead_detect_t / LEAD_DETECT_RAMP_T
    # ─────────────────────────────────────────────────────────────────────

    lead_xv_0 = self.process_lead(radarstate.leadOne)
    lead_xv_1 = self.process_lead(radarstate.leadTwo)

    # neokii
    cruise_gap = int(clip(carstate.cruiseGap, 1., 4.)) if carstate.cruiseGap > 0 else AUTO_TR_CRUISE_GAP
    if self.tfollow_gaps is not None and self.mode == 'acc':
      # ── CarrotPilot Auto-Tuner: 학습된 GAP별 추종거리 사용 ──────────────
      # 학습 활성 시 GAP4(오토)도 학습값(TFollowGap4)으로 고정됩니다.
      tr = interp(float(cruise_gap), CRUISE_GAP_BP, self.tfollow_gaps)
      # ─────────────────────────────────────────────────────────────────
    elif cruise_gap == AUTO_TR_CRUISE_GAP:
      tr = interp(carstate.vEgo, AUTO_TR_BP, AUTO_TR_V) if self.mode == 'acc' else T_FOLLOW
    else:
      tr = interp(float(cruise_gap), CRUISE_GAP_BP, CRUISE_GAP_V if self.mode == 'acc' else CRUISE_GAP_E2E_V)

    # ── MyDrivingMode: GAP(AUTO 포함)으로 정해진 base 추종거리에 모드 배율 적용 ──
    tr *= self.driving_mode_tf
    # ────────────────────────────────────────────────────────────────────

    # ── 속도-가변 차간거리 (commit dff7287 포팅) ────────────────────────────
    # 고정 stop_distance로 인한 저속 time-gap 역전 보정: 저속(≤30km/h)은 t_follow를
    # 줄여 간격을 좁히고, 고속(≥30km/h)은 늘려 넓힌다. GAP/학습값 기반 tr(base) 위에
    # 적용하며, 이후 HF·rate-limiter·Lever A가 이 조정된 base를 그대로 이어받는다.
    # (acc 모드 전용; e2e는 별도 CRUISE_GAP_E2E_V 튜닝값을 쓰므로 대상에서 제외)
    if self.mode == 'acc':
      v_kph = v_ego * CV.MS_TO_KPH
      tr = max(tr + float(interp(v_kph, _SPDTF_BP, _SPDTF_DELTA)), _SPDTF_MIN)
    # ─────────────────────────────────────────────────────────────────────

    self.t_follow = tr
    self.desired_distance = float(tr * v_ego + STOP_DISTANCE)   # UI 표시용
    tr_base = tr  # HF lead_ramp 보간 기준점 (학습된 base + 속도-가변 보정 포함)
    self.stop_dist = STOP_DISTANCE if self.mode == 'acc' else STOP_DISTANCE_E2E

    # ── Human-Like Following: multiplier 초기화 (매 update마다 리셋) ────────
    self._hf_j_multiplier            = 1.0
    self._hf_a_change_multiplier     = 1.0
    self._hf_danger_zone_multiplier  = 1.0
    self._hf_danger_factor           = LEAD_DANGER_FACTOR

    if self.human_following and radarstate.leadOne.status:
      self.lead_tracking_prob = radarstate.leadOne.modelProb
      # 필터링된 거리/속도 + ramp 값 전달
      self._apply_human_following(
        lead_distance = self._lead_d_filt,
        v_ego         = v_ego,
        v_lead        = self._lead_v_filt,
        lead_ramp     = lead_ramp,
        t_follow_base = tr_base,
      )
    # ─────────────────────────────────────────────────────────────────────

    # ── t_follow rate limiter: 급격한 변화 방지 ──────────────────────────
    # (Auto-Tuner 학습값 적용/GAP 전환/속도-가변 보정 점프도 이 limiter가 완만하게 처리)
    t_follow_delta = self.t_follow - self._t_follow_smooth
    max_delta      = T_FOLLOW_MAX_RATE * DT_UPDATE
    self._t_follow_smooth += float(np.clip(t_follow_delta, -max_delta, max_delta))
    self.t_follow = self._t_follow_smooth
    # ─────────────────────────────────────────────────────────────────────

    # ── Lever A (commit d897f06): 고속 + 선행차 접근 → 추종거리 선제 확대 ──────
    # rate limiter '뒤'에 둬서 즉시 반영(고속 접근은 늦으면 안 됨). _t_follow_smooth
    # 에는 누적하지 않아 매 프레임 TTC로 새로 계산되므로, 접근이 해소되면 자동으로 0.
    # 삼중 게이트(고속·접근(vRel<0)·TTC<임계)라 정속 추종·저속에선 무동작 → 평상시 부드러움 유지.
    _lead = radarstate.leadOne
    if _lead.status and v_ego * CV.MS_TO_KPH >= HIGH_SPEED_BRAKE_KPH \
       and _lead.dRel > 0.0 and _lead.vRel < 0.0:
      _ttc = _lead.dRel / -_lead.vRel
      _tf_boost = float(interp(_ttc, [3.0, HIGH_SPEED_BRAKE_TTC], [HIGH_SPEED_TF_BOOST, 0.0]))
      _tf_boost *= float(interp(v_ego * CV.MS_TO_KPH, [HIGH_SPEED_BRAKE_KPH, 110.0], [0.5, 1.0]))
      self.t_follow += _tf_boost
    # ─────────────────────────────────────────────────────────────────────

    # planner에서 저장된 값 + lead 속도 함께 전달
    self.set_weights(v_ego=v_ego,
                     a_desired=self.a_desired,
                     prev_accel_constraint=self.prev_accel_constraint,
                     v_lead0=lead_xv_0[0, 1],
                     v_lead1=lead_xv_1[0, 1])

    lead_0_obstacle = lead_xv_0[:,0] + get_stopped_equivalence_factor(
      lead_xv_0[:,1], self.x_sol[:,1], self.t_follow, self.stop_dist,
      krkeegan=self.applyLongDynamicCost)
    lead_1_obstacle = lead_xv_1[:,0] + get_stopped_equivalence_factor(
      lead_xv_1[:,1], self.x_sol[:,1], self.t_follow, self.stop_dist,
      krkeegan=self.applyLongDynamicCost)

    self.params[:,0] = MIN_ACCEL
    self.params[:,1] = self.max_a

    if self.mode == 'acc':
      # ── Human-Like Following: danger_factor를 params에 반영 ────────────
      self.params[:,5] = self._hf_danger_factor if self.human_following else LEAD_DANGER_FACTOR
      # ─────────────────────────────────────────────────────────────────

      v_lower = v_ego + (T_IDXS * self.cruise_min_a * 1.05)
      v_upper = v_ego + (T_IDXS * self.max_a * 1.05)
      v_cruise_clipped = np.clip(v_cruise * np.ones(N+1),
                                 v_lower,
                                 v_upper)
      cruise_obstacle = np.cumsum(T_DIFFS * v_cruise_clipped) + get_safe_obstacle_distance(v_cruise_clipped, self.t_follow, self.stop_dist)
      x_obstacles = np.column_stack([lead_0_obstacle, lead_1_obstacle, cruise_obstacle])
      self.source = SOURCES[np.argmin(x_obstacles[0])]

      cruise_target = T_IDXS * np.clip(v_cruise, v_ego - 2.0, 1e3) + x[0]
      xforward = ((v[1:] + v[:-1]) / 2) * (T_IDXS[1:] - T_IDXS[:-1])
      x = np.cumsum(np.insert(xforward, 0, x[0]))

      x_and_cruise = np.column_stack([x, cruise_target])
      x = np.min(x_and_cruise, axis=1)

    elif self.mode == 'blended':

      self.params[:,5] = 1.0

      x_obstacles = np.column_stack([lead_0_obstacle,
                                     lead_1_obstacle])
      cruise_target = T_IDXS * np.clip(v_cruise, v_ego - 2.0, 1e3) + x[0]
      xforward = ((v[1:] + v[:-1]) / 2) * (T_IDXS[1:] - T_IDXS[:-1])
      x = np.cumsum(np.insert(xforward, 0, x[0]))

      x_and_cruise = np.column_stack([x, cruise_target])
      x = np.min(x_and_cruise, axis=1)

      self.source = 'e2e' if x_and_cruise[1,0] < x_and_cruise[1,1] else 'cruise'

    else:
      raise NotImplementedError(f'Planner mode {self.mode} not recognized in planner update')

    self.yref[:,1] = x
    self.yref[:,2] = v
    self.yref[:,3] = a
    self.yref[:,5] = j
    for i in range(N):
      self.solver.set(i, "yref", self.yref[i])
    self.solver.set(N, "yref", self.yref[N][:COST_E_DIM])

    self.params[:,2] = np.min(x_obstacles, axis=1)
    self.params[:,3] = np.copy(self.prev_a)
    self.params[:,4] = self.t_follow
    self.params[:,6] = self.stop_dist

    self.run()
    if (np.any(lead_xv_0[FCW_IDXS,0] - self.x_sol[FCW_IDXS,0] < CRASH_DISTANCE) and
            radarstate.leadOne.modelProb > 0.9):
      self.crash_cnt += 1
    else:
      self.crash_cnt = 0

    if self.mode == 'blended':
      if any((lead_0_obstacle - get_safe_obstacle_distance(self.x_sol[:,1], self.t_follow, self.stop_dist)) - self.x_sol[:, 0] < 0.0):
        self.source = 'lead0'
      if any((lead_1_obstacle - get_safe_obstacle_distance(self.x_sol[:,1], self.t_follow, self.stop_dist)) - self.x_sol[:, 0] < 0.0) and \
         (lead_1_obstacle[0] - lead_0_obstacle[0]):
        self.source = 'lead1'


  def run(self):
    for i in range(N+1):
      self.solver.set(i, 'p', self.params[i])
    self.solver.constraints_set(0, "lbx", self.x0)
    self.solver.constraints_set(0, "ubx", self.x0)

    self.solution_status = self.solver.solve()
    self.solve_time = float(self.solver.get_stats('time_tot')[0])
    self.time_qp_solution = float(self.solver.get_stats('time_qp')[0])
    self.time_linearization = float(self.solver.get_stats('time_lin')[0])
    self.time_integrator = float(self.solver.get_stats('time_sim')[0])

    for i in range(N+1):
      self.x_sol[i] = self.solver.get(i, 'x')
    for i in range(N):
      self.u_sol[i] = self.solver.get(i, 'u')

    self.v_solution = self.x_sol[:,1]
    self.a_solution = self.x_sol[:,2]
    self.j_solution = self.u_sol[:,0]

    self.prev_a = np.interp(T_IDXS + 0.05, T_IDXS, self.a_solution)

    t = sec_since_boot()
    if self.solution_status != 0:
      if t > self.last_cloudlog_t + 5.0:
        self.last_cloudlog_t = t
        cloudlog.warning(f"Long mpc reset, solution_status: {self.solution_status}")
      self.reset()


if __name__ == "__main__":
  ocp = gen_long_ocp()
  AcadosOcpSolver.generate(ocp, json_file=JSON_FILE)
  # AcadosOcpSolver.build(ocp.code_export_directory, with_cython=True)
