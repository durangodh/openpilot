import os
import subprocess
import glob
import hashlib
import shutil
from common.basedir import BASEDIR

REMOVED_REMOTE_APPS = ("ai.comma.eonbleremote", "ai.comma.eonbleremote.probe", "ai.comma.eonbtpolicy")

android_packages = ("com.neokii.optool", )

def get_installed_apks():
  dat = subprocess.check_output(["pm", "list", "packages", "-f"], encoding='utf8').strip().split("\n")
  ret = {}
  for x in dat:
    if x.startswith("package:"):
      v, k = x.split("package:")[1].split("=")
      ret[k] = v
  return ret

def install_apk(path):
  # can only install from world readable path
  install_path = "/sdcard/%s" % os.path.basename(path)
  shutil.copyfile(path, install_path)

  ret = subprocess.call(["pm", "install", "-r", install_path])
  os.remove(install_path)
  return ret == 0

def start_offroad():
  set_package_permissions()
  system("am start -n ai.comma.plus.offroad/.MainActivity")

def set_package_permissions():
  try:
    output = subprocess.check_output(['dumpsys', 'package', 'ai.comma.plus.offroad'], encoding="utf-8")
    given_permissions = output.split("runtime permissions")[1]
  except Exception:
    given_permissions = ""

  wanted_permissions = ["ACCESS_FINE_LOCATION", "READ_PHONE_STATE", "READ_EXTERNAL_STORAGE"]
  for permission in wanted_permissions:
    if permission not in given_permissions:
      pm_grant("ai.comma.plus.offroad", "android.permission."+permission)

  appops_set("ai.comma.plus.offroad", "SU", "allow")
  appops_set("ai.comma.plus.offroad", "WIFI_SCAN", "allow")

def appops_set(package, op, mode):
  system(f"LD_LIBRARY_PATH= appops set {package} {op} {mode}")

def pm_grant(package, permission):
  system(f"pm grant {package} {permission}")

def system(cmd):
  try:
    subprocess.check_output(cmd, stderr=subprocess.STDOUT, shell=True)
  except subprocess.CalledProcessError as e:
    pass

# *** external functions ***

def remove_remote_apps(installed):
  # Upgraded EONs may still have a privileged copy on read-only /system.
  # Stop and disable that copy; uninstall regular /data applications.
  env = os.environ.copy()
  env["LD_LIBRARY_PATH"] = ""
  for app in REMOVED_REMOTE_APPS:
    path = installed.pop(app, None)
    if path is None:
      continue
    commands = [["am", "force-stop", app], ["pm", "disable", app]]
    if not path.startswith("/system/"):
      commands.append(["pm", "uninstall", app])
    for command in commands:
      try:
        result = subprocess.run(command, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=10, check=False)
        if result.returncode:
          print("remote app cleanup failed: %s" % " ".join(command))
      except (OSError, subprocess.TimeoutExpired) as error:
        print("remote app cleanup failed: %s" % error)


def update_apks():
  # install apks
  installed = get_installed_apks()
  remove_remote_apps(installed)

  install_apks = glob.glob(os.path.join(BASEDIR, "apk/*.apk"))
  for apk in install_apks:
    app = os.path.basename(apk)[:-4]
    if app in REMOVED_REMOTE_APPS:
      continue
    if app not in installed:
      installed[app] = None

  #cloudlog.info("installed apks %s" % (str(installed), ))

  for app in installed.keys():
    apk_path = os.path.join(BASEDIR, "apk/"+app+".apk")
    if not os.path.exists(apk_path):
      continue

    h1 = hashlib.sha1(open(apk_path, 'rb').read()).hexdigest()
    h2 = None
    if installed[app] is not None:
      h2 = hashlib.sha1(open(installed[app], 'rb').read()).hexdigest()
      print("comparing version of %s  %s vs %s" % (app, h1, h2))

    if h2 is None or h1 != h2:
      print("installing %s" % app)

      success = install_apk(apk_path)
      if not success:
        print("needing to uninstall %s" % app)
        system("pm uninstall %s" % app)
        success = install_apk(apk_path)

      assert success

def pm_apply_packages(cmd):
  for p in android_packages:
    system("pm %s %s" % (cmd, p))

if __name__ == "__main__":
  update_apks()
