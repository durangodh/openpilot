#!/usr/bin/env python3
import os
import math
from numbers import Number

from cereal import car, log
from common.numpy_fast import clip, interp
from common.realtime import sec_since_boot, config_realtime_process, Priority, Ratekeeper, DT_CTRL
from common.profiler import Profiler
from common.params import Params, put_nonblocking
import cereal.messaging as messaging
from common.conversions import Conversions as CV
from panda import ALTERNATIVE_EXPERIENCE
from selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from selfdrive.swaglog import cloudlog
from selfdrive.version import is_tested_branch
from selfdrive.boardd.boardd import can_list_to_can_capnp
from selfdrive.car.car_helpers import get_car, get_startup_event, get_one_can
from selfdrive.controls.lib.lane_planner import CAMERA_OFFSET
from selfdrive.controls.lib.drive_helpers import V_CRUISE_INITIAL, V_CRUISE_MAX, update_v_cruise, initialize_v_cruise
from selfdrive.controls.lib.drive_helpers import get_lag_adjusted_curvature
from selfdrive.controls.lib.latcontrol import LatControl
from selfdrive.controls.lib.longcontrol import LongControl
from selfdrive.controls.lib.latcontrol_pid import LatControlPID
from selfdrive.controls.lib.latcontrol_indi import LatControlINDI
from selfdrive.controls.lib.latcontrol_angle import LatControlAngle
from selfdrive.controls.lib.events import Events, ET
from selfdrive.controls.lib.soft_hold import SoftHoldController
from selfdrive.controls.lib.low_speed_long import read_cruise_speed_min
from selfdrive.controls.lib.alertmanager import AlertManager, set_offroad_alert
from selfdrive.controls.lib.vehicle_model import VehicleModel
from selfdrive.locationd.calibrationd import Calibration
from selfdrive.hardware import HARDWARE, TICI, EON
from selfdrive.manager.process_config import managed_processes
from selfdrive.car.hyundai.scc_smoother import SccSmoother
from selfdrive.controls.lib import live_tune

SOFT_DISABLE_TIME = 3  # seconds
LDW_MIN_SPEED = 31 * CV.MPH_TO_MS
LANE_DEPARTURE_THRESHOLD = 0.1

REPLAY = "REPLAY" in os.environ
SIMULATION = "SIMULATION" in os.environ
NOSENSOR = "NOSENSOR" in os.environ
IGNORE_PROCESSES = {"rtshield", "uploader", "deleter", "loggerd", "logmessaged", "tombstoned",
                    "logcatd", "proclogd", "clocksd", "updated", "timezoned", "manage_athenad",
                    "statsd", "shutdownd"} | \
                   {k for k, v in managed_processes.items() if not v.enabled}

ThermalStatus = log.DeviceState.ThermalStatus
State = log.ControlsState.OpenpilotState
PandaType = log.PandaState.PandaType
Desire = log.LateralPlan.Desire
LaneChangeState = log.LateralPlan.LaneChangeState
LaneChangeDirection = log.LateralPlan.LaneChangeDirection
EventName = car.CarEvent.EventName
ButtonEvent = car.CarState.ButtonEvent
ButtonType = car.CarState.ButtonEvent.Type
GearShifter = car.CarState.GearShifter
SafetyModel = car.CarParams.SafetyModel

IGNORED_SAFETY_MODES = (SafetyModel.silent, SafetyModel.noOutput)
CSID_MAP = {"1": EventName.roadCameraError, "2": EventName.wideRoadCameraError, "0": EventName.driverCameraError}
ACTUATOR_FIELDS = tuple(car.CarControl.Actuators.schema.fields.keys())
ACTIVE_STATES = (State.enabled, State.softDisabling, State.overriding)
ENABLED_STATES = (State.preEnabled, *ACTIVE_STATES)


class Controls:
  def __init__(self, sm=None, pm=None, can_sock=None, CI=None):
    config_realtime_process(4 if TICI else 3, Priority.CTRL_HIGH)

    # Setup sockets
    self.pm = pm
    if self.pm is None:
      self.pm = messaging.PubMaster(['sendcan', 'controlsState', 'carState',
                                     'carControl', 'carEvents', 'carParams'])

    self.camera_packets = ["roadCameraState", "driverCameraState"]
    if TICI:
      self.camera_packets.append("wideRoadCameraState")

    self.can_sock = can_sock
    if can_sock is None:
      can_timeout = None if os.environ.get('NO_CAN_TIMEOUT', False) else 100
      self.can_sock = messaging.sub_sock('can', timeout=can_timeout)

    if TICI:
      self.log_sock = messaging.sub_sock('androidLog')

    if CI is None:
      # wait for one pandaState and one CAN packet
      print("Waiting for CAN messages...")
      get_one_can(self.can_sock)

      self.CI, self.CP = get_car(self.can_sock, self.pm.sock['sendcan'])
    else:
      self.CI, self.CP = CI, CI.CP

    params = Params()
    self.joystick_mode = params.get_bool("JoystickDebugMode") or (self.CP.notCar and sm is None)
    joystick_packet = ['testJoystick'] if self.joystick_mode else []

    self.sm = sm
    if self.sm is None:
      ignore = ['driverCameraState', 'managerState'] if SIMULATION else None
      self.sm = messaging.SubMaster(['deviceState', 'pandaStates', 'peripheralState', 'modelV2', 'liveCalibration',
                                     'driverMonitoringState', 'longitudinalPlan', 'lateralPlan', 'liveLocationKalman',
                                     'managerState', 'liveParameters', 'radarState'] + self.camera_packets + joystick_packet,
                                    ignore_alive=ignore, ignore_avg_freq=['radarState', 'longitudinalPlan'])

    # set alternative experiences from parameters
    self.disengage_on_accelerator = params.get_bool("DisengageOnAccelerator")
    self.CP.alternativeExperience = 0
    if not self.disengage_on_accelerator:
      self.CP.alternativeExperience |= ALTERNATIVE_EXPERIENCE.DISABLE_DISENGAGE_ON_GAS

    # read params
    self.is_metric = params.get_bool("IsMetric")
    self.cruise_speed_min = read_cruise_speed_min(params)
    self.is_ldw_enabled = params.get_bool("IsLdwEnabled")
    openpilot_enabled_toggle = params.get_bool("OpenpilotEnabledToggle")
    passive = params.get_bool("Passive") or not openpilot_enabled_toggle

    # detect sound card presence and ensure successful init
    sounds_available = HARDWARE.get_sound_card_online()

    car_recognized = self.CP.carName != 'mock'

    controller_available = self.CI.CC is not None and not passive and not self.CP.dashcamOnly
    self.read_only = not car_recognized or not controller_available or self.CP.dashcamOnly
    if self.read_only:
      safety_config = car.CarParams.SafetyConfig.new_message()
      safety_config.safetyModel = car.CarParams.SafetyModel.noOutput
      self.CP.safetyConfigs = [safety_config]

    if is_tested_branch():
      self.CP.experimentalLongitudinalAvailable = False

    # Write CarParams for radard
    cp_bytes = self.CP.to_bytes()
    params.put("CarParams", cp_bytes)
    put_nonblocking("CarParamsCache", cp_bytes)
    put_nonblocking("CarParamsPersistent", cp_bytes)

    # cleanup old params
    if not self.CP.experimentalLongitudinalAvailable:
      params.remove("ExperimentalLongitudinalEnabled")
    if not self.CP.openpilotLongitudinalControl:
      params.remove("ExperimentalMode")

    self.CC = car.CarControl.new_message()
    self.CS_prev = car.CarState.new_message()
    self.AM = AlertManager()
    self.events = Events()

    self.LoC = LongControl(self.CP)
    self.VM = VehicleModel(self.CP)

    self.LaC: LatControl
    if self.CP.steerControlType == car.CarParams.SteerControlType.angle:
      self.LaC = LatControlAngle(self.CP, self.CI)
    elif self.CP.lateralTuning.which() == 'pid':
      self.LaC = LatControlPID(self.CP, self.CI)
    elif self.CP.lateralTuning.which() == 'indi':
      self.LaC = LatControlINDI(self.CP, self.CI)
    elif self.CP.lateralTuning.which() == 'torque':
      self.LaC = LatControlTorque(self.CP, self.CI)

    self.initialized = False
    self.state = State.disabled
    self.enabled = False
    self.active = False
    self.can_rcv_error = False
    self.soft_disable_timer = 0
    self.v_cruise_kph = V_CRUISE_INITIAL
    self.v_cruise_cluster_kph = V_CRUISE_INITIAL
    self.v_cruise_kph_last = 0
    self.mismatch_counter = 0
    self.cruise_mismatch_counter = 0
    self.can_rcv_error_counter = 0
    self.last_blinker_frame = 0
    self.distance_traveled = 0
    self.last_functional_fan_frame = 0
    self.events_prev = []
    self.current_alert_types = [ET.PERMANENT]
    self.logged_comm_issue = False
    self.button_timers = {ButtonEvent.Type.decelCruise: 0, ButtonEvent.Type.accelCruise: 0}
    self.last_actuators = car.CarControl.Actuators.new_message()
    self.steer_limited = False
    self.desired_curvature = 0.0
    self.desired_curvature_rate = 0.0

    # scc smoother
    self.is_cruise_enabled = False
    self.applyMaxSpeed = 0
    self.apply_accel = 0.
    self.fused_accel = 0.
    self.lead_drel = 0.
    self.aReqValue = 0.
    self.aReqValueMin = 0.
    self.aReqValueMax = 0.
    self.sccStockCamStatus = 0
    self.sccStockCamAct = 0

    self.left_lane_visible = False
    self.right_lane_visible = False

    self.wide_camera = TICI and params.get_bool('EnableWideCamera')
    self.disable_op_fcw = params.get_bool('DisableOpFcw')

    # AutoLaneChangeTimer íŒŒë¼ë¯¸í„° ì ‘ê·¼ìš©
    self.params = Params()
    self.soft_hold = SoftHoldController()
    self.soft_hold_enabled = self.params.get_bool("SoftHoldMode")
    self.cruise_button_mode = 0
    self.cruise_speed_unit = 10
    self.cruise_speed_unit_basic = 1
    self.cruise_button_long_delay = 70
    self.cruise_speed_table = [30, 50, 70, 90, 110]
    self.speed_from_pcm = 2

    # TODO: no longer necessary, aside from process replay
    self.sm['liveParameters'].valid = True

    self.startup_event = get_startup_event(car_recognized, controller_available, len(self.CP.carFw) > 0)

    if not sounds_available:
      self.events.add(EventName.soundsUnavailable, static=True)
    if not car_recognized:
      self.events.add(EventName.carUnrecognized, static=True)
      if len(self.CP.carFw) > 0:
        set_offroad_alert("Offroad_CarUnrecognized", True)
      else:
        set_offroad_alert("Offroad_NoFirmware", True)
    elif self.read_only:
      self.events.add(EventName.dashcamMode, static=True)
    elif self.joystick_mode:
      self.events.add(EventName.joystickDebug, static=True)
      self.startup_event = None

    # controlsd is driven by can recv, expected at 100Hz
    self.rk = Ratekeeper(100, print_delay_threshold=None)
    self.prof = Profiler(False)  # off by default

  def update_events(self, CS):
    """Compute carEvents from carState"""

    self.events.clear()

    # Add startup event
    if self.startup_event is not None:
      self.events.add(self.startup_event)
      self.startup_event = None

    # Don't add any more events if not initialized
    if not self.initialized:
      self.events.add(EventName.controlsInitializing)
      return

    # Block resume if cruise never previously enabled
    resume_pressed = any(be.type in (ButtonType.accelCruise, ButtonType.resumeCruise) for be in CS.buttonEvents)
    if not self.CP.pcmCruise and self.v_cruise_kph == V_CRUISE_INITIAL and resume_pressed:
      self.events.add(EventName.resumeBlocked)

    if CS.gasPressed:
      self.events.add(EventName.pedalPressedPreEnable if self.disengage_on_accelerator else
                      EventName.gasPressedOverride)

    self.events.add_from_msg(CS.events)

    if not self.CP.notCar:
      self.events.add_from_msg(self.sm['driverMonitoringState'].events)

    # Create events for battery, temperature, disk space, and memory
    if EON and (self.sm['peripheralState'].pandaType != PandaType.uno) and \
       self.sm['deviceState'].batteryPercent < 1 and self.sm['deviceState'].chargingError:
      # at zero percent battery, while discharging, OP should not allowed
      self.events.add(EventName.lowBattery)
    if self.sm['deviceState'].thermalStatus >= ThermalStatus.red:
      self.events.add(EventName.overheat)
    if self.sm['deviceState'].freeSpacePercent < 7 and not SIMULATION:
      # under 7% of space free no enable allowed
      self.events.add(EventName.outOfSpace)
    # TODO: make tici threshold the same
    if self.sm['deviceState'].memoryUsagePercent > (90 if TICI else 65) and not SIMULATION:
      self.events.add(EventName.lowMemory)

    # TODO: enable this once loggerd CPU usage is more reasonable
    #cpus = list(self.sm['deviceState'].cpuUsagePercent)[:(-1 if EON else None)]
    #if max(cpus, default=0) > 95 and not SIMULATION:
    #  self.events.add(EventName.highCpuUsage)

    # Alert if fan isn't spinning for 5 seconds
    if self.sm['peripheralState'].pandaType in (PandaType.uno, PandaType.dos):
      if self.sm['peripheralState'].fanSpeedRpm == 0 and self.sm['deviceState'].fanSpeedPercentDesired > 50:
        if (self.sm.frame - self.last_functional_fan_frame) * DT_CTRL > 5.0:
          self.events.add(EventName.fanMalfunction)
      else:
        self.last_functional_fan_frame = self.sm.frame

    # Handle calibration status
    cal_status = self.sm['liveCalibration'].calStatus
    if cal_status != Calibration.CALIBRATED:
      if cal_status == Calibration.UNCALIBRATED:
        self.events.add(EventName.calibrationIncomplete)
      else:
        self.events.add(EventName.calibrationInvalid)

    # Handle lane change
    lane_change_set_timer = int(self.params.get("AutoLaneChangeTimer", encoding="utf8"))
    if self.sm['lateralPlan'].laneChangeEdgeBlock:
      self.events.add(EventName.laneChangeRoadEdge)
    elif self.sm['lateralPlan'].laneChangeState == LaneChangeState.preLaneChange:
      direction = self.sm['lateralPlan'].laneChangeDirection
      lc_prev = self.sm['lateralPlan'].laneChangePrev
      if (CS.leftBlindspot and direction == LaneChangeDirection.left) or \
         (CS.rightBlindspot and direction == LaneChangeDirection.right):
        self.events.add(EventName.laneChangeBlocked)
      else:
        if direction == LaneChangeDirection.left:
          self.events.add(EventName.preLaneChangeLeft) if lane_change_set_timer == 0 or lc_prev else \
            self.events.add(EventName.laneChange)
        else:
          self.events.add(EventName.preLaneChangeRight) if lane_change_set_timer == 0 or lc_prev else \
            self.events.add(EventName.laneChange)
    elif self.sm['lateralPlan'].laneChangeState in (LaneChangeState.laneChangeStarting,
                                                    LaneChangeState.laneChangeFinishing):
      self.events.add(EventName.laneChange)

    if CS.canTimeout:
      self.events.add(EventName.canBusMissing)
    elif not CS.canValid:
      self.events.add(EventName.canError)

    for i, pandaState in enumerate(self.sm['pandaStates']):
      # All pandas must match the list of safetyConfigs, and if outside this list, must be silent or noOutput
      if i < len(self.CP.safetyConfigs):
        safety_mismatch = pandaState.safetyModel != self.CP.safetyConfigs[i].safetyModel or \
                          pandaState.safetyParam != self.CP.safetyConfigs[i].safetyParam or \
                          pandaState.alternativeExperience != self.CP.alternativeExperience
      else:
        safety_mismatch = pandaState.safetyModel not in IGNORED_SAFETY_MODES

      if safety_mismatch or self.mismatch_counter >= 200:
        self.events.add(EventName.controlsMismatch)

      if lß½ù¶‰ËkºwµçHÜŠB‚ˆ]Ü[ˆHÙ[‹œÛVÉÛ]\˜[[‰×BˆÛ™×Ü[ˆHÙ[‹œÛVÉÛÛ™Ú]Y[˜[[‰×B‚ˆYˆÙ[‹œÛK™œ˜[YH	HLOH‚ˆÙ[‹œÛÙÚÛÙ[˜X›YHÙ[‹œ\˜[\Ë™Ù]Ø›ÛÛ
”ÛÙÛ[ÙHŠBˆ™\İ[YWÜ™\ÜÙYH[J‹œ™\ÜÙY[™‹\H[ˆ
]Û•\K˜XØÙ[ÜZ\ÙK]Û•\Kœ™\İ[YPÜZ\ÙJBˆ›Üˆˆ[ˆÔË˜]Û‘]™[ÊBˆÛÙÚÛØ]˜Z[X›HH
Ù[‹œÛÙÚÛÙ[˜X›Y[™Ù[‹Ô›Ü[œ[İÛ™Ú]Y[˜[ÛÛ›Û[™ˆÙ[‹˜Xİ]™H[™
ÔË˜ÜZ\ÙTİ]K™[˜X›YXØÈÜˆÙ[‹œÛÙÚÛ˜Xİ]™JJBˆÛÙÚÛØXİ]™HHÙ[‹œÛÙÚÛ\]JÛÙÚÛØ]˜Z[X›KÔË˜œ˜ZÙT™\ÜÙYÔË™Ø\Ô™\ÜÙYˆÔË‘YÛË™\İ[YWÜ™\ÜÙYÔË™ÙX\”ÚY\ˆOHÙX\”ÚY\‹™š]™KĞÕ“
B‚ˆĞÈHØ\‹Ø\ÛÛ›Û›™]×ÛY\ÜØYÙJ
BˆĞË™[˜X›YHÙ[‹™[˜X›YˆÈÚXÚÈÚXÚXİX]ÜœÈØ[ˆ™H[˜X›YˆĞË›]Xİ]™HHÙ[‹˜Xİ]™H[™›İÔËœİY\‘˜][[\Ü˜\H[™›İÔËœİY\‘˜][\›X[™[[™ˆÔË‘YÛÈˆÙ[‹Ô›Z[”İY\”ÜYY[™›İÔËœİ[™İ[ˆ[™XœÊÔËœİY\š[™Ğ[™ÛQYÊHÙ[‹Ô›X^İY\š[™Ğ[™ÛQYÂˆĞË›Û™ĞXİ]™HHÙ[‹˜Xİ]™H[™›İÙ[‹™]™[Ë˜[JU“Õ‘T”’QJH[™Ù[‹Ô›Ü[œ[İÛ™Ú]Y[˜[ÛÛ›Û‚ˆXİX]ÜœÈHĞË˜XİX]ÜœÂˆXİX]ÜœË›Û™ĞÛÛ›Ûİ]HHÙ[‹“ĞË›Û™×ØÛÛ›ÛÜİ]B‚ˆYˆÔË›Y›[šÙ\ˆÜˆÔËœšYÚ›[šÙ\‚ˆÙ[‹›\İØ›[šÙ\—Ùœ˜[YHHÙ[‹œÛK™œ˜[YB‚ˆÈİ]HÜXÚYšXÈXİ[ÛœÂˆYˆ›İĞË›]Xİ]™N‚ˆÙ[‹“PËœ™\Ù]

BˆYˆ›İĞË›Û™ĞXİ]™N‚ˆÙ[‹“ĞËœ™\Ù]
—ÜYPÔË‘YÛÊB‚ˆYˆ›İÔË˜ÜZ\ÙTİ]K™[˜X›YXØÈ[™›İÛÙÚÛØXİ]™N‚ˆÙ[‹“ĞËœ™\Ù]
—ÜYPÔË‘YÛÊB‚ˆYˆ›İÙ[‹š›Ş\İXÚ×Û[ÙN‚ˆÈXØÙ[QÛÜˆYØXØÙ[Û[Z]ÈHÙ[‹ÒK™Ù]ÜYØXØÙ[Û[Z]ÊÙ[‹ÔÔË‘YÛËÙ[‹—ØÜZ\ÙWÚÜ
ˆÕ‹’ÔÕ×ÓTÊBˆÜÚ[˜ÙWÜ[ˆH
Ù[‹œÛK™œ˜[YHHÙ[‹œÛKœ˜İ—Ùœ˜[YVÉÛÛ™Ú]Y[˜[[‰×JH
ˆĞÕ“ˆXİX]ÜœË˜XØÙ[HÙ[‹“ĞË\]JĞË›Û™ĞXİ]™H[™
ÔË˜ÜZ\ÙTİ]K™[˜X›YXØÈÜˆÛÙÚÛØXİ]™JKˆÔËÛ™×Ü[‹YØXØÙ[Û[Z]ËÜÚ[˜ÙWÜ[‹ˆÙ[‹œÛVÉÜ˜Y\”İ]I×KÛÙÚÛØXİ]™KÙ[‹œÛÙÚÛœ™[X\ÙY
B‚ˆÈİY\š[™ÈQÛÜ[™]\˜[TÂˆÙ[‹™\Ú\™YØİ\˜]\™KÙ[‹™\Ú\™YØİ\˜]\™WÜ˜]HHÙ]ÛY×ØY\İYØİ\˜]\™JÙ[‹ÔÔË‘YÛËˆ]Ü[‹œÚ\Ëˆ]Ü[‹˜İ\˜]\™\Ëˆ]Ü[‹˜İ\˜]\™T˜]\ÊBˆXİX]ÜœËœİY\‹XİX]ÜœËœİY\š[™Ğ[™ÛQYËX×ÛÙÈHÙ[‹“PË\]JĞË›]Xİ]™KÔËÙ[‹•“K\˜[\ËˆÙ[‹›\İØXİX]ÜœËÙ[‹œİY\—Û[Z]YÙ[‹™\Ú\™YØİ\˜]\™KˆÙ[‹™\Ú\™YØİ\˜]\™WÜ˜]KÙ[‹œÛVÉÛ]™SØØ][Û’Ø[X[‰×KˆÙ[‹œÛVÉÛ[Ù[Œ‰×JBˆ[ÙN‚ˆX×ÛÙÈHÙËÛÛ›ÛÔİ]K“]\˜[XYÔİ]K›™]×ÛY\ÜØYÙJ
BˆYˆÙ[‹œÛKœ˜İ—Ùœ˜[YVÉİ\İ›Ş\İXÚÉ×Hˆ‚ˆYˆĞË›Û™ĞXİ]™N‚ˆXİX]ÜœË˜XØÙ[HŒ
˜Û\
Ù[‹œÛVÉİ\İ›Ş\İXÚÉ×K˜^\ÖÌKLKJB‚ˆYˆĞË›]Xİ]™N‚ˆİY\ˆHÛ\
Ù[‹œÛVÉİ\İ›Ş\İXÚÉ×K˜^\ÖÌWKLKJBˆÈX^[™ÛH\ÈH›Üˆ[™ÛKX˜\ÙYØ\œÂˆXİX]ÜœËœİY\‹XİX]ÜœËœİY\š[™Ğ[™ÛQYÈHİY\‹İY\ˆ
ˆK‚‚ˆX×ÛÙË˜Xİ]™HHÙ[‹˜Xİ]™BˆX×ÛÙËœİY\š[™Ğ[™ÛQYÈHÔËœİY\š[™Ğ[™ÛQYÂˆX×ÛÙË›İ]]HXİX]ÜœËœİY\‚ˆX×ÛÙËœØ]\˜]YHXœÊXİX]ÜœËœİY\ŠHHB‚ˆÈÙ[™HœİY\š[™È™\]Z\™Y[\ˆYˆØ]\˜][ÛˆÛİ[\È™XXÚYH[Z]ˆYˆX×ÛÙË˜Xİ]™H[™›İÔËœİY\š[™Ô™\ÜÙY[™Ù[‹Ô›]\˜[[š[™ËÚXÚ

HOH	İÜœ]YIÈ[™›İÙ[‹š›Ş\İXÚ×Û[ÙN‚ˆ[™\œÚÛİ[™ÈHXœÊX×ÛÙË™\Ú\™Y]\˜[XØÙ[
HÈXœÊYKLÈ
ÈX×ÛÙË˜XİX[]\˜[XØÙ[
HˆKŒÂˆ\›š[™ÈHXœÊX×ÛÙË™\Ú\™Y]\˜[XØÙ[
HˆKŒˆÛÛÙÜÜYYHÔË‘YÛÈˆBˆX^İÜœ]YHHXœÊÙ[‹›\İØXİX]ÜœËœİY\ŠHˆNBˆYˆ[™\œÚÛİ[™È[™\›š[™È[™ÛÛÙÜÜYY[™X^İÜœ]YN‚ˆÙ[‹™]™[Ë˜Y
]™[˜[YKœİY\”Ø]\˜]Y
Bˆ[YˆX×ÛÙË˜Xİ]™H[™›İÔËœİY\š[™Ô™\ÜÙY[™X×ÛÙËœØ]\˜]Y‚ˆ]ÜÚ[ÈH]Ü[‹™]Ú[ÂˆYˆ[Š]ÜÚ[ÊN‚ˆÈÚXÚÈYˆÙH]šX]Yœ›ÛHH]ˆÈÑÈ\ÙH\Ú\™YœÈXİX[İ\˜]\™BˆYˆÙ[‹ÔœİY\ÛÛ›Û\HOHØ\‹Ø\”\˜[\Ë”İY\ÛÛ›Û\K˜[™ÛN‚ˆİY\š[™×İ˜[YHHXİX]ÜœËœİY\š[™Ğ[™ÛQYÂˆ[ÙN‚ˆİY\š[™×İ˜[YHHXİX]ÜœËœİY\‚‚ˆYÙ]šX][ÛˆHİY\š[™×İ˜[YHˆ[™]ÜÚ[ÖÌHLŒŒˆšYÚÙ]šX][ÛˆHİY\š[™×İ˜[YH[™]ÜÚ[ÖÌHˆŒŒ‚ˆYˆYÙ]šX][ÛˆÜˆšYÚÙ]šX][Û‚ˆÙ[‹™]™[Ë˜Y
]™[˜[YKœİY\”Ø]\˜]Y
B‚ˆÈ[œİ\™H›È˜SœËÒ[™œÂˆ›Üˆ[ˆPÕPUÔ—Ñ’QSÎ‚ˆ]ˆHÙ]]ŠXİX]ÜœË
BˆYˆ›İ\Ú[œİ[˜ÙJ]‹[X™\ŠN‚ˆÛÛ[YB‚ˆYˆ›İX]š\Ùš[š]J]ŠN‚ˆÛİYÙË™\œ›ÜŠˆ˜XİX]ÜœËÜH›İš[š]HØXİX]ÜœË×ÙXİ

_HŠBˆÙ]]ŠXİX]ÜœËŒ
B‚ˆ™]\›ˆĞËX×ÛÙÂ‚ˆYˆ\]WØ]Û—İ[Y\œÊÙ[‹]Û‘]™[ÊN‚ˆÈ[˜Ü™[Y[[Y\ˆ›Üˆ]ÛœÈİ[™\ÜÙYˆ›ÜˆÈ[ˆÙ[‹˜]Û—İ[Y\œÎ‚ˆYˆÙ[‹˜]Û—İ[Y\œÖÚ×Hˆ‚ˆÙ[‹˜]Û—İ[Y\œÖÚ×H
ÏHB‚ˆ›Üˆˆ[ˆ]Û‘]™[Î‚ˆYˆ‹\Kœ˜]È[ˆÙ[‹˜]Û—İ[Y\œÎ‚ˆÙ[‹˜]Û—İ[Y\œÖØ‹\Kœ˜]×HHHYˆ‹œ™\ÜÙY[ÙH‚ˆYˆX›\ÚÛÙÜÊÙ[‹ÔËİ\İ[YKĞËX×ÛÙÊN‚ˆˆˆ”Ù[™XİX]ÜœÈ[™YÛÛ[X[™ÈÈHØ\‹Ù[™ÛÛ›ÛÜİ]H[™TÈÙÙÚ[™Èˆˆ‚‚ˆÈÜšY[][Ûˆ[™[™ÛH˜]\ÈØ[ˆ™H\ÙY[›ÜˆØ\˜ÛÛ›Û\‚ˆÈÛ›HØ[Xœ˜]Y
Ø\ŠHœ˜[YH\È™[]˜[›ÜˆHØ\˜ÛÛ›Û\‚ˆÜšY[][Û—İ˜[YHH\İ
Ù[‹œÛVÉÛ]™SØØ][Û’Ø[X[‰×K˜Ø[Xœ˜]YÜšY[][Û“‘Q˜[YJBˆYˆ[ŠÜšY[][Û—İ˜[YJHˆ‚ˆĞË›ÜšY[][Û“‘QHÜšY[][Û—İ˜[YBˆ[™İ[\—Ü˜]Wİ˜[YHH\İ
Ù[‹œÛVÉÛ]™SØØ][Û’Ø[X[‰×K˜[™İ[\•™[ØÚ]PØ[Xœ˜]Y˜[YJBˆYˆ[Š[™İ[\—Ü˜]Wİ˜[YJHˆ‚ˆĞË˜[™İ[\•™[ØÚ]HH[™İ[\—Ü˜]Wİ˜[YB‚ˆĞË˜ÜZ\ÙPÛÛ›Û˜Ø[˜Ù[HÙ[‹ÔœÛPÜZ\ÙH[™›İÙ[‹™[˜X›Y[™ÔË˜ÜZ\ÙTİ]K™[˜X›YˆYˆÙ[‹š›Ş\İXÚ×Û[ÙH[™Ù[‹œÛKœ˜İ—Ùœ˜[YVÉİ\İ›Ş\İXÚÉ×Hˆ[™Ù[‹œÛVÉİ\İ›Ş\İXÚÉ×K˜]ÛœÖÌN‚ˆĞË˜ÜZ\ÙPÛÛ›Û˜Ø[˜Ù[HYB‚ˆÜYYÈHÙ[‹œÛVÉÛÛ™Ú]Y[˜[[‰×KœÜYYÂˆYˆ[ŠÜYYÊN‚ˆĞË˜ÜZ\ÙPÛÛ›Ûœ™\İ[YHH
Ù[‹™[˜X›Y[™›İÙ[‹œÛÙÚÛ˜Xİ]™H[™ˆÔË˜ÜZ\ÙTİ]Kœİ[™İ[[™ÜYYÖËLWHˆŒJB‚ˆYÛÛ›ÛHĞËšYÛÛ›ÛˆYÛÛ›ÛœÛÙÛHÙ[‹œÛÙÚÛ˜Xİ]™BˆYÛÛ›ÛœÙ]ÜYYH›Ø]
Ù[‹—ØÜZ\ÙWØÛ\İ\—ÚÜ
ˆÕ‹’ÔÕ×ÓTÊBˆYÛÛ›ÛœÜYYš\ÚX›HHÙ[‹™[˜X›YˆYÛÛ›Û›[™\Õš\ÚX›HHÙ[‹™[˜X›YˆYÛÛ›Û›XYš\ÚX›HHÙ[‹œÛVÉÛÛ™Ú]Y[˜[[‰×Kš\ÓXY‚ˆšYÚÛ[™Wİš\ÚX›HHÙ[‹œÛVÉÛ]\˜[[‰×Kœ”›ØˆˆBˆYÛ[™Wİš\ÚX›HHÙ[‹œÛVÉÛ]\˜[[‰×K››ØˆˆB‚ˆYˆÙ[‹œÛK™œ˜[YH	HLOH‚ˆÙ[‹œšYÚÛ[™Wİš\ÚX›HHšYÚÛ[™Wİš\ÚX›BˆÙ[‹›YÛ[™Wİš\ÚX›HHYÛ[™Wİš\ÚX›B‚ˆYÛÛ›ÛœšYÚ[™Uš\ÚX›HHÙ[‹œšYÚÛ[™Wİš\ÚX›BˆYÛÛ›Û›Y[™Uš\ÚX›HHÙ[‹›YÛ[™Wİš\ÚX›B‚ˆ™XÙ[Ø›[šÙ\ˆH
Ù[‹œÛK™œ˜[YHHÙ[‹›\İØ›[šÙ\—Ùœ˜[YJH
ˆĞÕ“KŒÈ\È›[šÙ\ˆÛÛÛİÛ‚ˆ×Ø[İÙYHÙ[‹š\×Û×Ù[˜X›Y[™ÔË‘YÛÈˆ×ÓRS—ÔÔQQ[™›İ™XÙ[Ø›[šÙ\ˆˆ[™›İĞË›]Xİ]™H[™Ù[‹œÛVÉÛ]™PØ[Xœ˜][Û‰×K˜Ø[İ]\ÈOHØ[Xœ˜][Û‹ĞSP”UQ‚ˆ[Ù[İŒˆHÙ[‹œÛVÉÛ[Ù[Œ‰×Bˆ\Ú\™WÜ™YXİ[ÛˆH[Ù[İŒ‹›Y]K™\Ú\™T™YXİ[Û‚ˆYˆ[Š\Ú\™WÜ™YXİ[ÛŠH[™×Ø[İÙY‚ˆšYÚÛ[™Wİš\ÚX›HH[Ù[İŒ‹›[™S[™T›ØœÖÌ—HˆBˆYÛ[™Wİš\ÚX›HH[Ù[İŒ‹›[™S[™T›ØœÖÌWHˆBˆÛ[™WØÚ[™ÙWÜ›ØˆH\Ú\™WÜ™YXİ[Û–Ñ\Ú\™K›[™PÚ[™ÙSYHWBˆ—Û[™WØÚ[™ÙWÜ›ØˆH\Ú\™WÜ™YXİ[Û–Ñ\Ú\™K›[™PÚ[™ÙTšYÚHWB‚ˆ[™WÛ[™\ÈH[Ù[İŒ‹›[™S[™\ÂˆÛ[™WØÛÜÙHHYÛ[™Wİš\ÚX›H[™
[™WÛ[™\ÖÌWKVÌHˆJKŒ
ÈĞSQTWÓÑ‘”ÑU
JBˆ—Û[™WØÛÜÙHHšYÚÛ[™Wİš\ÚX›H[™
[™WÛ[™\ÖÌ—KVÌH
KŒHĞSQTWÓÑ‘”ÑU
JB‚ˆYÛÛ›Û›Y[™Q\\H›ÛÛ
Û[™WØÚ[™ÙWÜ›ØˆˆS‘WÑTT•T‘WÕ‘TÒÓ[™Û[™WØÛÜÙJBˆYÛÛ›ÛœšYÚ[™Q\\H›ÛÛ
—Û[™WØÚ[™ÙWÜ›ØˆˆS‘WÑTT•T‘WÕ‘TÒÓ[™—Û[™WØÛÜÙJB‚ˆYˆYÛÛ›ÛœšYÚ[™Q\\ÜˆYÛÛ›Û›Y[™Q\\‚ˆÙ[‹™]™[Ë˜Y
]™[˜[YK›ÊB‚ˆÛX\—Ù]™[İ\\ÈHÙ]

BˆYˆU•ĞT“’S‘È›İ[ˆÙ[‹˜İ\œ™[Ø[\İ\\Î‚ˆÛX\—Ù]™[İ\\Ë˜Y
U•ĞT“’S‘ÊBˆYˆÙ[‹™[˜X›Y‚ˆÛX\—Ù]™[İ\\Ë˜Y
U““×ÑS•–JB‚ˆ[\ÈHÙ[‹™]™[Ë˜Ü™X]WØ[\ÊÙ[‹˜İ\œ™[Ø[\İ\\ËÜÙ[‹ÔÙ[‹œÛKÙ[‹š\×ÛY]šXËÙ[‹œÛÙÙ\ØX›Wİ[Y\—JBˆÙ[‹SK˜YÛX[JÙ[‹œÛK™œ˜[YK[\ÊBˆİ\œ™[Ø[\HÙ[‹SKœ›ØÙ\Ü×Ø[\ÊÙ[‹œÛK™œ˜[YKÛX\—Ù]™[İ\\ÊBˆYˆİ\œ™[Ø[\‚ˆYÛÛ›Ûš\İX[[\Hİ\œ™[Ø[\š\İX[Ø[\‚ˆYˆ›İÙ[‹œ™XYÛÛ›H[™Ù[‹š[š]X[^™Y‚ˆÈÙ[™Ø\ˆÛÛ›ÛÈİ™\ˆØ[‚ˆÙ[‹›\İØXİX]ÜœËØ[—ÜÙ[™ÈHÙ[‹ÒK˜\JĞËÙ[ŠBˆÙ[‹œKœÙ[™
	ÜÙ[™Ø[‰ËØ[—Û\İİ×ØØ[—ØØ\œ
Ø[—ÜÙ[™Ë\Ùİ\OIÜÙ[™Ø[‰Ë˜[YPÔË˜Ø[•˜[Y
JBˆĞË˜XİX]ÜœÓİ]]HÙ[‹›\İØXİX]ÜœÂˆÙ[‹œİY\—Û[Z]YHXœÊĞË˜XİX]ÜœËœİY\ˆHĞË˜XİX]ÜœÓİ]]œİY\ŠHˆYKL‚‚ˆ›Ü˜ÙWÙXÙ[H
Ù[‹œÛVÉÙš]™\“[Ûš]Üš[™Ôİ]I×K˜]Ø\™[™\ÜÔİ]\ÈŠHÜˆˆ
Ù[‹œİ]HOHİ]KœÛÙ\ØX›[™ÊB‚ˆÈİ\˜]\™H	ˆİY\š[™È[™ÛBˆ\˜[\ÈHÙ[‹œÛVÉÛ]™T\˜[Y]\œÉ×B‚ˆİY\—Ø[™ÛWİÚ]İ]ÛÙ™œÙ]HX]œ˜YX[œÊÔËœİY\š[™Ğ[™ÛQYÈH\˜[\Ë˜[™ÛSÙ™œÙ]YÊBˆİ\˜]\™HH\Ù[‹•“K˜Ø[×Øİ\˜]\™JİY\—Ø[™ÛWİÚ]İ]ÛÙ™œÙ]ÔË‘YÛË\˜[\Ëœ›Û
B‚ˆÈÛÛ›ÛÔİ]Bˆ]HY\ÜØYÚ[™Ë›™]×ÛY\ÜØYÙJ	ØÛÛ›ÛÔİ]IÊBˆ]˜[YHÔË˜Ø[•˜[YˆÛÛ›ÛÔİ]HH]˜ÛÛ›ÛÔİ]BˆYˆİ\œ™[Ø[\‚ˆÛÛ›ÛÔİ]K˜[\^HHİ\œ™[Ø[\˜[\İ^ÌBˆÛÛ›ÛÔİ]K˜[\^ˆHİ\œ™[Ø[\˜[\İ^Ì‚ˆÛÛ›ÛÔİ]K˜[\Ú^™HHİ\œ™[Ø[\˜[\ÜÚ^™BˆÛÛ›ÛÔİ]K˜[\İ]\ÈHİ\œ™[Ø[\˜[\Üİ]\ÂˆÛÛ›ÛÔİ]K˜[\›[šÚ[™Ô˜]HHİ\œ™[Ø[\˜[\Ü˜]BˆÛÛ›ÛÔİ]K˜[\\HHİ\œ™[Ø[\˜[\İ\BˆÛÛ›ÛÔİ]K˜[\Ûİ[™Hİ\œ™[Ø[\˜]YX›WØ[\‚ˆÛÛ›ÛÔİ]K˜Ø[“[Û›Õ[Y\ÈH\İ
ÔË˜Ø[“[Û›Õ[Y\ÊBˆÛÛ›ÛÔİ]K›Û™Ú]Y[˜[[“[Û›Õ[YHHÙ[‹œÛK›ÙÓ[Û›Õ[YVÉÛÛ™Ú]Y[˜[[‰×BˆÛÛ›ÛÔİ]K›]\˜[[“[Û›Õ[YHHÙ[‹œÛK›ÙÓ[Û›Õ[YVÉÛ]\˜[[‰×BˆÛÛ›ÛÔİ]K™[˜X›YHÙ[‹™[˜X›YˆÛÛ›ÛÔİ]K˜Xİ]™HHÙ[‹˜Xİ]™BˆÛÛ›ÛÔİ]K˜İ\˜]\™HHİ\˜]\™BˆÛÛ›ÛÔİ]K™\Ú\™Yİ\˜]\™HHÙ[‹™\Ú\™YØİ\˜]\™BˆÛÛ›ÛÔİ]K™\Ú\™Yİ\˜]\™T˜]HHÙ[‹™\Ú\™YØİ\˜]\™WÜ˜]BˆÛÛ›ÛÔİ]Kœİ]HHÙ[‹œİ]BˆÛÛ›ÛÔİ]K™[™ØYÙXX›HH›İÙ[‹™]™[Ë˜[JU““×ÑS•–JBˆÛÛ›ÛÔİ]K›Û™ĞÛÛ›Ûİ]HHÙ[‹“ĞË›Û™×ØÛÛ›ÛÜİ]BˆÛÛ›ÛÔİ]K”YH›Ø]
Ù[‹“ĞË—ÜY
BˆÛÛ›ÛÔİ]KÜZ\ÙHH›Ø]
Ù[‹˜\SX^ÜYYYˆÙ[‹Ô›Ü[œ[İÛ™Ú]Y[˜[ÛÛ›Û[ÙHÙ[‹—ØÜZ\ÙWÚÜ
BˆÛÛ›ÛÔİ]KÜZ\ÙPÛ\İ\ˆH›Ø]
Ù[‹—ØÜZ\ÙWØÛ\İ\—ÚÜ
BˆÛÛ›ÛÔİ]K\XØÙ[ÛYH›Ø]
Ù[‹“ĞËœYœ
BˆÛÛ›ÛÔİ]KZPXØÙ[ÛYH›Ø]
Ù[‹“ĞËœYšJBˆÛÛ›ÛÔİ]KYXØÙ[ÛYH›Ø]
Ù[‹“ĞËœY™ŠBˆÛÛ›ÛÔİ]K˜İ[SYÓ\ÈH\Ù[‹œšËœ™[XZ[š[™È
ˆL‚ˆÛÛ›ÛÔİ]Kœİ\[Û›Õ[YHH[
İ\İ[YH
ˆYNJBˆÛÛ›ÛÔİ]K™›Ü˜ÙQXÙ[H›ÛÛ
›Ü˜ÙWÙXÙ[
BˆÛÛ›ÛÔİ]K˜Ø[‘\œ›ÜÛİ[\ˆHÙ[‹˜Ø[—Ü˜İ—Ù\œ›Ü—ØÛİ[\‚‚ˆÛÛ›ÛÔİ]K˜[™ÛTİY\œÈHİY\—Ø[™ÛWİÚ]İ]ÛÙ™œÙ]
ˆÕ‹”QÕ×ÑQÂˆÛÛ›ÛÔİ]K˜\PXØÙ[HÙ[‹˜\WØXØÙ[ˆÛÛ›ÛÔİ]K˜T™\U˜[YHHÙ[‹˜T™\U˜[YBˆÛÛ›ÛÔİ]K˜T™\U˜[YSZ[ˆHÙ[‹˜T™\U˜[YSZ[‚ˆÛÛ›ÛÔİ]K˜T™\U˜[YSX^HÙ[‹˜T™\U˜[YSX^ˆÛÛ›ÛÔİ]KœØØÔİØÚĞØ[PXİHÙ[‹œØØÔİØÚĞØ[PXİˆÛÛ›ÛÔİ]KœØØÔİØÚĞØ[Tİ]\ÈHÙ[‹œØØÔİØÚĞØ[Tİ]\Â‚ˆÛÛ›ÛÔİ]KœİY\”˜][ÈHÙ[‹•“KœÔ‚ˆÛÛ›ÛÔİ]KœİY\XİX]Ü‘[^HH]™Wİ[™KœİY\—ØXİX]Ü—Ù[^J
B‚ˆ]İ[š[™ÈHÙ[‹Ô›]\˜[[š[™ËÚXÚ

BˆYˆÙ[‹š›Ş\İXÚ×Û[ÙN‚ˆÛÛ›ÛÔİ]K›]\˜[ÛÛ›Ûİ]K™XYÔİ]HHX×ÛÙÂˆ[YˆÙ[‹ÔœİY\ÛÛ›Û\HOHØ\‹Ø\”\˜[\Ë”İY\ÛÛ›Û\K˜[™ÛN‚ˆÛÛ›ÛÔİ]K›]\˜[ÛÛ›Ûİ]K˜[™ÛTİ]HHX×ÛÙÂˆ[Yˆ]İ[š[™ÈOH	ÜY	Î‚ˆÛÛ›ÛÔİ]K›]\˜[ÛÛ›Ûİ]KœYİ]HHX×ÛÙÂˆ[Yˆ]İ[š[™ÈOH	Ú[™IÎ‚ˆÛÛ›ÛÔİ]K›]\˜[ÛÛ›Ûİ]Kš[™Tİ]HHX×ÛÙÂˆ[Yˆ]İ[š[™ÈOH	İÜœ]YIÎ‚ˆÛÛ›ÛÔİ]K›]\˜[ÛÛ›Ûİ]KÜœ]YTİ]HHX×ÛÙÂ‚ˆÙ[‹œKœÙ[™
	ØÛÛ›ÛÔİ]IË]
B‚ˆÈØ\”İ]BˆØ\—Ù]™[ÈHÙ[‹™]™[Ë×Û\ÙÊ
BˆÜ×ÜÙ[™HY\ÜØYÚ[™Ë›™]×ÛY\ÜØYÙJ	ØØ\”İ]IÊBˆÜ×ÜÙ[™˜[YHÔË˜Ø[•˜[YˆÜ×ÜÙ[™˜Ø\”İ]HHÔÂˆÜ×ÜÙ[™˜Ø\”İ]K™]™[ÈHØ\—Ù]™[ÂˆÙ[‹œKœÙ[™
	ØØ\”İ]IËÜ×ÜÙ[™
B‚ˆÈØ\‘]™[ÈHÙÙÙY]™\HÙXÛÛ™ÜˆÛˆÚ[™ÙBˆYˆ
Ù[‹œÛK™œ˜[YH	H[
KˆÈĞÕ“
HOH
HÜˆ
Ù[‹™]™[Ë›˜[Y\ÈOHÙ[‹™]™[×Ü™]ŠN‚ˆÙWÜÙ[™HY\ÜØYÚ[™Ë›™]×ÛY\ÜØYÙJ	ØØ\‘]™[ÉË[ŠÙ[‹™]™[ÊJBˆÙWÜÙ[™˜Ø\‘]™[ÈHØ\—Ù]™[ÂˆÙ[‹œKœÙ[™
	ØØ\‘]™[ÉËÙWÜÙ[™
BˆÙ[‹™]™[×Ü™]ˆHÙ[‹™]™[Ë›˜[Y\Ë˜ÛÜJ
B‚ˆÈØ\”\˜[\ÈHÙÙÙY]™\HLÙXÛÛ™È
ˆH\ˆÙYÛY[
BˆYˆ
Ù[‹œÛK™œ˜[YH	H[
LˆÈĞÕ“
HOH
N‚ˆÜÜÙ[™HY\ÜØYÚ[™Ë›™]×ÛY\ÜØYÙJ	ØØ\”\˜[\ÉÊBˆÜÜÙ[™˜Ø\”\˜[\ÈHÙ[‹ÔˆÙ[‹œKœÙ[™
	ØØ\”\˜[\ÉËÜÜÙ[™
B‚ˆÈØ\ÛÛ›ÛˆØ×ÜÙ[™HY\ÜØYÚ[™Ë›™]×ÛY\ÜØYÙJ	ØØ\ÛÛ›Û	ÊBˆØ×ÜÙ[™˜[YHÔË˜Ø[•˜[YˆØ×ÜÙ[™˜Ø\ÛÛ›ÛHĞÂˆÙ[‹œKœÙ[™
	ØØ\ÛÛ›Û	ËØ×ÜÙ[™
B‚ˆÈÛÜHØ\ÛÛ›ÛÈ\ÜÈÈØ\’[\™˜XÙHÛˆH™^]\˜][Û‚ˆÙ[‹ĞÈHĞÂ‚ˆYˆİ\
Ù[ŠN‚ˆİ\İ[YHHÙX×ÜÚ[˜ÙWØ›Ûİ

BˆÙ[‹œ›Ù‹˜ÚXÚÜÚ[
”˜]ZÙY\\ˆ‹YÛ›Ü™OUYJB‚ˆÈØ[\H]Hœ›ÛHÛØÚÙ]È[™Ù]HØ\”İ]BˆÔÈHÙ[‹™]WÜØ[\J
BˆÛİYÙË[Y\İ[\
‘]HØ[\YŠBˆÙ[‹œ›Ù‹˜ÚXÚÜÚ[
”Ø[\HŠB‚ˆÙ[‹\]WÙ]™[ÊÔÊBˆÛİYÙË[Y\İ[\
‘]™[È\]YŠB‚ˆYˆ›İÙ[‹œ™XYÛÛ›H[™Ù[‹š[š]X[^™Y‚ˆÈ\]HÛÛ›Ûİ]BˆÙ[‹œİ]Wİ˜[œÚ][ÛŠÔÊBˆÙ[‹œ›Ù‹˜ÚXÚÜÚ[
”İ]H˜[œÚ][ÛˆŠB‚ˆÈÛÛ\]HXİX]ÜœÈ
[œÈQÛÜÈ[™]\˜[TÊBˆĞËX×ÛÙÈHÙ[‹œİ]WØÛÛ›Û
ÔÊB‚ˆÙ[‹œ›Ù‹˜ÚXÚÜÚ[
”İ]HÛÛ›ÛŠB‚ˆÈX›\Ú]BˆÙ[‹œX›\ÚÛÙÜÊÔËİ\İ[YKĞËX×ÛÙÊBˆÙ[‹œ›Ù‹˜ÚXÚÜÚ[
”Ù[ŠB‚ˆÙ[‹\]WØ]Û—İ[Y\œÊÔË˜]Û‘]™[ÊBˆÙ[‹Ô×Ü™]ˆHÔÂ‚ˆYˆÛÛ›ÛÙİ™XY
Ù[ŠN‚ˆÚ[HYN‚ˆÙ[‹œİ\

BˆÙ[‹œšË›[Ûš]Ü—İ[YJ
BˆÙ[‹œ›Ù‹™\Ü^J
B‚‚™YˆXZ[ŠÛOS›Û™KOS›Û™KÙØØ[S›Û™JN‚ˆÛÛ›ÛÈHÛÛ›ÛÊÛKKÙØØ[ŠBˆÛÛ›ÛË˜ÛÛ›ÛÙİ™XY

B‚‚šYˆ×Û˜[YW×ÈOH—×ÛXZ[—×È‚ˆXZ[Š
B