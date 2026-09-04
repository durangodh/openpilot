#!/usr/bin/env python3
import math

from cereal import car
from opendbc.can.parser import CANParser
from selfdrive.car.interfaces import RadarInterfaceBase
from selfdrive.car.hyundai.scc_lead_tracker import SCCLeadTracker
from selfdrive.car.hyundai.values import DBC

RADAR_START_ADDR = 0x500
RADAR_MSG_COUNT = 32

def get_radar_can_parser(CP):

  if False: #Params().get_bool("NewRadarInterface"):

    signals = []
    checks = []

    for addr in range(RADAR_START_ADDR, RADAR_START_ADDR + RADAR_MSG_COUNT):
      msg = f"RADAR_TRACK_{addr:x}"
      signals += [
        ("STATE", msg),
        ("AZIMUTH", msg),
        ("LONG_DIST", msg),
        ("REL_ACCEL", msg),
        ("REL_SPEED", msg),
      ]
      checks += [(msg, 50)]
    return CANParser('hyundai_kia_mando_front_radar', signals, checks, 1)

  else:
    signals = [
      # sig_name, sig_address, default
      ("ObjValid", "SCC11"),
      ("ACC_ObjStatus", "SCC11"),
      ("ACC_ObjLatPos", "SCC11"),
      ("ACC_ObjDist", "SCC11"),
      ("ACC_ObjRelSpd", "SCC11"),
    ]
    checks = [
      ("SCC11", 50),
    ]
    return CANParser(DBC[CP.carFingerprint]['pt'], signals, checks, CP.sccBus)


class RadarInterface(RadarInterfaceBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.new_radar = False #Params().get_bool("NewRadarInterface")
    self.scc_only = not self.new_radar
    self.updated_messages = set()
    self.trigger_msg = 0x420 if not self.new_radar else RADAR_START_ADDR + RADAR_MSG_COUNT - 1
    self.track_id = 0

    self.radar_off_can = CP.radarOffCan
    self.rcp = get_radar_can_parser(CP)

    self.scc_lead_tracker = SCCLeadTracker()

  def update(self, can_strings):
    if self.radar_off_can or (self.rcp is None):
      return super().update(None)

    vls = self.rcp.update_strings(can_strings)
    self.updated_messages.update(vls)

    if self.trigger_msg not in self.updated_messages:
      return None

    rr = self._update(self.updated_messages)
    self.updated_messages.clear()

    return rr

  def _update(self, updated_messages):
    ret = car.RadarData.new_message()
    if self.rcp is None:
      return ret

    errors = []

    if not self.rcp.can_valid:
      errors.append("canError")
    ret.errors = errors

    if self.new_radar:

      for addr in range(RADAR_START_ADDR, RADAR_START_ADDR + RADAR_MSG_COUNT):
        msg = self.rcp.vl[f"RADAR_TRACK_{addr:x}"]

        if addr not in self.pts:
          self.pts[addr] = car.RadarData.RadarPoint.new_message()
          self.pts[addr].trackId = self.track_id
          self.track_id += 1

        valid = msg['STATE'] in [3, 4]
        if valid:
          azimuth = math.radians(msg['AZIMUTH'])
          self.pts[addr].measured = True
          self.pts[addr].dRel = math.cos(azimuth) * msg['LONG_DIST']
          self.pts[addr].yRel = 0.5 * -math.sin(azimuth) * msg['LONG_DIST']
          self.pts[addr].vRel = msg['REL_SPEED']
          self.pts[addr].aRel = msg['REL_ACCEL']
          self.pts[addr].yvRel = float('nan')

        else:
          del self.pts[addr]

      ret.points = list(self.pts.values())
      return ret

    else:
      cpt = self.rcp.vl
      scc11 = cpt["SCC11"]
      sample = self.scc_lead_tracker.update(
        scc11['ObjValid'],
        scc11['ACC_ObjStatus'],
        scc11['ACC_ObjDist'],
        -scc11['ACC_ObjLatPos'],  # in car frame's y axis, left is negative
        scc11['ACC_ObjRelSpd'],
      )

      if sample is None:
        self.pts.pop(0, None)
      else:
        if 0 not in self.pts:
          self.pts[0] = car.RadarData.RadarPoint.new_message()
        self.pts[0].trackId = sample.track_id
        self.pts[0].dRel = sample.d_rel  # from front of car
        self.pts[0].yRel = sample.y_rel
        self.pts[0].vRel = sample.v_rel
        self.pts[0].aRel = sample.a_rel
        self.pts[0].yvRel = float('nan')
        self.pts[0].measured = sample.measured

      ret.points = list(self.pts.values())
      return ret
