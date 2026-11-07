"""git pull / reboot requested by the S20 HUD voice assistant (HUDPRM1 op "system").

Rules, enforced here whatever the AI asks:
- Allowed whenever openpilot is not engaged (car off, parked or driving disengaged).
  The check runs when the AI asks and again when the driver confirms with the button.
- The phone only sends the request after the driver pressed the button on the yellow
  confirmation card.
- pull is --ff-only; a reboot after "pull_reboot" happens only when the pull succeeded.
  A new commit is rebuilt on boot automatically (build.py compares prebuilt to HEAD).
- No commits are ever made on the EON.
"""
import os
import subprocess
import threading
import time
import uuid

# common.basedir 와 같은 경로(그 모듈은 cereal 을 불러와 테스트에서 못 쓴다).
BASEDIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

ACTIONS = ("pull", "reboot", "pull_reboot")
PULL_TIMEOUT_S = 90
REBOOT_DELAY_S = 3.0
NATIVE_SUFFIXES = (".cc", ".c", ".h", ".hpp", ".cpp", ".capnp", ".pyx", ".pxd", "SConstruct", "SConscript")


def allowed(sm):
  """(ok, reason): any time openpilot is not engaged (2026-11-07 운전자 결정)."""
  try:
    if sm.alive["controlsState"] and sm["controlsState"].enabled:
      return False, "인게이지 중에는 불가. 오파 해제 후 다시"
    return True, ""
  except Exception as e:
    return False, "상태 확인 실패: %s" % e


def _git(args, timeout=20):
  out = subprocess.run(["git"] + args, cwd=BASEDIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=timeout, check=False)
  return out.returncode, out.stdout.decode("utf-8", "replace").strip()


def _reboot():
  from selfdrive.hardware import HARDWARE
  HARDWARE.reboot(reason="voice")


class SystemJobs:
  def __init__(self, git=_git, reboot=_reboot, clock=time.monotonic):
    self.git, self.reboot, self.clock = git, reboot, clock
    self.lock = threading.Lock()
    self.jobs = {}

  def check(self, action, state):
    if action not in ACTIONS:
      return {"ok": False, "error": "알 수 없는 작업"}
    ok, reason = state
    return {"ok": ok, "error": reason, "action": action}

  def start(self, action, state):
    checked = self.check(action, state)
    if not checked["ok"]:
      return checked
    with self.lock:
      if any(j["running"] for j in self.jobs.values()):
        return {"ok": False, "error": "이미 실행 중"}
      job = uuid.uuid4().hex[:8]
      self.jobs = {job: {"running": True, "action": action, "result": "", "ok": False, "reboot": False}}
    threading.Thread(target=self._run, args=(job, action), name="voice-system", daemon=True).start()
    return {"ok": True, "job": job, "running": True}

  def status(self, job):
    with self.lock:
      j = self.jobs.get(job)
      return dict(j, ok=j["ok"] if not j["running"] else True) if j else {"ok": False, "error": "작업 없음"}

  def _finish(self, job, ok, result, reboot=False):
    with self.lock:
      self.jobs[job].update(running=False, ok=ok, result=result, reboot=reboot)

  def _run(self, job, action):
    try:
      if action in ("pull", "pull_reboot"):
        _, before = self.git(["rev-parse", "HEAD"])
        code, out = self.git(["pull", "--ff-only"], timeout=PULL_TIMEOUT_S)
        if code != 0:
          self._finish(job, False, "pull 실패: " + (out.splitlines()[-1] if out else "오류 %d" % code))
          return
        _, after = self.git(["rev-parse", "HEAD"])
        if before == after:
          summary = "변경 없음"
        else:
          _, count = self.git(["rev-list", "--count", "%s..%s" % (before, after)])
          _, files = self.git(["diff", "--name-only", before, after])
          native = any(f.endswith(NATIVE_SUFFIXES) for f in files.splitlines())
          summary = "새 커밋 %s개 (%s)" % (count or "?", after[:7]) + (" · 재빌드 필요" if native else "")
        if action == "pull":
          self._finish(job, True, "pull 완료: " + summary)
          return
        self._finish(job, True, "pull 완료: %s → %d초 뒤 재부팅" % (summary, REBOOT_DELAY_S), reboot=True)
      else:
        self._finish(job, True, "%d초 뒤 재부팅" % REBOOT_DELAY_S, reboot=True)
      time.sleep(REBOOT_DELAY_S)   # 결과가 HUD 로 갈 시간
      self.reboot()
    except Exception as e:
      self._finish(job, False, "실패: %s" % e)
