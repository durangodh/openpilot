"""Genesis DH turn-signal request over CGW1 (0x541, bus 0).

The DH has no CAN message that commands the turn lamps (docs/bt_remote.md), so
a remote/openpilot lane change runs with the lamps off. This tries the device-free
route found on the classic-CAN Palisade (ajouatom carrot-wip, PalisadeBlinkerTest /
CanfdBlinkerKick): imitate the BCM's own CGW1 status with the lever side
(CF_Gway_TSigLHSw/RHSw) set and that side's lamp bit (CF_Gway_TurnSigLh/Rh)
flashing 0.35 s on / 0.35 s off like stock.

Whether anything on the DH actually lights a lamp from this frame is UNVERIFIED.
Run the parked test (CgwBlinkerTest) first and look at the exterior lamps.

The host never puts 0x541 on the bus itself. It sends a command frame that only
carries the side and lamp bits; panda (safety_hyundai_community.h) keeps the
newest one and, right behind every stock CGW1 it receives, sends a copy of that
stock frame with those bits added. The BCM's 10 Hz frame and ours therefore never
start at the same time (direct sending on the Palisade made 32 bus errors in 0.5 s).
Panda reports each held command frame as "rejected" (bus + 192); that is expected.
"""
from common.realtime import DT_CTRL

CGW1_ADDR = 0x541
CGW_BLINKER_PARAM = 0x0800  # panda safety_defaults.h, next to MCAN_TX_PARAM 0x0400

# CGW1 byte/bit masks (opendbc hyundai_kia_generic.dbc, BO_ 1345)
LAMP_LH = (2, 0x18)    # CF_Gway_TurnSigLh, bits 19..20
LAMP_RH = (7, 0xC0)    # CF_Gway_TurnSigRh, bits 62..63
LEVER_LH = (5, 0x04)   # CF_Gway_TSigLHSw, bit 42
LEVER_RH = (5, 0x02)   # CF_Gway_TSigRHSw, bit 41

LEFT, RIGHT = 1, 2

TX_STEP = 5                               # 20 Hz commands; panda only echoes behind the 10 Hz stock frame
FLASH_HALF_FRAMES = int(0.35 / DT_CTRL)   # stock flash: 0.35 s on / 0.35 s off
TEST_FRAMES = int(3.0 / DT_CTRL)          # parked test length
LANE_MAX_FRAMES = int(10.0 / DT_CTRL)     # one lane change; panda also cuts at 10 s


def command_frame(direction, lamp):
  """Host -> panda command: only the lever side and (when lamp) that side's lamp bit, every other bit 0."""
  data = bytearray(8)
  lever = LEVER_LH if direction == LEFT else LEVER_RH
  data[lever[0]] |= lever[1]
  if lamp:
    byte, mask = LAMP_LH if direction == LEFT else LAMP_RH
    data[byte] |= mask & (mask >> 1)  # lowest bit of the 2-bit field (value 1 = on, as stock)
  return [CGW1_ADDR, 0, bytes(data), 0]


class CgwBlinker:
  """Decides when to request the lamp: the parked test or an openpilot lane change without the lever."""

  def __init__(self):
    self.test_armed = False
    self.test_dir = 0
    self.test_start = 0
    self.lane_dir = 0
    self.lane_start = 0

  def _test(self, frame, selection, parked):
    if selection not in (LEFT, RIGHT):
      self.test_armed = True   # back to 0 re-arms the one-shot test
      self.test_dir = 0
      return 0
    if self.test_armed:
      self.test_armed = False
      self.test_dir = selection
      self.test_start = frame
    if self.test_dir != selection or not parked or frame - self.test_start >= TEST_FRAMES:
      self.test_dir = 0
    return self.test_dir

  def _lane(self, frame, lane_dir):
    if lane_dir not in (LEFT, RIGHT):
      self.lane_dir = 0
      return 0
    if lane_dir != self.lane_dir:
      self.lane_dir = lane_dir
      self.lane_start = frame
    return lane_dir if frame - self.lane_start < LANE_MAX_FRAMES else 0

  def update(self, frame, test_selection, parked, lane_dir, hazard, lever_left, lever_right):
    """test_selection: CgwBlinkerTest (0 off, 1 left, 2 right), only while parked and disengaged.
    lane_dir: 1/2 while openpilot runs a lane change, else 0.
    Returns the command frames to send this control frame."""
    direction = self._test(frame, test_selection, parked)
    start = self.test_start
    if not direction:
      direction = self._lane(frame, lane_dir)
      start = self.lane_start

    # Hazard or the driver's own lever: the BCM is already signalling, never add to it.
    if not direction or hazard or lever_left or lever_right or frame % TX_STEP:
      return []

    lamp = (frame - start) // FLASH_HALF_FRAMES % 2 == 0
    return [command_frame(direction, lamp)]
