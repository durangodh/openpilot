# pylint: skip-file
try:
  from common.kalman.simple_kalman_impl import KF1D as KF1D
except ImportError:
  # The Cython build (scons) is missing, e.g. a plain checkout running the unit
  # tests. The pure-Python filter has the same interface and results. On the
  # device the compiled module is always built, so this branch is not taken.
  from common.kalman.simple_kalman_old import KF1D as KF1D
assert KF1D
