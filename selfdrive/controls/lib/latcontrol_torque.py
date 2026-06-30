import math

from cereal import log
from common.numpy_fast import interp
from selfdrive.controls.lib.latcontrol import LatControl, MIN_STEER_SPEED
from selfdrive.controls.lib.pid import PIDController
from selfdrive.controls.lib.vehicle_model import ACCELERATION_DUE_TO_GRAVITY
from selfdrive.ntune import nTune
from selfdrive.controls.lib.latcontrol_pid import ERROR_RATE_FRAME

# At higher speeds (25+mph) we can assume:
# Lateral acceleration achieved by a specific car correlates to
# torque applied to the steering rack. It does not correlate to
# wheel slip, or to speed.

# This controller applies torque to achieve desired lateral
# accelerations. To compensate for the low speed effects we
# use a LOW_SPEED_FACTOR in the error. Additionally there is
# friction in the steering wheel that needs to be overcome to
# move it at all, this is compensated for too.

LOW_SPEED_X = [0, 10, 20, 30]
LOW_SPEED_Y = [15, 13, 10, 5]

# ── 긴 커브 안쪽 파고듦 대응 (적분 와인드업 억제) ────────────────────────────
# 원인: steerRatio 과대 등으로 생긴 미세 +오차가 긴 커브 내내 적분에 쌓여 토크가
#       서서히 증가 → 점점 안쪽으로 파고듦. 두 가지로 막는다.
#  (1) 소오차 적분 동결: 추종이 거의 맞은 뒤(잔차 작을 때) 새 누적 정지.
#  (2) 적분 누설(leak): 정상 추종 중에도 쌓인 적분을 시간상수로 흘려보냄(pid.py).
# 둘은 보완적이라 함께 사용한다(freeze=더 안 쌓이게 / leak=쌓인 걸 빼게).
ERR_FREEZE_DEADZONE = 0.05   # 토크 단위(0~1). 실측 보고 0.03~0.08 조정
I_LEAK_FACTOR = 0.999        # 적분 누설 @100Hz → τ≈1.0s. 0.998(τ≈0.5s)~0.9995(τ≈2.0s)


class LatControlTorque(LatControl):
  def __init__(self, CP, CI):
    super().__init__(CP, CI)
    self.torque_params = CP.lateralTuning.torque
    self.pid = PIDController(self.torque_params.kp, self.torque_params.ki,
                             k_f=self.torque_params.kf, pos_limit=self.steer_max, neg_limit=-self.steer_max,
                             i_leak_factor=I_LEAK_FACTOR)
    self.torque_from_lateral_accel = CI.torque_from_lateral_accel()
    self.use_steering_angle = self.torque_params.useSteeringAngle
    self.steering_angle_deadzone_deg = self.torque_params.steeringAngleDeadzoneDeg
    self.tune = nTune(CP, self)
      
  def update_live_torque_params(self, latAccelFactor, latAccelOffset, friction):
    self.torque_params.latAccelFactor = latAccelFactor
    self.torque_params.latAccelOffset = latAccelOffset
    self.torque_params.friction = friction

  def update(self, active, CS, VM, params, last_actuators, steer_limited, desired_curvature, desired_curvature_rate, llk):
    self.tune.updateTorque()
    pid_log = log.ControlsState.LateralTorqueState.new_message()

    if CS.vEgo < MIN_STEER_SPEED or not active:
      output_torque = 0.0
      pid_log.active = False
      angle_steers_des = 0.0
      pid_log.latAccelFactor = self.torque_params.latAccelFactor
      pid_log.latAccelOffset = self.torque_params.latAccelOffset
      pid_log.friction = self.torque_params.friction
    else:
      if self.use_steering_angle:
        actual_curvature = -VM.calc_curvature(math.radians(CS.steeringAngleDeg - params.angleOffsetDeg), CS.vEgo, params.roll)
        curvature_deadzone = abs(VM.calc_curvature(math.radians(self.steering_angle_deadzone_deg), CS.vEgo, 0.0))
      else:
        actual_curvature_vm = -VM.calc_curvature(math.radians(CS.steeringAngleDeg - params.angleOffsetDeg), CS.vEgo, params.roll)
        actual_curvature_llk = llk.angularVelocityCalibrated.value[2] / CS.vEgo
        actual_curvature = interp(CS.vEgo, [2.0, 5.0], [actual_curvature_vm, actual_curvature_llk])
        curvature_deadzone = 0.0
      desired_lateral_accel = desired_curvature * CS.vEgo ** 2

      # desired rate is the desired rate of change in the setpoint, not the absolute desired curvature
      #desired_lateral_jerk = desired_curvature_rate * CS.vEgo ** 2
      actual_lateral_accel = actual_curvature * CS.vEgo ** 2
      lateral_accel_deadzone = curvature_deadzone * CS.vEgo ** 2

      low_speed_factor = interp(CS.vEgo, LOW_SPEED_X, LOW_SPEED_Y)**2
      setpoint = desired_lateral_accel + low_speed_factor * desired_curvature
      measurement = actual_lateral_accel + low_speed_factor * actual_curvature
      error = setpoint - measurement
      gravity_adjusted_lateral_accel = desired_lateral_accel - params.roll * ACCELERATION_DUE_TO_GRAVITY
      pid_log.error = self.torque_from_lateral_accel(error, self.torque_params, error,
                                                     lateral_accel_deadzone, friction_compensation=False)
      ff = self.torque_from_lateral_accel(gravity_adjusted_lateral_accel, self.torque_params,
                                          desired_lateral_accel - actual_lateral_accel,
                                          lateral_accel_deadzone, friction_compensation=True)

      # 소오차 구간 적분 동결(anti-windup): 추종이 거의 맞은 뒤에도 미세 +오차가
      # 긴 커브 내내 적분에 쌓여 서서히 안쪽으로 파고드는 현상을 차단한다.
      # error 는 횡가속도 오차의 토크 환산값(pid_log.error). 작은 잔차는 새 누적 정지.
      # (이미 쌓인 적분은 pid.py 의 i_leak_factor 로 계속 흘러나간다)
      low_error = abs(pid_log.error) < ERR_FREEZE_DEADZONE
      freeze_integrator = steer_limited or CS.steeringPressed or CS.vEgo < 5 or low_error
      output_torque = self.pid.update(pid_log.error,
                                      feedforward=ff,
                                      speed=CS.vEgo,
                                      freeze_integrator=freeze_integrator)

      pid_log.active = True
      pid_log.p = self.pid.p
      pid_log.i = self.pid.i
      pid_log.d = self.pid.d
      pid_log.f = self.pid.f
      pid_log.output = -output_torque
      pid_log.actualLateralAccel = actual_lateral_accel
      pid_log.desiredLateralAccel = desired_lateral_accel
      pid_log.saturated = self._check_saturation(self.steer_max - abs(output_torque) < 1e-3, CS, steer_limited)

      pid_log.latAccelFactor = self.torque_params.latAccelFactor
      pid_log.latAccelOffset = self.torque_params.latAccelOffset
      pid_log.friction = self.torque_params.friction

      angle_steers_des = math.degrees(VM.get_steer_from_curvature(-desired_curvature, CS.vEgo, params.roll)) + params.angleOffsetDeg

    #TODO left is positive in this convention
    return -output_torque, angle_steers_des, pid_log
