"""Renew the Android R1-only pairing policy without another cereal subscriber."""
import os
import subprocess

SERVICE = 'ai.comma.eonbtpolicy/ai.comma.eonbleremote.BluetoothPolicyService'
RETRY_S = 15.0
LEGACY_RENEW_S = 12.0


class BluetoothPolicy:
  def __init__(self, run=subprocess.run):
    self.run = run
    self.running = False
    self.next_renew = 0.0
    self.target = None
    self.error = ''

  def _am(self, *args):
    env = os.environ.copy()
    env['LD_LIBRARY_PATH'] = ''
    try:
      result = self.run(['/system/bin/am', *args], stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, timeout=8, check=False, env=env)
      output = result.stdout.decode('utf-8', 'replace').strip()
      ok = result.returncode == 0 and 'Error' not in output and 'Exception' not in output
      self.error = '' if ok else output[-160:] or 'Android service command failed'
      return ok
    except (OSError, subprocess.TimeoutExpired) as error:
      self.error = str(error)[-160:]
      return False

  def renew(self, now, selected, force=False, legacy=False):
    """Start the policy service once, again when the R1 changes or `force` (receiver restarted).

    The Android service no longer expires, so there is no periodic `am` call (2026-10-21:
    the 12 s renewals pushed EON CPU to the high 90s every few seconds). A failed start is
    retried after RETRY_S.
    """
    if legacy:
      force = force or now >= self.next_renew  # 예전 APK(18초 임대)가 깔린 동안만 12초 갱신
    if self.running and selected == self.target and not force:
      return
    if not self.running and selected == self.target and now < self.next_renew:
      return
    self.next_renew = now + (LEGACY_RENEW_S if legacy else RETRY_S)
    self.target = selected
    self.running = self._am('startservice', '-n', SERVICE, '--es', 'target', selected)

  def stop(self):
    if self.running:
      self._am('stopservice', '-n', SERVICE)
    self.running = False
    self.next_renew = 0.0
    self.target = None
