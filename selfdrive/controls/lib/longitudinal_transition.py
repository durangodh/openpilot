# Limit only acceleration release after a stop or MPC mode handoff. Stronger
# braking is always passed through immediately.
ACCEL_TRANSITION_JERK_UP = 1.5  # m/s^3
ACCEL_MODE_TRANSITION_TIME = 1.5
ACCEL_LAUNCH_TRANSITION_TIME = 2.0


def limit_accel_increase(output_accel, last_output_accel, transition_time, dt):
  remaining = max(0.0, transition_time - dt)
  if transition_time > 0.0 and output_accel > last_output_accel:
    output_accel = min(output_accel,
                       last_output_accel + ACCEL_TRANSITION_JERK_UP * dt)
  return output_accel, remaining
