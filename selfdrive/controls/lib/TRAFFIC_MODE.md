# Traffic Mode

Port of FrogPilot **Traffic Mode v2**:
https://github.com/FrogAi/FrogPilot/commit/891a81448fce63e2942bdcc282fcff55bfc6e0c6

Enable **TRAFFIC MODE** in the Cruise settings. The persistent parameter is
`TrafficMode`, default **0 (off)**. Planner settings refresh within approximately
5 seconds. This is separate from `TrafficStopMode` (traffic-light/E2E stopping).

When enabled, openpilot longitudinal ACC uses the traffic profile while tracking
a lead within the model horizon, including a closing lead projected to enter it.
The upstream speed tables cover 0–25 m/s; there is no automatic highway cutoff.
The profile replaces the selected GAP's following time while active: the
upstream base range is **0.5–1.197 seconds**, increasing toward 3 seconds when
braking space is needed. It can therefore follow closer than the normal GAP.

## Adaptations for g_remote

- Original acceleration, jerk, following-time and severity tables are retained.
- Acceleration remains capped by the existing CruiseMax, driving-mode and
  cornering limits. Pull-away does not raise these limits.
- The distance calculation uses the configured stop distance, SAFE factor and
  stopped-lead comfort-brake protection. Base danger factor remains g_remote's
  0.8; the upstream dynamic range is 0.7–0.9.
- Following time is seeded from the previous MPC value on activation, then
  follows the upstream rate limits to avoid an immediate gap reduction.
- Traffic jerk multipliers replace C2 dynamic/departure costs and the additional
  following comfort weight. The KRKeegan virtual-distance boost is disabled
  while traffic is active. C2's finite standstill acceleration-change cost stays.
- The minimum-acceleration profile affects the cruise trajectory. The MPC's full
  -4 m/s² braking constraint and both lead obstacles remain unchanged.
- Driver pedal override, invalid/stale inputs, disengagement, lead loss,
  blended/E2E operation and an active traffic-stop/soft-hold reset the profile.
  Existing longitudinal stop-hold/release logic is unchanged.
- HUD, navigation, CAN/Panda safety and generated solver interfaces are unchanged.

## Verification

The profile/planner/recording-MPC tests and existing following, acceleration-limit,
departure-handoff and traffic-departure tests passed (102 tests). The recording
solver checks real MPC inputs and weights; it does not solve a vehicle trajectory.
Python syntax, JSON parsing and git whitespace checks passed.

An additional existing `test_t_follow.py::TestTFollow::test_deceleration_holds_only_gap_reductions`
fails on the unchanged baseline: it expects 1.4 seconds while the existing
rate-limited release returns 1.396. Neither that helper nor its test is changed.
EON native build and physical-vehicle validation have not been performed.
