#!/usr/bin/env python3
"""Voice git pull / reboot: state gate, ff-only pull, reboot only after a good pull."""
import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from selfdrive.eon_cluster import voice_system  # noqa: E402


class Car:
  def __init__(self, gear="park", standstill=True, v=0.0):
    self.gearShifter, self.standstill, self.vEgo = gear, standstill, v


class SM(dict):
  def __init__(self, started, car=None, enabled=False):
    super().__init__(deviceState=types.SimpleNamespace(started=started), carState=car or Car(),
                     controlsState=types.SimpleNamespace(enabled=enabled))
    self.alive = {"deviceState": True, "carState": car is not None or not started, "controlsState": True}


class FakeGit:
  def __init__(self, pull_code=0, heads=("aaa", "bbb")):
    self.heads = list(heads)
    self.pull_code = pull_code
    self.calls = []

  def __call__(self, args, timeout=20):
    self.calls.append(args[0])
    if args[0] == "rev-parse":
      return 0, self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]
    if args[0] == "pull":
      return self.pull_code, "Fast-forward" if self.pull_code == 0 else "fatal: Not possible to fast-forward"
    if args[0] == "rev-list":
      return 0, "2"
    if args[0] == "diff":
      return 0, "selfdrive/ui/qt/offroad/settings.cc\nselfdrive/x.py"
    return 0, ""


def wait(jobs, job):
  for _ in range(100):
    s = jobs.status(job)
    if not s.get("running"):
      return s
    time.sleep(0.01)
  raise AssertionError("job did not finish")


class AllowedTest(unittest.TestCase):
  def test_states(self):
    self.assertTrue(voice_system.allowed(SM(False))[0])
    self.assertTrue(voice_system.allowed(SM(True, Car()))[0])
    self.assertTrue(voice_system.allowed(SM(True, Car(gear="drive", standstill=False, v=20.0)))[0])
    self.assertFalse(voice_system.allowed(SM(True, Car(), enabled=True))[0])
    self.assertFalse(voice_system.allowed(SM(True, Car(gear="drive", standstill=False, v=20.0), enabled=True))[0])


class JobsTest(unittest.TestCase):
  def setUp(self):
    voice_system.REBOOT_DELAY_S = 0.0
    self.reboots = []

  def test_blocked_state_never_runs(self):
    git = FakeGit()
    jobs = voice_system.SystemJobs(git=git, reboot=lambda: self.reboots.append(1))
    self.assertFalse(jobs.start("pull_reboot", (False, "P단 정차 중에만 가능"))["ok"])
    self.assertEqual(git.calls, [])

  def test_pull_reboot(self):
    jobs = voice_system.SystemJobs(git=FakeGit(), reboot=lambda: self.reboots.append(1))
    s = wait(jobs, jobs.start("pull_reboot", (True, ""))["job"])
    self.assertTrue(s["ok"] and s["reboot"])
    self.assertIn("재빌드 필요", s["result"])
    time.sleep(0.05)
    self.assertEqual(self.reboots, [1])

  def test_failed_pull_does_not_reboot(self):
    jobs = voice_system.SystemJobs(git=FakeGit(pull_code=1), reboot=lambda: self.reboots.append(1))
    s = wait(jobs, jobs.start("pull_reboot", (True, ""))["job"])
    self.assertFalse(s["ok"])
    time.sleep(0.05)
    self.assertEqual(self.reboots, [])

  def test_no_change(self):
    jobs = voice_system.SystemJobs(git=FakeGit(heads=("aaa", "aaa")), reboot=lambda: self.reboots.append(1))
    s = wait(jobs, jobs.start("pull", (True, ""))["job"])
    self.assertEqual(s["result"], "pull 완료: 변경 없음")
    self.assertEqual(self.reboots, [])


if __name__ == "__main__":
  unittest.main()
