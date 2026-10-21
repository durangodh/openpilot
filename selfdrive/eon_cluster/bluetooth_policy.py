"""Renew the Android R1-only pairing policy without another cereal subscriber."""
import os
import subprocess

SERVICE = 'ai.comma.eonbtpolicy/ai.comma.eonbleremote.BluetoothPolicyService'


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

  def renew(self, now, selected):
    if now < self.next_renew and selected == self.target:
      return
    # Offset the first renewal from the BLE receiver's 12-second cycle.
    # Both installed Android services expire after 18 seconds without renewal.
    self.next_renew = now + (12.0 if self.running else 6.0)
    self.target = selected
    if self._am('startservice', '-n', SERVICE, '--es', 'target', selected):
      self.running = True

  def stop(self):
    if self.running:
      self._am('stopservice', '-n', SERVICE)
    self.running = False
    self.next_renew = 0.0
    self.target = None
