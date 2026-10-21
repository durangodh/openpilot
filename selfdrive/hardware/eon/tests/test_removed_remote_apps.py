"""Removal of old regular and privileged Bluetooth remote installations."""
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("apk_cleanup", Path(__file__).parents[1] / "apk.py")
apk = importlib.util.module_from_spec(spec)
basedir = ModuleType("common.basedir")
basedir.BASEDIR = "/data/openpilot"
with patch.dict(sys.modules, {"common.basedir": basedir}):
  spec.loader.exec_module(apk)


class TestRemovedRemoteApps(unittest.TestCase):
  def test_regular_apps_uninstalled_and_privileged_apps_disabled(self):
    installed = {
      "ai.comma.eonbleremote": "/system/priv-app/EonBleRemote/EonBleRemote.apk",
      "ai.comma.eonbleremote.probe": "/system/priv-app/EonBleRemoteProbe/EonBleRemoteProbe.apk",
      "ai.comma.eonbtpolicy": "/data/app/ai.comma.eonbtpolicy/base.apk",
      "com.neokii.optool": "/data/app/com.neokii.optool/base.apk",
    }
    with patch.object(apk.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
      apk.remove_remote_apps(installed)
    self.assertEqual(list(installed), ["com.neokii.optool"])
    commands = [call.args[0] for call in run.call_args_list]
    for app in apk.REMOVED_REMOTE_APPS:
      self.assertIn(["am", "force-stop", app], commands)
      self.assertIn(["pm", "disable", app], commands)
    self.assertIn(["pm", "uninstall", "ai.comma.eonbtpolicy"], commands)
    self.assertEqual(len(commands), 7)
    for call in run.call_args_list:
      self.assertEqual(call.kwargs["env"]["LD_LIBRARY_PATH"], "")
      self.assertEqual(call.kwargs["timeout"], 10)

  def test_cleanup_failure_does_not_block_other_apps(self):
    installed = {app: "/data/app/" + app for app in apk.REMOVED_REMOTE_APPS}
    with patch.object(apk.subprocess, "run", side_effect=subprocess.TimeoutExpired("pm", 10)) as run, \
         patch("builtins.print"):
      apk.remove_remote_apps(installed)
    self.assertEqual(run.call_count, 9)
    self.assertFalse(installed)

  def test_absent_apps_do_not_start_android_commands(self):
    with patch.object(apk.subprocess, "run") as run:
      apk.remove_remote_apps({"com.neokii.optool": "/data/app/optool.apk"})
    run.assert_not_called()

  def test_leftover_remote_apk_is_not_reinstalled(self):
    with patch.object(apk, "get_installed_apks", return_value={}), \
         patch.object(apk.glob, "glob", return_value=["/data/openpilot/apk/ai.comma.eonbleremote.apk"]), \
         patch.object(apk, "install_apk") as install:
      apk.update_apks()
    install.assert_not_called()


if __name__ == "__main__":
  unittest.main()
