#!/usr/bin/env python3
import datetime
import os
import signal
import sys
import traceback
from multiprocessing import Process
from typing import List, Tuple, Union

import cereal.messaging as messaging
import selfdrive.sentry as sentry
from common.basedir import BASEDIR
from common.params import Params, ParamKeyType
from common.text_window import TextWindow
from selfdrive.boardd.set_time import set_time
from selfdrive.hardware import HARDWARE, PC, EON
from selfdrive.manager.helpers import unblock_stdout
from selfdrive.manager.process import ensure_running, launcher
from selfdrive.manager.process_config import managed_processes
from selfdrive.athena.registration import register, UNREGISTERED_DONGLE_ID
from selfdrive.swaglog import cloudlog, add_file_handler
from selfdrive.version import is_dirty, get_commit, get_version, get_origin, get_short_branch, \
                              terms_version, training_version
from selfdrive.hardware.eon.apk import system

sys.path.append(os.path.join(BASEDIR, "pyextra"))


def manager_init() -> None:
  # update system time from panda
  set_time(cloudlog)

  params = Params()
  params.clear_all(ParamKeyType.CLEAR_ON_MANAGER_START)

  default_params: List[Tuple[str, Union[str, bytes]]] = [
    ("CompletedTrainingVersion", "0"),
    ("DisengageOnAccelerator", "0"),
    ("HasAcceptedTerms", "0"),
    ("OpenpilotEnabledToggle", "1"),
    ("IsMetric", "1"),

    # HKG
    ("AutoGasTokSpeed", "30"),
    ("AutoGasCancelSpeed", "30"),
    ("AutoGasTapSpeedIncrement", "5"),
    ("SpeedFromPCM", "2"),
    ("AutoSpeedUptoRoadSpeedLimit", "0"),
    ("AutoRoadSpeedAdjust", "0"),          # -100=limit immediately, 0=retain, 1..100=blend on limit drop
    ("AutoRoadSpeedLimitOffset", "0"),
    ("AutoNaviSpeedCtrlEnd", "7"),
    ("AutoNaviSpeedBumpTime", "1"),
    ("AutoNaviSpeedBumpSpeed", "35"),
    ("AutoNaviSpeedSafetyFactor", "105"),
    ("AutoNaviSectionBoost", "10"),        # 구간단속 평균 미달 시 보상 가속 상한 km/h, 0=off
    ("NavigationOnOpenpilot", "0"),
    ("NooTurnSpeed", "30"),
    ("NooMode", "0"),
    ("EonClusterHudTmapIcon", "0"),
    ("NooTurnEndTime", "6"),
    ("NooLaneChangeSpeed", "30"),
    ("CruiseButtonMode", "0"),              # 0=normal, 1/2=custom, 3=speed table
    ("CruiseSpeedUnit", "10"),
    ("CruiseSpeedUnitBasic", "1"),
    ("CruiseButtonLongDelay", "40"),       # C2 long-press threshold: 0.40 s
    ("CruiseSpeed1", "30"),
    ("CruiseSpeed2", "50"),
    ("CruiseSpeed3", "70"),
    ("CruiseSpeed4", "90"),
    ("CruiseSpeed5", "110"),
    ("AutoGasResumeGuard", "1"),            # 가속페달 재개 안전조건   # 도로제한속도 대비 자동증속 상한(%), 0=off
    ("AutoResumeFromGas", "1"),              # 0=off, 1=hold, 2=hold+quick release
    ("AutoResumeFromGasSpeedMode", "0"),     # 0=current, 1=previous, 2=previous with lead
    ("AutoResumeFromBrakeRelease", "0"),     # opt-in for safety
    ("AutoResumeFromBrakeCarSpeed", "30"),
    ("AutoResumeFromBrakeReleaseDist", "10"),
    ("UseClusterSpeed", "0"),
    ("LongControlEnabled", "0"),
    ("SccConnectedBus2", "0"),
    ("MadModeEnabled", "1"),
    ("CruiseSpeedMin", "30"),
    ("LaneChangeEnabled", "0"),
    ("AutoLaneChangeEnabled", "0"),
    ("ExperimentalMode", "0"),
    ("TrafficStopMode", "2"),
    ("MixRadarInfo", "0"),
    ("LongTraceEnabled", "0"),
    ("PathOffset", "0"),
    ("UseLaneLineSpeed", "0"),
    ("PathCostApply", "100"),
    ("MaxAngleFrames", "89"),

    ("SccSmootherSyncGasPressed", "0"),
    ("HapticFeedbackWhenSpeedCamera", "0"),
    ("WideCameraOnly", "0"),       # plannerd.py 크래시 수정
    ("ShowGearAnimation", "1"),
    ("ShowCarrotHud", "1"),
    ("EonClusterHud", "0"),
    ("BtRemoteEnabled", "0"),
    ("EonClusterHudRoadZ", "100"),
    ("EonClusterHudPitchDyn", "60"),
    ("EonClusterHudViewPitch", "0"),
    ("EonClusterHudFps", "10"),
    ("EonClusterHudMapFps", "3"),
    ("EonClusterHudBrightness", "0"),
    ("EonClusterHudDayBrightness", "65"),
    ("EonClusterHudNightBrightness", "35"),
    ("EonClusterHudJpegQuality", "55"),
    ("EonClusterHudLayoutMode", "1"),
    ("EonClusterHudNaviLayout", "0"),
    ("EonClusterHudMapScaleTmap", "70"),
    ("EonClusterHudMapScaleKakao", "70"),
    ("EonClusterHudMapScaleNaver", "70"),
    ("EonClusterHudNavApp", "1"),
    ("EonClusterHudScreenMode", "1"),
    ("EonClusterHudInfoBox", "1"),
    ("EonClusterHudTheme", "0"),
    ("EonClusterHudOrientation", "0"),
    ("EonClusterHudMirror", "0"),
    ("EonClusterHudPathFlip", "0"),
    ("EonClusterHudLanguage", "0"),
    ("EonClusterHudRadarInfo", "4"),
    ("EonClusterHudGuardrail", "1"),
    ("EonClusterHudHaze", "55"),
    ("ShowMapboxMap", "1"),
    ("ShowRouteMapAlways", "0"),
    ("ShowPathWidth", "90"),
    ("ShowPathStatusColor", "1"),
    ("PrevCruiseGap", "4"),
    ("ApplyLongDynamicCost", "0"),
    ("HumanAcceleration", "0"),
    ("MyDrivingMode", "3"),
    ("InitMyDrivingMode", "3"),
    ("MyEcoModeFactor", "80"),
    ("MySafeModeFactor", "80"),
    ("CruiseMaxVals1", "160"),
    ("CruiseMaxVals2", "120"),
    ("CruiseMaxVals3", "100"),
    ("CruiseMaxVals4", "80"),
    ("CruiseMaxVals5", "70"),
    ("CruiseMaxVals6", "60"),
    ("NoLeadCruiseAccelFactor", "100"),
    ("LeadCruiseAccelFactor", "100"),
    ("LiveSteerRatioApply", "100"),
    ("SteerRatioApply", "0"),
    ("SteerActuatorDelay", "30"),
    ("SteeringRateCost", "700"),
    ("LateralMotionCost", "11"),
    ("LateralAccelCost", "0"),
    ("LateralJerkCost", "5"),
    ("LateralTorqueCustom", "0"),
    ("LateralTorqueAccelFactor", "2500"),
    ("LateralTorqueFriction", "100"),
    ("SteerDeltaUp", "3"),
    ("SteerDeltaDown", "7"),
    ("AutoLaneChangeTimer", "0"),  # controlsd.py 크래시 수정
    ("AutoLaneChangeSpeed", "50"),  # 자동/방향지시등 차선변경 허용 최저 속도 (km/h)
    ("TurnVisionControl", "0"),
    ("AutoCurveSpeedFactor", "120"),
    ("AutoCurveSpeedLowerLimit", "30"),
    ("MapTurnSpeedFactor", "90"),
    ("AutoNaviSpeedDecelRate", "80"),
    ("JerkStartLimit", "10"),
    ("StartAccelApply", "0"),
    ("StopAccelApply", "30"),
    ("StoppingDecelRate", "120"),
    ("LowSpeedJerkBoost", "100"),
    ("StandstillHoldApply", "55"),
    ("EarlyHoldRelax", "1"),
    ("FastLeadRelease", "1"),
    ("TrafficStopAccel", "80"),
    ("TrafficStopDistanceAdjust", "400"),
    ("StopDistance", "600"),
    ("LongTuningKpV", "100"),
    ("LongTuningKiV", "200"),
    ("LongTuningKf", "100"),
    ("LongitudinalActuatorDelayLowerBound", "50"),
    ("SoftHoldMode", "1"),
    ("McanHudTbt", "0"),
  ]
  if not PC:
    default_params.append(("LastUpdateTime", datetime.datetime.utcnow().isoformat().encode('utf8')))

  if params.get_bool("RecordFrontLock"):
    params.put_bool("RecordFront", True)

  # Preserve the driver's previous navigation values once, then run only the
  # unified NOO parameters. Legacy files are read directly because their keys
  # are intentionally no longer registered.
  if params.get("NooUnifiedMigrated") is None:
    legacy_dir = "/data/params/d"
    try:
      legacy_modes = []
      for filename in ("CarrotAutoTurnControl", "AutoTurnControl"):
        try:
          legacy_modes.append(int(open(os.path.join(legacy_dir, filename)).read().strip()))
        except (IOError, OSError, TypeError, ValueError):
          pass
      if any(mode > 0 for mode in legacy_modes):
        params.put_bool("NavigationOnOpenpilot", True)

      for old_name, new_name, lower, upper in (
          ("CarrotAutoTurnSpeed", "NooTurnSpeed", 30, 60),
          ("CarrotAutoTurnEndTime", "NooTurnEndTime", 2, 12)):
        try:
          value = int(open(os.path.join(legacy_dir, old_name)).read().strip())
          params.put(new_name, str(max(lower, min(upper, value))))
        except (IOError, OSError, TypeError, ValueError):
          pass
    finally:
      params.put_bool("NooUnifiedMigrated", True)

  # The old HUD schema stored 65 as the default fixed brightness. When the
  # day/night controls first appear, migrate only that legacy default to auto.
  # Other fixed values are user choices and remain unchanged.
  if (params.get("EonClusterHudDayBrightness") is None and
      params.get("EonClusterHudNightBrightness") is None and
      params.get("EonClusterHudBrightness") == b"65"):
    params.put("EonClusterHudBrightness", "0")

  # Migrate the legacy stored 7 FPS value once. Preserve 0 (paused) and every
  # non-legacy value the driver selected.
  if params.get("EonClusterHudGeometryV2Migrated") is None:
    if params.get("EonClusterHudFps", encoding="utf8") in (None, "7"):
      params.put("EonClusterHudFps", "10")
    params.put_bool("EonClusterHudGeometryV2Migrated", True)

  # aPilot PathOffset is an integer centimetre value. Preserve the previous
  # common OffsetTotal calibration (metres) once before defaults are created.
  if params.get("PathOffset") is None:
    try:
      with open("/data/params/d/OffsetTotal", "r") as f:
        old_offset_m = float(f.read().strip())
      params.put("PathOffset", str(max(-200, min(200, int(round(old_offset_m * 100.0))))))
    except (OSError, TypeError, ValueError):
      pass

  # set unset params
  for k, v in default_params:
    if params.get(k) is None:
      params.put(k, v)

  # Migrate this fork's former torque defaults to the aPilot values. Custom
  # values are preserved; only the exact old defaults are changed.
  for key, old_value, apilot_value in (
      ("SteerActuatorDelay", "25", "30"),
      ("LateralTorqueAccelFactor", "2747", "2500"),
      ("LateralTorqueFriction", "98", "100")):
    if params.get(key, encoding="utf8") == old_value:
      params.put(key, apilot_value)

  # Preserve the existing 0~40 km/h curve when introducing the 20 km/h point.
  # Devices with custom end points receive their actual midpoint rather than
  # a static default that could create a new acceleration bump after updating.
  if params.get("CruiseMaxVals20") is None:
    vals1 = params.get_int("CruiseMaxVals1")
    vals2 = params.get_int("CruiseMaxVals2")
    midpoint = int(round((vals1 + vals2) / 10.0) * 5)
    params.put("CruiseMaxVals20", str(max(10, min(250, midpoint))))

  # 더 이상 어떤 코드도 읽지 않는 파라미터 파일을 지운다(2026-10-10 정리).
  # 키 목록에서 빠졌으므로 Params 로는 지울 수 없고 파일을 직접 지운다. 없으면 무시.
  for dead_key in ("PidJerkAccel", "PidJerkDecel", "LeadDepartCost",
                   "LongCoastBand", "LongitudinalActuatorDelayUpperBound", "StoppingAccel",
                   "NoLeadCruiseJerkLimit", "EnableSpeedTF", "StockNaviDecelEnabled",
                   "VisionRadarToggle", "SccSmootherState",
                   "LateralTorqueKd", "CarrotLearnFrictionBase",
                   "OPKRTimeZone", "ShowBlindSpotAlways", "EonClusterHudPathFlipV2Migrated",
                   "EnableRadarTracks", "LatTraceEnabled", "HudRemoteKey",
                   "LatYawMeasureBlend", "LateralTorqueKpV", "LateralTorqueKiV",
                   "LateralTorqueKf", "LatAccelFrictionFactor", "LatJerkFrictionFactor",
                   "LatCurveIDeadzone", "LatLowSpeedCurvTauMs", "EarlyStopReqRelease",
                   "OffsetTotal", "AdjustLaneOffset", "LanelessOffset", "LatMpcInputOffset",
                   "DynamicLaneProfile", "KeepSteeringTurnSignals",
                   "CustomSteerRatio", "UseLiveSteerRatio",
                   # 2026-11-05: unused longitudinal params and finished one-time migrations.
                   "LongActuatorDelay", "NewRadarInterface", "E2EAccMode",
                   "ApilotC2LongMigrated", "CruiseButtonLongDelayC2Migrated",
                   "TFollowDecelBoost", "TFollowClosingMargin",
                   "RadarReactionFactor", "SccVisionMismatchFallback"):
    try:
      os.remove(os.path.join("/data/params/d", dead_key))
    except OSError:
      pass

  # This EON build targets Korean left-hand-drive vehicles only.
  params.put_bool("IsRHD", False)

  # is this dashcam?
  if os.getenv("PASSIVE") is not None:
    params.put_bool("Passive", bool(int(os.getenv("PASSIVE", "0"))))

  if params.get("Passive") is None:
    raise Exception("Passive must be set to continue")

  # Create folders needed for msgq
  try:
    os.mkdir("/dev/shm")
  except FileExistsError:
    pass
  except PermissionError:
    print("WARNING: failed to make /dev/shm")

  # set version params
  params.put("Version", get_version())
  params.put("TermsVersion", terms_version)
  params.put("TrainingVersion", training_version)
  params.put("GitCommit", get_commit(default=""))
  params.put("GitBranch", get_short_branch(default=""))
  params.put("GitRemote", get_origin(default=""))

  # set dongle id
  reg_res = register(show_spinner=True)
  if reg_res:
    dongle_id = reg_res
  else:
    serial = params.get("HardwareSerial")
    raise Exception(f"Registration failed for device {serial}")
  os.environ['DONGLE_ID'] = dongle_id  # Needed for swaglog

  if not is_dirty():
    os.environ['CLEAN'] = '1'

  # init logging
  sentry.init(sentry.SentryProject.SELFDRIVE)
  cloudlog.bind_global(dongle_id=dongle_id, version=get_version(), dirty=is_dirty(),
                       device=HARDWARE.get_device_type())


def manager_prepare() -> None:
  for p in managed_processes.values():
    p.prepare()


def manager_cleanup() -> None:
  # send signals to kill all procs
  for p in managed_processes.values():
    p.stop(block=False)

  # ensure all are killed
  for p in managed_processes.values():
    p.stop(block=True)

  cloudlog.info("everything is dead")


def manager_thread() -> None:

  if EON:
    Process(name="autoshutdownd", target=launcher, args=("selfdrive.autoshutdownd", "autoshutdownd")).start()
    system("am startservice com.neokii.optool/.MainService")

  Process(name="road_speed_limiter", target=launcher, args=("selfdrive.road_speed_limiter", "road_speed_limiter")).start()
  cloudlog.bind(daemon="manager")
  cloudlog.info("manager start")
  cloudlog.info({"environ": os.environ})

  params = Params()

  ignore: List[str] = []
  if params.get("DongleId", encoding='utf8') in (None, UNREGISTERED_DONGLE_ID):
    ignore += ["manage_athenad", "uploader"]
  if os.getenv("NOBOARD") is not None:
    ignore.append("pandad")
  ignore += [x for x in os.getenv("BLOCK", "").split(",") if len(x) > 0]

  ensure_running(managed_processes.values(), started=False, not_run=ignore)

  started_prev = False
  sm = messaging.SubMaster(['deviceState'])
  pm = messaging.PubMaster(['managerState'])

  while True:
    sm.update()
    not_run = ignore[:]

    started = sm['deviceState'].started
    driverview = params.get_bool("IsDriverViewEnabled")
    ensure_running(managed_processes.values(), started, driverview, not_run)

    # trigger an update after going offroad
    if started_prev and not started and 'updated' in managed_processes:
      os.sync()
      managed_processes['updated'].signal(signal.SIGHUP)

    started_prev = started

    running = ' '.join("%s%s\u001b[0m" % ("\u001b[32m" if p.proc.is_alive() else "\u001b[31m", p.name)
                       for p in managed_processes.values() if p.proc)
    print(running)
    cloudlog.debug(running)

    # send managerState
    msg = messaging.new_message('managerState')
    msg.managerState.processes = [p.get_process_state_msg() for p in managed_processes.values()]
    pm.send('managerState', msg)

    # Exit main loop when uninstall/shutdown/reboot is needed
    shutdown = False
    for param in ("DoUninstall", "DoShutdown", "DoReboot"):
      if params.get_bool(param):
        shutdown = True
        params.put("LastManagerExitReason", param)
        cloudlog.warning(f"Shutting down manager - {param} set")

    if shutdown:
      break


def main() -> None:
  prepare_only = os.getenv("PREPAREONLY") is not None

  manager_init()

  # Start UI early so prepare can happen in the background
  if not prepare_only:
    managed_processes['ui'].start()

  manager_prepare()

  if prepare_only:
    return

  # SystemExit on sigterm
  signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(1))

  try:
    manager_thread()
  except Exception:
    traceback.print_exc()
    sentry.capture_exception()
  finally:
    manager_cleanup()

  params = Params()
  if params.get_bool("DoUninstall"):
    cloudlog.warning("uninstalling")
    HARDWARE.uninstall()
  elif params.get_bool("DoReboot"):
    cloudlog.warning("reboot")
    HARDWARE.reboot()
  elif params.get_bool("DoShutdown"):
    cloudlog.warning("shutdown")
    HARDWARE.shutdown()


if __name__ == "__main__":
  unblock_stdout()

  try:
    main()
  except Exception:
    add_file_handler(cloudlog)
    cloudlog.exception("Manager failed to start")

    try:
      managed_processes['ui'].stop()
    except Exception:
      pass

    # Show last 3 lines of traceback
    error = traceback.format_exc(-3)
    error = "Manager failed to start\n\n" + error
    with TextWindow(error) as t:
      t.wait_for_exit()

    raise

  # manual exit because we are forked
  sys.exit(0)

