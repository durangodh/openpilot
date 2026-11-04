#!/usr/bin/env python3
from cereal import car
from opendbc.can.parser import CANParser
from selfdrive.car.interfaces import RadarInterfaceBase
from common.filter_simple import StreamingMovingAverage
from selfdrive.car.hyundai.values import DBC

def get_radar_can_parser(CP):
  # SCC11 lead only. The mando radar-track branch (NewRadarInterface) was
  # hard-disabled and is removed; this car has no radar tracks.
  signals = [
    # sig_name, sig_address
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
    # radard uses scc_only to match the single SCC11 lead to the vision lead.
    self.scc_only = True
    self.updated_messages = set()
    self.trigger_msg = 0x420  # SCC11
    self.track_id = 0

    self.radar_off_can = CP.radarOffCan
    self.rcp = get_radar_can_parser(CP)

    self.dRelFilter = StreamingMovingAverage(2)
    self.vRelFilter = StreamingMovingAverage(4)
    self.valid_prev = False

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

    cpt = self.rcp.vl

    valid = cpt["SCC11"]['ACC_ObjStatus']

    for ii in range(1):
      if valid:
        if ii not in self.pts:
          self.pts[ii] = car.RadarData.RadarPoint.new_message()
          self.pts[ii].trackId = self.track_id
          self.track_id += 1

        if not self.valid_prev:
          dRel = self.dRelFilter.set(cpt["SCC11"]['ACC_ObjDist'])
          vRel = self.vRelFilter.set(cpt["SCC11"]['ACC_ObjRelSpd'])
        else:
          dRel = self.dRelFilter.process(cpt["SCC11"]['ACC_ObjDist'])
          vRel = self.vRelFilter.process(cpt["SCC11"]['ACC_ObjRelSpd'])
        self.pts[ii].dRel = dRel #cpt["SCC11"]['ACC_ObjDist']  # from front of car
        self.pts[ii].yRel = -cpt["SCC11"]['ACC_ObjLatPos']  # in car frame's y axis, left is negative
        self.pts[ii].vRel = vRel #cpt["SCC11"]['ACC_ObjRelSpd']
        self.pts[ii].aRel = float('nan')
        self.pts[ii].yvRel = float('nan')
        self.pts[ii].measured = True

      else:
        if ii in self.pts:
          del self.pts[ii]

    self.valid_prev = valid
    ret.points = list(self.pts.values())
    return ret
