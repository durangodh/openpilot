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
                          pandaState.safetyParam != self.CP.sãN=¶‰žËkºwµçPÔË˜]Û‘]™[ÊCBˆÛÙÚÛØ]˜Z[X›HH
Ù[‹œÛÙÚÛÙ[˜X›Y[™Ù[‹Ô›Ü[œ[ÝÛ™Ú]Y[˜[ÛÛ›Û[™BˆÙ[‹˜XÝ]™H[™
ÔË˜ÜZ\ÙTÝ]K™[˜X›YXØÈÜˆÙ[‹œÛÙÚÛ˜XÝ]™JJCBˆÛÙÚÛØXÝ]™HHÙ[‹œÛÙÚÛ\]JÛÙÚÛØ]˜Z[X›KÔË˜œ˜ZÙT™\ÜÙYÔË™Ø\Ô™\ÜÙYˆÔË‘YÛË™\Ý[YWÜ™\ÜÙYÔË™ÙX\”ÚY\ˆOHÙX\”ÚY\‹™š]™KÐÕ“
BƒBˆÐÈHØ\‹Ø\ÛÛ›Û›™]×ÛY\ÜØYÙJ
CBˆÐË™[˜X›YHÙ[‹™[˜X›YBˆÈÚXÚÈÚXÚXÝX]ÜœÈØ[ˆ™H[˜X›YBˆÐË›]XÝ]™HHÙ[‹˜XÝ]™H[™›ÝÔËœÝY\‘˜][[\Ü˜\žH[™›ÝÔËœÝY\‘˜][\›X[™[[™BˆÔË‘YÛÈˆÙ[‹Ô›Z[”ÝY\”ÜYY[™›ÝÔËœÝ[™Ý[Bˆ[™XœÊÔËœÝY\š[™Ð[™ÛQYÊHÙ[‹Ô›X^ÝY\š[™Ð[™ÛQYÃBˆÐË›Û™ÐXÝ]™HHÙ[‹˜XÝ]™H[™›ÝÙ[‹™]™[Ë˜[žJU“Õ‘T”’QJH[™Ù[‹Ô›Ü[œ[ÝÛ™Ú]Y[˜[ÛÛ›ÛBƒBˆXÝX]ÜœÈHÐË˜XÝX]ÜœÃBˆXÝX]ÜœË›Û™ÐÛÛ›ÛÝ]HHÙ[‹“ÐË›Û™×ØÛÛ›ÛÜÝ]CBƒBˆYˆÔË›Y›[šÙ\ˆÜˆÔËœšYÚ›[šÙ\ŽƒBˆÙ[‹›\ÝØ›[šÙ\—Ùœ˜[YHHÙ[‹œÛK™œ˜[YCBƒBˆÈÝ]HÜXÚYšXÈXÝ[ÛœÃBˆYˆ›ÝÐË›]XÝ]™NƒBˆÙ[‹“PËœ™\Ù]

CBˆYˆ›ÝÐË›Û™ÐXÝ]™NƒBˆÙ[‹“ÐËœ™\Ù]
—ÜYPÔË‘YÛÊCBƒBˆYˆ›ÝÔË˜ÜZ\ÙTÝ]K™[˜X›YXØÈ[™›ÝÛÙÚÛØXÝ]™NƒBˆÙ[‹“ÐËœ™\Ù]
—ÜYPÔË‘YÛÊCBƒBˆYˆ›ÝÙ[‹š›Þ\ÝXÚ×Û[ÙNƒBˆÈXØÙ[QÛÜBˆYØXØÙ[Û[Z]ÈHÙ[‹ÒK™Ù]ÜYØXØÙ[Û[Z]ÊÙ[‹ÔÔË‘YÛËÙ[‹—ØÜZ\ÙWÚÜ
ˆÕ‹’ÔÕ×ÓTÊCBˆÜÚ[˜ÙWÜ[ˆH
Ù[‹œÛK™œ˜[YHHÙ[‹œÛKœ˜Ý—Ùœ˜[YVÉÛÛ™Ú]Y[˜[[‰×JH
ˆÐÕ“BˆXÝX]ÜœË˜XØÙ[HÙ[‹“ÐË\]JÐË›Û™ÐXÝ]™H[™
ÔË˜ÜZ\ÙTÝ]K™[˜X›YXØÈÜˆÛÙÚÛØXÝ]™JKBˆÔËÛ™×Ü[‹YØXØÙ[Û[Z]ËÜÚ[˜ÙWÜ[‹ˆÙ[‹œÛVÉÜ˜Y\”Ý]I×KÛÙÚÛØXÝ]™KÙ[‹œÛÙÚÛœ™[X\ÙY
BƒBˆÈÝY\š[™ÈQÛÜ[™]\˜[TÃBˆÙ[‹™\Ú\™YØÝ\˜]\™KÙ[‹™\Ú\™YØÝ\˜]\™WÜ˜]HHÙ]ÛY×ØY\ÝYØÝ\˜]\™JÙ[‹ÔÔË‘YÛËBˆ]Ü[‹œÚ\ËBˆ]Ü[‹˜Ý\˜]\™\ËBˆ]Ü[‹˜Ý\˜]\™T˜]\ÊCBˆXÝX]ÜœËœÝY\‹XÝX]ÜœËœÝY\š[™Ð[™ÛQYËX×ÛÙÈHÙ[‹“PË\]JÐË›]XÝ]™KÔËÙ[‹•“K\˜[\ËBˆÙ[‹›\ÝØXÝX]ÜœËÙ[‹œÝY\—Û[Z]YÙ[‹™\Ú\™YØÝ\˜]\™KBˆÙ[‹™\Ú\™YØÝ\˜]\™WÜ˜]KÙ[‹œÛVÉÛ]™SØØ][Û’Ø[X[‰×KBˆÙ[‹œÛVÉÛ[Ù[Œ‰×JCBˆ[ÙNƒBˆX×ÛÙÈHÙËÛÛ›ÛÔÝ]K“]\˜[XYÔÝ]K›™]×ÛY\ÜØYÙJ
CBˆYˆÙ[‹œÛKœ˜Ý—Ùœ˜[YVÉÝ\Ý›Þ\ÝXÚÉ×HˆƒBˆYˆÐË›Û™ÐXÝ]™NƒBˆXÝX]ÜœË˜XØÙ[HŒ
˜Û\
Ù[‹œÛVÉÝ\Ý›Þ\ÝXÚÉ×K˜^\ÖÌKLKJCBƒBˆYˆÐË›]XÝ]™NƒBˆÝY\ˆHÛ\
Ù[‹œÛVÉÝ\Ý›Þ\ÝXÚÉ×K˜^\ÖÌWKLKJCBˆÈX^[™ÛH\ÈH›Üˆ[™ÛKX˜\ÙYØ\œÃBˆXÝX]ÜœËœÝY\‹XÝX]ÜœËœÝY\š[™Ð[™ÛQYÈHÝY\‹ÝY\ˆ
ˆKƒBƒBˆX×ÛÙË˜XÝ]™HHÙ[‹˜XÝ]™CBˆX×ÛÙËœÝY\š[™Ð[™ÛQYÈHÔËœÝY\š[™Ð[™ÛQYÃBˆX×ÛÙË›Ý]]HXÝX]ÜœËœÝY\ƒBˆX×ÛÙËœØ]\˜]YHXœÊXÝX]ÜœËœÝY\ŠHHŽCBƒBˆÈÙ[™HœÝY\š[™È™\]Z\™Y[\ˆYˆØ]\˜][ÛˆÛÝ[\È™XXÚYH[Z]BˆYˆX×ÛÙË˜XÝ]™H[™›ÝÔËœÝY\š[™Ô™\ÜÙY[™Ù[‹Ô›]\˜[[š[™ËÚXÚ

HOH	ÝÜœ]YIÈ[™›ÝÙ[‹š›Þ\ÝXÚ×Û[ÙNƒBˆ[™\œÚÛÝ[™ÈHXœÊX×ÛÙË™\Ú\™Y]\˜[XØÙ[
HÈXœÊYKLÈ
ÈX×ÛÙË˜XÝX[]\˜[XØÙ[
HˆKŒÃBˆ\›š[™ÈHXœÊX×ÛÙË™\Ú\™Y]\˜[XØÙ[
HˆKŒBˆÛÛÙÜÜYYHÔË‘YÛÈˆCBˆX^ÝÜœ]YHHXœÊÙ[‹›\ÝØXÝX]ÜœËœÝY\ŠHˆŽNCBˆYˆ[™\œÚÛÝ[™È[™\›š[™È[™ÛÛÙÜÜYY[™X^ÝÜœ]YNƒBˆÙ[‹™]™[Ë˜Y
]™[˜[YKœÝY\”Ø]\˜]Y
CBˆ[YˆX×ÛÙË˜XÝ]™H[™›ÝÔËœÝY\š[™Ô™\ÜÙY[™X×ÛÙËœØ]\˜]YƒBˆ]ÜÚ[ÈH]Ü[‹™]Ú[ÃBˆYˆ[Š]ÜÚ[ÊNƒBˆÈÚXÚÈYˆÙH]šX]Yœ›ÛHH]BˆÈÑÈ\ÙH\Ú\™YœÈXÝX[Ý\˜]\™CBˆYˆÙ[‹ÔœÝY\ÛÛ›Û\HOHØ\‹Ø\”\˜[\Ë”ÝY\ÛÛ›Û\K˜[™ÛNƒBˆÝY\š[™×Ý˜[YHHXÝX]ÜœËœÝY\š[™Ð[™ÛQYÃBˆ[ÙNƒBˆÝY\š[™×Ý˜[YHHXÝX]ÜœËœÝY\ƒBƒBˆYÙ]šX][ÛˆHÝY\š[™×Ý˜[YHˆ[™]ÜÚ[ÖÌHLŒŒBˆšYÚÙ]šX][ÛˆHÝY\š[™×Ý˜[YH[™]ÜÚ[ÖÌHˆŒŒBƒBˆYˆYÙ]šX][ÛˆÜˆšYÚÙ]šX][ÛŽƒBˆÙ[‹™]™[Ë˜Y
]™[˜[YKœÝY\”Ø]\˜]Y
CBƒBˆÈ[œÝ\™H›È˜SœËÒ[™œÃBˆ›Üˆ[ˆPÕPUÔ—Ñ’QSÎƒBˆ]ˆHÙ]]ŠXÝX]ÜœË
CBˆYˆ›Ý\Ú[œÝ[˜ÙJ]‹[X™\ŠNƒBˆÛÛ[YCBƒBˆYˆ›ÝX]š\Ùš[š]J]ŠNƒBˆÛÝYÙË™\œ›ÜŠˆ˜XÝX]ÜœËžÜH›Ýš[š]HØXÝX]ÜœË×ÙXÝ

_HŠCBˆÙ]]ŠXÝX]ÜœËŒ
CBƒBˆ™]\›ˆÐËX×ÛÙÃBƒBˆYˆ\]WØ]Û—Ý[Y\œÊÙ[‹]Û‘]™[ÊNƒBˆÈ[˜Ü™[Y[[Y\ˆ›Üˆ]ÛœÈÝ[™\ÜÙYBˆ›ÜˆÈ[ˆÙ[‹˜]Û—Ý[Y\œÎƒBˆYˆÙ[‹˜]Û—Ý[Y\œÖÚ×HˆƒBˆÙ[‹˜]Û—Ý[Y\œÖÚ×H
ÏHCBƒBˆ›Üˆˆ[ˆ]Û‘]™[ÎƒBˆYˆ‹\Kœ˜]È[ˆÙ[‹˜]Û—Ý[Y\œÎƒBˆÙ[‹˜]Û—Ý[Y\œÖØ‹\Kœ˜]×HHHYˆ‹œ™\ÜÙY[ÙHBƒBˆYˆX›\ÚÛÙÜÊÙ[‹ÔËÝ\Ý[YKÐËX×ÛÙÊNƒBˆˆˆ”Ù[™XÝX]ÜœÈ[™YÛÛ[X[™ÈÈHØ\‹Ù[™ÛÛ›ÛÜÝ]H[™TÈÙÙÚ[™ÈˆˆƒBƒBˆÈÜšY[][Ûˆ[™[™ÛH˜]\ÈØ[ˆ™H\ÙY[›ÜˆØ\˜ÛÛ›Û\ƒBˆÈÛ›HØ[Xœ˜]Y
Ø\ŠHœ˜[YH\È™[]˜[›ÜˆHØ\˜ÛÛ›Û\ƒBˆÜšY[][Û—Ý˜[YHH\Ý
Ù[‹œÛVÉÛ]™SØØ][Û’Ø[X[‰×K˜Ø[Xœ˜]YÜšY[][Û“‘Q˜[YJCBˆYˆ[ŠÜšY[][Û—Ý˜[YJHˆŽƒBˆÐË›ÜšY[][Û“‘QHÜšY[][Û—Ý˜[YCBˆ[™Ý[\—Ü˜]WÝ˜[YHH\Ý
Ù[‹œÛVÉÛ]™SØØ][Û’Ø[X[‰×K˜[™Ý[\•™[ØÚ]PØ[Xœ˜]Y˜[YJCBˆYˆ[Š[™Ý[\—Ü˜]WÝ˜[YJHˆŽƒBˆÐË˜[™Ý[\•™[ØÚ]HH[™Ý[\—Ü˜]WÝ˜[YCBƒBˆÐË˜ÜZ\ÙPÛÛ›Û˜Ø[˜Ù[HÙ[‹ÔœÛPÜZ\ÙH[™›ÝÙ[‹™[˜X›Y[™ÔË˜ÜZ\ÙTÝ]K™[˜X›YBˆYˆÙ[‹š›Þ\ÝXÚ×Û[ÙH[™Ù[‹œÛKœ˜Ý—Ùœ˜[YVÉÝ\Ý›Þ\ÝXÚÉ×Hˆ[™Ù[‹œÛVÉÝ\Ý›Þ\ÝXÚÉ×K˜]ÛœÖÌNƒBˆÐË˜ÜZ\ÙPÛÛ›Û˜Ø[˜Ù[HYCBƒBˆÜYYÈHÙ[‹œÛVÉÛÛ™Ú]Y[˜[[‰×KœÜYYÃBˆYˆ[ŠÜYYÊNƒBˆÐË˜ÜZ\ÙPÛÛ›Ûœ™\Ý[YHH
Ù[‹™[˜X›Y[™›ÝÙ[‹œÛÙÚÛ˜XÝ]™H[™BˆÔË˜ÜZ\ÙTÝ]KœÝ[™Ý[[™ÜYYÖËLWHˆŒJCBƒBˆYÛÛ›ÛHÐËšYÛÛ›ÛBˆYÛÛ›ÛœÛÙÛHÙ[‹œÛÙÚÛ˜XÝ]™CBˆYÛÛ›ÛœÙ]ÜYYH›Ø]
Ù[‹—ØÜZ\ÙWØÛ\Ý\—ÚÜ
ˆÕ‹’ÔÕ×ÓTÊCBˆYÛÛ›ÛœÜYYš\ÚX›HHÙ[‹™[˜X›YBˆYÛÛ›Û›[™\Õš\ÚX›HHÙ[‹™[˜X›YBˆYÛÛ›Û›XYš\ÚX›HHÙ[‹œÛVÉÛÛ™Ú]Y[˜[[‰×Kš\ÓXYBƒBˆšYÚÛ[™WÝš\ÚX›HHÙ[‹œÛVÉÛ]\˜[[‰×Kœ”›ØˆˆCBˆYÛ[™WÝš\ÚX›HHÙ[‹œÛVÉÛ]\˜[[‰×K››ØˆˆCBƒBˆYˆÙ[‹œÛK™œ˜[YH	HLOHƒBˆÙ[‹œšYÚÛ[™WÝš\ÚX›HHšYÚÛ[™WÝš\ÚX›CBˆÙ[‹›YÛ[™WÝš\ÚX›HHYÛ[™WÝš\ÚX›CBƒBˆYÛÛ›ÛœšYÚ[™Uš\ÚX›HHÙ[‹œšYÚÛ[™WÝš\ÚX›CBˆYÛÛ›Û›Y[™Uš\ÚX›HHÙ[‹›YÛ[™WÝš\ÚX›CBƒBˆ™XÙ[Ø›[šÙ\ˆH
Ù[‹œÛK™œ˜[YHHÙ[‹›\ÝØ›[šÙ\—Ùœ˜[YJH
ˆÐÕ“KŒÈ\È›[šÙ\ˆÛÛÛÝÛƒBˆ×Ø[ÝÙYHÙ[‹š\×Û×Ù[˜X›Y[™ÔË‘YÛÈˆ×ÓRS—ÔÔQQ[™›Ý™XÙ[Ø›[šÙ\ˆBˆ[™›ÝÐË›]XÝ]™H[™Ù[‹œÛVÉÛ]™PØ[Xœ˜][Û‰×K˜Ø[Ý]\ÈOHØ[Xœ˜][Û‹ÐSP”UQBƒBˆ[Ù[ÝŒˆHÙ[‹œÛVÉÛ[Ù[Œ‰×CBˆ\Ú\™WÜ™YXÝ[ÛˆH[Ù[ÝŒ‹›Y]K™\Ú\™T™YXÝ[ÛƒBˆYˆ[Š\Ú\™WÜ™YXÝ[ÛŠH[™×Ø[ÝÙYƒBˆšYÚÛ[™WÝš\ÚX›HH[Ù[ÝŒ‹›[™S[™T›ØœÖÌ—HˆCBˆYÛ[™WÝš\ÚX›HH[Ù[ÝŒ‹›[™S[™T›ØœÖÌWHˆCBˆÛ[™WØÚ[™ÙWÜ›ØˆH\Ú\™WÜ™YXÝ[Û–Ñ\Ú\™K›[™PÚ[™ÙSYHWCBˆ—Û[™WØÚ[™ÙWÜ›ØˆH\Ú\™WÜ™YXÝ[Û–Ñ\Ú\™K›[™PÚ[™ÙTšYÚHWCBƒBˆ[™WÛ[™\ÈH[Ù[ÝŒ‹›[™S[™\ÃBˆÛ[™WØÛÜÙHHYÛ[™WÝš\ÚX›H[™
[™WÛ[™\ÖÌWKžVÌHˆJKŒ
ÈÐSQTWÓÑ‘”ÑU
JCBˆ—Û[™WØÛÜÙHHšYÚÛ[™WÝš\ÚX›H[™
[™WÛ[™\ÖÌ—KžVÌH
KŒHÐSQTWÓÑ‘”ÑU
JCBƒBˆYÛÛ›Û›Y[™Q\\H›ÛÛ
Û[™WØÚ[™ÙWÜ›ØˆˆS‘WÑTT•T‘WÕ‘TÒÓ[™Û[™WØÛÜÙJCBˆYÛÛ›ÛœšYÚ[™Q\\H›ÛÛ
—Û[™WØÚ[™ÙWÜ›ØˆˆS‘WÑTT•T‘WÕ‘TÒÓ[™—Û[™WØÛÜÙJCBƒBˆYˆYÛÛ›ÛœšYÚ[™Q\\ÜˆYÛÛ›Û›Y[™Q\\ƒBˆÙ[‹™]™[Ë˜Y
]™[˜[YK›ÊCBƒBˆÛX\—Ù]™[Ý\\ÈHÙ]

CBˆYˆU•ÐT“’S‘È›Ý[ˆÙ[‹˜Ý\œ™[Ø[\Ý\\ÎƒBˆÛX\—Ù]™[Ý\\Ë˜Y
U•ÐT“’S‘ÊCBˆYˆÙ[‹™[˜X›YƒBˆÛX\—Ù]™[Ý\\Ë˜Y
U““×ÑS•–JCBƒBˆ[\ÈHÙ[‹™]™[Ë˜Ü™X]WØ[\ÊÙ[‹˜Ý\œ™[Ø[\Ý\\ËÜÙ[‹ÔÙ[‹œÛKÙ[‹š\×ÛY]šXËÙ[‹œÛÙÙ\ØX›WÝ[Y\—JCBˆÙ[‹SK˜YÛX[žJÙ[‹œÛK™œ˜[YK[\ÊCBˆÝ\œ™[Ø[\HÙ[‹SKœ›ØÙ\Ü×Ø[\ÊÙ[‹œÛK™œ˜[YKÛX\—Ù]™[Ý\\ÊCBˆYˆÝ\œ™[Ø[\ƒBˆYÛÛ›Ûš\ÝX[[\HÝ\œ™[Ø[\š\ÝX[Ø[\BƒBˆYˆ›ÝÙ[‹œ™XYÛÛ›H[™Ù[‹š[š]X[^™YƒBˆÈÙ[™Ø\ˆÛÛ›ÛÈÝ™\ˆØ[ƒBˆÙ[‹›\ÝØXÝX]ÜœËØ[—ÜÙ[™ÈHÙ[‹ÒK˜\JÐËÙ[ŠCBˆÙ[‹œKœÙ[™
	ÜÙ[™Ø[‰ËØ[—Û\ÝÝ×ØØ[—ØØ\œ
Ø[—ÜÙ[™Ë\ÙÝ\OIÜÙ[™Ø[‰Ë˜[YPÔË˜Ø[•˜[Y
JCBˆÐË˜XÝX]ÜœÓÝ]]HÙ[‹›\ÝØXÝX]ÜœÃBˆÙ[‹œÝY\—Û[Z]YHXœÊÐË˜XÝX]ÜœËœÝY\ˆHÐË˜XÝX]ÜœÓÝ]]œÝY\ŠHˆYKLƒBƒBˆ›Ü˜ÙWÙXÙ[H
Ù[‹œÛVÉÙš]™\“[Ûš]Üš[™ÔÝ]I×K˜]Ø\™[™\ÜÔÝ]\ÈŠHÜˆBˆ
Ù[‹œÝ]HOHÝ]KœÛÙ\ØX›[™ÊCBƒBˆÈÝ\˜]\™H	ˆÝY\š[™È[™ÛCBˆ\˜[\ÈHÙ[‹œÛVÉÛ]™T\˜[Y]\œÉ×CBƒBˆÝY\—Ø[™ÛWÝÚ]Ý]ÛÙ™œÙ]HX]œ˜YX[œÊÔËœÝY\š[™Ð[™ÛQYÈH\˜[\Ë˜[™ÛSÙ™œÙ]YÊCBˆÝ\˜]\™HH\Ù[‹•“K˜Ø[×ØÝ\˜]\™JÝY\—Ø[™ÛWÝÚ]Ý]ÛÙ™œÙ]ÔË‘YÛË\˜[\Ëœ›Û
CBƒBˆÈÛÛ›ÛÔÝ]CBˆ]HY\ÜØYÚ[™Ë›™]×ÛY\ÜØYÙJ	ØÛÛ›ÛÔÝ]IÊCBˆ]˜[YHÔË˜Ø[•˜[YBˆÛÛ›ÛÔÝ]HH]˜ÛÛ›ÛÔÝ]CBˆYˆÝ\œ™[Ø[\ƒBˆÛÛ›ÛÔÝ]K˜[\^HHÝ\œ™[Ø[\˜[\Ý^ÌCBˆÛÛ›ÛÔÝ]K˜[\^ˆHÝ\œ™[Ø[\˜[\Ý^ÌƒBˆÛÛ›ÛÔÝ]K˜[\Ú^™HHÝ\œ™[Ø[\˜[\ÜÚ^™CBˆÛÛ›ÛÔÝ]K˜[\Ý]\ÈHÝ\œ™[Ø[\˜[\ÜÝ]\ÃBˆÛÛ›ÛÔÝ]K˜[\›[šÚ[™Ô˜]HHÝ\œ™[Ø[\˜[\Ü˜]CBˆÛÛ›ÛÔÝ]K˜[\\HHÝ\œ™[Ø[\˜[\Ý\CBˆÛÛ›ÛÔÝ]K˜[\ÛÝ[™HÝ\œ™[Ø[\˜]YX›WØ[\BƒBˆÛÛ›ÛÔÝ]K˜Ø[“[Û›Õ[Y\ÈH\Ý
ÔË˜Ø[“[Û›Õ[Y\ÊCBˆÛÛ›ÛÔÝ]K›Û™Ú]Y[˜[[“[Û›Õ[YHHÙ[‹œÛK›ÙÓ[Û›Õ[YVÉÛÛ™Ú]Y[˜[[‰×CBˆÛÛ›ÛÔÝ]K›]\˜[[“[Û›Õ[YHHÙ[‹œÛK›ÙÓ[Û›Õ[YVÉÛ]\˜[[‰×CBˆÛÛ›ÛÔÝ]K™[˜X›YHÙ[‹™[˜X›YBˆÛÛ›ÛÔÝ]K˜XÝ]™HHÙ[‹˜XÝ]™CBˆÛÛ›ÛÔÝ]K˜Ý\˜]\™HHÝ\˜]\™CBˆÛÛ›ÛÔÝ]K™\Ú\™YÝ\˜]\™HHÙ[‹™\Ú\™YØÝ\˜]\™CBˆÛÛ›ÛÔÝ]K™\Ú\™YÝ\˜]\™T˜]HHÙ[‹™\Ú\™YØÝ\˜]\™WÜ˜]CBˆÛÛ›ÛÔÝ]KœÝ]HHÙ[‹œÝ]CBˆÛÛ›ÛÔÝ]K™[™ØYÙXX›HH›ÝÙ[‹™]™[Ë˜[žJU““×ÑS•–JCBˆÛÛ›ÛÔÝ]K›Û™ÐÛÛ›ÛÝ]HHÙ[‹“ÐË›Û™×ØÛÛ›ÛÜÝ]CBˆÛÛ›ÛÔÝ]K”YH›Ø]
Ù[‹“ÐË—ÜY
CBˆÛÛ›ÛÔÝ]KÜZ\ÙHH›Ø]
Ù[‹˜\SX^ÜYYYˆÙ[‹Ô›Ü[œ[ÝÛ™Ú]Y[˜[ÛÛ›Û[ÙHÙ[‹—ØÜZ\ÙWÚÜ
CBˆÛÛ›ÛÔÝ]KÜZ\ÙPÛ\Ý\ˆH›Ø]
Ù[‹—ØÜZ\ÙWØÛ\Ý\—ÚÜ
CBˆÛÛ›ÛÔÝ]K\XØÙ[ÛYH›Ø]
Ù[‹“ÐËœYœ
CBˆÛÛ›ÛÔÝ]KZPXØÙ[ÛYH›Ø]
Ù[‹“ÐËœYšJCBˆÛÛ›ÛÔÝ]KYXØÙ[ÛYH›Ø]
Ù[‹“ÐËœY™ŠCBˆÛÛ›ÛÔÝ]K˜Ý[SYÓ\ÈH\Ù[‹œšËœ™[XZ[š[™È
ˆLƒBˆÛÛ›ÛÔÝ]KœÝ\[Û›Õ[YHH[
Ý\Ý[YH
ˆYNJCBˆÛÛ›ÛÔÝ]K™›Ü˜ÙQXÙ[H›ÛÛ
›Ü˜ÙWÙXÙ[
CBˆÛÛ›ÛÔÝ]K˜Ø[‘\œ›ÜÛÝ[\ˆHÙ[‹˜Ø[—Ü˜Ý—Ù\œ›Ü—ØÛÝ[\ƒBƒBˆÛÛ›ÛÔÝ]K˜[™ÛTÝY\œÈHÝY\—Ø[™ÛWÝÚ]Ý]ÛÙ™œÙ]
ˆÕ‹”QÕ×ÑQÃBˆÛÛ›ÛÔÝ]K˜\PXØÙ[HÙ[‹˜\WØXØÙ[BˆÛÛ›ÛÔÝ]K˜T™\U˜[YHHÙ[‹˜T™\U˜[YCBˆÛÛ›ÛÔÝ]K˜T™\U˜[YSZ[ˆHÙ[‹˜T™\U˜[YSZ[ƒBˆÛÛ›ÛÔÝ]K˜T™\U˜[YSX^HÙ[‹˜T™\U˜[YSX^BˆÛÛ›ÛÔÝ]KœØØÔÝØÚÐØ[PXÝHÙ[‹œØØÔÝØÚÐØ[PXÝBˆÛÛ›ÛÔÝ]KœØØÔÝØÚÐØ[TÝ]\ÈHÙ[‹œØØÔÝØÚÐØ[TÝ]\ÃBƒBˆÛÛ›ÛÔÝ]KœÝY\”˜][ÈHÙ[‹•“KœÔƒBˆÛÛ›ÛÔÝ]KœÝY\XÝX]Ü‘[^HH]™WÝ[™KœÝY\—ØXÝX]Ü—Ù[^J
CBƒBˆ]Ý[š[™ÈHÙ[‹Ô›]\˜[[š[™ËÚXÚ

CBˆYˆÙ[‹š›Þ\ÝXÚ×Û[ÙNƒBˆÛÛ›ÛÔÝ]K›]\˜[ÛÛ›ÛÝ]K™XYÔÝ]HHX×ÛÙÃBˆ[YˆÙ[‹ÔœÝY\ÛÛ›Û\HOHØ\‹Ø\”\˜[\Ë”ÝY\ÛÛ›Û\K˜[™ÛNƒBˆÛÛ›ÛÔÝ]K›]\˜[ÛÛ›ÛÝ]K˜[™ÛTÝ]HHX×ÛÙÃBˆ[Yˆ]Ý[š[™ÈOH	ÜY	ÎƒBˆÛÛ›ÛÔÝ]K›]\˜[ÛÛ›ÛÝ]KœYÝ]HHX×ÛÙÃBˆ[Yˆ]Ý[š[™ÈOH	Ú[™IÎƒBˆÛÛ›ÛÔÝ]K›]\˜[ÛÛ›ÛÝ]Kš[™TÝ]HHX×ÛÙÃBˆ[Yˆ]Ý[š[™ÈOH	ÝÜœ]YIÎƒBˆÛÛ›ÛÔÝ]K›]\˜[ÛÛ›ÛÝ]KÜœ]YTÝ]HHX×ÛÙÃBƒBˆÙ[‹œKœÙ[™
	ØÛÛ›ÛÔÝ]IË]
CBƒBˆÈØ\”Ý]CBˆØ\—Ù]™[ÈHÙ[‹™]™[Ë×Û\ÙÊ
CBˆÜ×ÜÙ[™HY\ÜØYÚ[™Ë›™]×ÛY\ÜØYÙJ	ØØ\”Ý]IÊCBˆÜ×ÜÙ[™˜[YHÔË˜Ø[•˜[YBˆÜ×ÜÙ[™˜Ø\”Ý]HHÔÃBˆÜ×ÜÙ[™˜Ø\”Ý]K™]™[ÈHØ\—Ù]™[ÃBˆÙ[‹œKœÙ[™
	ØØ\”Ý]IËÜ×ÜÙ[™
CBƒBˆÈØ\‘]™[ÈHÙÙÙY]™\žHÙXÛÛ™ÜˆÛˆÚ[™ÙCBˆYˆ
Ù[‹œÛK™œ˜[YH	H[
KˆÈÐÕ“
HOH
HÜˆ
Ù[‹™]™[Ë›˜[Y\ÈOHÙ[‹™]™[×Ü™]ŠNƒBˆÙWÜÙ[™HY\ÜØYÚ[™Ë›™]×ÛY\ÜØYÙJ	ØØ\‘]™[ÉË[ŠÙ[‹™]™[ÊJCBˆÙWÜÙ[™˜Ø\‘]™[ÈHØ\—Ù]™[ÃBˆÙ[‹œKœÙ[™
	ØØ\‘]™[ÉËÙWÜÙ[™
CBˆÙ[‹™]™[×Ü™]ˆHÙ[‹™]™[Ë›˜[Y\Ë˜ÛÜJ
CBƒBˆÈØ\”\˜[\ÈHÙÙÙY]™\žHLÙXÛÛ™È
ˆH\ˆÙYÛY[
CBˆYˆ
Ù[‹œÛK™œ˜[YH	H[
LˆÈÐÕ“
HOH
NƒBˆÜÜÙ[™HY\ÜØYÚ[™Ë›™]×ÛY\ÜØYÙJ	ØØ\”\˜[\ÉÊCBˆÜÜÙ[™˜Ø\”\˜[\ÈHÙ[‹ÔBˆÙ[‹œKœÙ[™
	ØØ\”\˜[\ÉËÜÜÙ[™
CBƒBˆÈØ\ÛÛ›ÛBˆØ×ÜÙ[™HY\ÜØYÚ[™Ë›™]×ÛY\ÜØYÙJ	ØØ\ÛÛ›Û	ÊCBˆØ×ÜÙ[™˜[YHÔË˜Ø[•˜[YBˆØ×ÜÙ[™˜Ø\ÛÛ›ÛHÐÃBˆÙ[‹œKœÙ[™
	ØØ\ÛÛ›Û	ËØ×ÜÙ[™
CBƒBˆÈÛÜHØ\ÛÛ›ÛÈ\ÜÈÈØ\’[\™˜XÙHÛˆH™^]\˜][ÛƒBˆÙ[‹ÐÈHÐÃBƒBˆYˆÝ\
Ù[ŠNƒBˆÝ\Ý[YHHÙX×ÜÚ[˜ÙWØ›ÛÝ

CBˆÙ[‹œ›Ù‹˜ÚXÚÜÚ[
”˜]ZÙY\\ˆ‹YÛ›Ü™OUYJCBƒBˆÈØ[\H]Hœ›ÛHÛØÚÙ]È[™Ù]HØ\”Ý]CBˆÔÈHÙ[‹™]WÜØ[\J
CBˆÛÝYÙË[Y\Ý[\
‘]HØ[\YŠCBˆÙ[‹œ›Ù‹˜ÚXÚÜÚ[
”Ø[\HŠCBƒBˆÙ[‹\]WÙ]™[ÊÔÊCBˆÛÝYÙË[Y\Ý[\
‘]™[È\]YŠCBƒBˆYˆ›ÝÙ[‹œ™XYÛÛ›H[™Ù[‹š[š]X[^™YƒBˆÈ\]HÛÛ›ÛÝ]CBˆÙ[‹œÝ]WÝ˜[œÚ][ÛŠÔÊCBˆÙ[‹œ›Ù‹˜ÚXÚÜÚ[
”Ý]H˜[œÚ][ÛˆŠCBƒBˆÈÛÛ\]HXÝX]ÜœÈ
[œÈQÛÜÈ[™]\˜[TÊCBˆÐËX×ÛÙÈHÙ[‹œÝ]WØÛÛ›Û
ÔÊCBƒBˆÙ[‹œ›Ù‹˜ÚXÚÜÚ[
”Ý]HÛÛ›ÛŠCBƒBˆÈX›\Ú]CBˆÙ[‹œX›\ÚÛÙÜÊÔËÝ\Ý[YKÐËX×ÛÙÊCBˆÙ[‹œ›Ù‹˜ÚXÚÜÚ[
”Ù[ŠCBƒBˆÙ[‹\]WØ]Û—Ý[Y\œÊÔË˜]Û‘]™[ÊCBˆÙ[‹Ô×Ü™]ˆHÔÃBƒBˆYˆÛÛ›ÛÙÝ™XY
Ù[ŠNƒBˆÚ[HYNƒBˆÙ[‹œÝ\

CBˆÙ[‹œšË›[Ûš]Ü—Ý[YJ
CBˆÙ[‹œ›Ù‹™\Ü^J
CBƒBƒB™YˆXZ[ŠÛOS›Û™KOS›Û™KÙØØ[S›Û™JNƒBˆÛÛ›ÛÈHÛÛ›ÛÊÛKKÙØØ[ŠCBˆÛÛ›ÛË˜ÛÛ›ÛÙÝ™XY

CBƒBƒBšYˆ×Û˜[YW×ÈOH—×ÛXZ[—×ÈŽƒBˆXZ[Š
CB