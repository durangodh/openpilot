"""Sub-section timers for the controlsd section profiler (controlsd._SectionProf).

Code inside a controlsd section calls

  t = section_prof.start()
  ...
  section_prof.lap("cc_scc", t)

and controlsd adds the totals under the enclosing section in controlsd_prof_MMDD.log.
Off (start() returns 0.0, lap() returns at once) unless controlsd turns it on with the
/tmp/controlsd_prof flag. Remove = delete the lines marked "# _prof".
"""
import time

enabled = False
totals = {}


def start():
  return time.monotonic() if enabled else 0.0


def lap(name, t0):
  """Add the time since t0 to name; returns the current time for the next lap."""
  if not enabled:
    return 0.0
  now = time.monotonic()
  totals[name] = totals.get(name, 0.0) + (now - t0)
  return now


def take():
  global totals
  out, totals = totals, {}
  return out
