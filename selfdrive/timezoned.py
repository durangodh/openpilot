#!/usr/bin/env python3
import json
import os
import time
import subprocess
from typing import NoReturn

from common.params import Params
from selfdrive.hardware import EON, TICI
from selfdrive.swaglog import cloudlog


def get_eon_timezones():
  path = os.path.join(os.path.dirname(__file__), "assets", "timezones.txt")
  with open(path) as f:
    return [line.strip() for line in f if line.strip()]


def set_timezone(valid_timezones, timezone):
  if timezone not in valid_timezones:
    cloudlog.error(f"Timezone not supported {timezone}")
    return False

  cloudlog.debug(f"Setting timezone to {timezone}")
  try:
    if EON:
      # NEOS uses Android properties, not systemd/timedatectl.
      subprocess.check_call(["setprop", "persist.sys.timezone", timezone])
    elif TICI:
      tzpath = os.path.join("/usr/share/zoneinfo/", timezone)
      subprocess.check_call(f'sudo su -c "ln -snf {tzpath} /data/etc/tmptime && \
                              mv /data/etc/tmptime /data/etc/localtime"', shell=True)
      subprocess.check_call(f'sudo su -c "echo \"{timezone}\" > /data/etc/timezone"', shell=True)
    else:
      subprocess.check_call(f'sudo timedatectl set-timezone {timezone}', shell=True)
  except (OSError, subprocess.CalledProcessError):
    cloudlog.exception(f"Error setting timezone to {timezone}")
    return False
  return True


def eon_main(params) -> NoReturn:
  valid_timezones = get_eon_timezones()
  if not params.get("Timezone", encoding='utf8'):
    params.put("Timezone", "Asia/Seoul")

  applied_timezone = None
  while True:
    if params.get_bool("IsOffroad"):
      timezone = params.get("Timezone", encoding='utf8')
      if timezone != applied_timezone and set_timezone(valid_timezones, timezone):
        applied_timezone = timezone
    time.sleep(5)


def main() -> NoReturn:
  params = Params()
  if EON:
    return eon_main(params)

  # EON manual selection needs neither the GPS database nor IP lookups.
  import requests
  from timezonefinder import TimezoneFinder

  tf = TimezoneFinder()

  # Get allowed timezones
  valid_timezones = subprocess.check_output('timedatectl list-timezones', shell=True, encoding='utf8').strip().split('\n')

  while True:
    time.sleep(60)

    is_onroad = not params.get_bool("IsOffroad")
    if is_onroad:
      continue

    # Set based on param
    timezone = params.get("Timezone", encoding='utf8')
    if timezone is not None:
      cloudlog.debug("Setting timezone based on param")
      set_timezone(valid_timezones, timezone)
      continue

    location = params.get("LastGPSPosition", encoding='utf8')

    # Find timezone based on IP geolocation if no gps location is available
    if location is None:
      cloudlog.debug("Setting timezone based on IP lookup")
      try:
        r = requests.get("https://ipapi.co/timezone", timeout=10)
        if r.status_code == 200:
          set_timezone(valid_timezones, r.text)
        else:
          cloudlog.error(f"Unexpected status code from api {r.status_code}")

        time.sleep(3600)  # Don't make too many API requests
      except requests.exceptions.RequestException:
        cloudlog.exception("Error getting timezone based on IP")
        continue

    # Find timezone by reverse geocoding the last known gps location
    else:
      cloudlog.debug("Setting timezone based on GPS location")
      try:
        location = json.loads(location)
      except Exception:
        cloudlog.exception("Error parsing location")
        continue

      timezone = tf.timezone_at(lng=location['longitude'], lat=location['latitude'])
      if timezone is None:
        cloudlog.error(f"No timezone found based on location, {location}")
        continue
      set_timezone(valid_timezones, timezone)


if __name__ == "__main__":
  main()
