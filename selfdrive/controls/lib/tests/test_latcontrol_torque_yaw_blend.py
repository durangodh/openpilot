"""LatYawMeasureBlend / LatCurveIDeadzone in the real torque controller (cereal log only)."""
from types import SimpleNamespace as NS

import pytest

from selfdrive.controls.lib import latcontrol_torque as lct


def linear_torque(lat_accel, torque_params, lat_accel_error, deadzone, friction_compensation):
  # Same shape as CarInterfaceBase.torque_from_lateral_accel_linear (friction 0 here);
  # importing car.interfaces needs compiled kalman modules.
  return lat_accel / torque_params.latAccelFactor


class FakeParams:
  def __init__(self, values):
    self.values = values

  def get(self, key, encoding=None):
    return self.values.get(key)


class FakeVM:
  # 1 rad steering wheel angle -> 0.01 1/m, no roll term
  def calc_curvature(self, sa, u, roll):
    return 0.01 * sa

  def get_steer_from_curvature(self, curv, u, roll):
    return curv / 0.01


def make_controller(values):
  torque = NS(kp=1.0, ki=0.1, kf=1.0, friction=0.0, latAccelFactor=2.5, latAccelOffset=0.0,
              useSteeringAngle=True, steeringAngleDeadzoneDeg=0.0)
  cp = NS(steerLimitTimer=4.0, lateralTuning=NS(torque=torque))
  ci = NS(torque_from_lateral_accel=lambda: linear_torque)
  orig = lct.Params
  lct.Params = lambda: FakeParams(values)
  try:
    return lct.LatControlTorque(cp, ci)
  finally:
    lct.Params = orig


def run(values, yaw_curv, v=20.0, angle_deg=0.0, desired=0.0, valid=True):
  ctl = make_controller(values)
  cs = NS(vEgo=v, steeringAngleDeg=angle_deg, steeringPressed=False)
  llk = NS(angularVelocityCalibrated=NS(valid=valid, value=[0.0, 0.0, yaw_curv * v]))
  params = NS(angleOffsetDeg=0.0, roll=0.0)
  _, _, log = ctl.update(True, cs, FakeVM(), params, None, False, desired, 0.0, llk, None)
  return log.actualLateralAccel / v ** 2


def test_default_uses_steering_angle_only():
  assert run({}, yaw_curv=0.002) == pytest.approx(0.0, abs=1e-9)


def test_blend_mixes_gyro_curvature_at_speed():
  assert run({"LatYawMeasureBlend": "50"}, yaw_curv=0.002) == pytest.approx(0.001, rel=1e-3)
  assert run({"LatYawMeasureBlend": "100"}, yaw_curv=0.002) == pytest.approx(0.002, rel=1e-3)


def test_blend_fades_out_at_low_speed_and_ignores_invalid_gyro():
  assert run({"LatYawMeasureBlend": "100"}, yaw_curv=0.002, v=7.0) == pytest.approx(0.0, abs=1e-9)
  assert run({"LatYawMeasureBlend": "100"}, yaw_curv=0.002, valid=False) == pytest.approx(0.0, abs=1e-9)


def test_curve_integrator_deadzone_param():
  assert make_controller({}).curve_i_deadzone == pytest.approx(0.02)
  assert make_controller({"LatCurveIDeadzone": "70"}).curve_i_deadzone == pytest.approx(0.07)
