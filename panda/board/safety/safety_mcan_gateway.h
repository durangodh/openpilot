// Genesis DH MM-CAN gateway for a spare black panda (firmware built with -DMCAN_GATEWAY).
//
// The stock AVN keeps sending its own 0x115/0x123 ("no guidance") at 5 Hz even without a
// destination; on a bus shared with the HUD they make the HUD flicker against the
// openpilot (main panda) turn-by-turn frames (2026-11-04 14:13 log). This panda sits in
// series in the MM-CAN wires in front of the HUD, through a low-speed fault-tolerant <->
// high-speed CAN converter. Wiring on this panda's own harness box (no car harness on it):
//   car side (AVN, cluster, ...): 26-pin car-harness connector pin 4 CAN0_H / pin 6 CAN0_L
//     (comma open_pinout; CAN2 would be pins 22/24)
//   HUD side (HUD + the main panda's bus 1): 8-pin power connector pin 8 CAN3-H / 7 CAN3-L
//   power: 8-pin pin 1 (12 V), pin 4 GND
//   The 8-pin's pins 5/6 are NOT CAN0: 0 V with the panda powered, and no frame reached
//   the panda through them (2026-11-11 diagnostic build: rx 0, ACK errors on CAN0/CAN2).
//   Which panda bus each pair lands on depends on the OBD-C cable orientation; the
//   firmware finds the sides from the NM frames, see mcan_gw_car_bus / mcan_gw_hud_bus.
// Everything is forwarded both ways, except:
//   - car -> HUD: the AVN's HUD guidance IDs while the main panda's own frame with that ID
//     was seen on the HUD side within MCAN_GW_FRESH_US (0x4A9 text follows 0x115), so the
//     HUD only gets ours; once ours stop, the AVN's frames pass again (stock navi guidance).
//   - HUD -> car: the main panda's guidance frames are not sent to the car side.
// Nothing bridges the two sides while this panda is unpowered: power it with the car.

#define MCAN_GW_FRESH_US 300000U
#define MCAN_GW_IDS 4

static const int mcan_gw_ids[MCAN_GW_IDS] = {0x115, 0x123, 0x1E5, 0x4A9};
static uint32_t mcan_gw_seen_ts[MCAN_GW_IDS] = {0U, 0U, 0U, 0U};
static bool mcan_gw_seen[MCAN_GW_IDS] = {false, false, false, false};
// frames received per panda bus since the last diagnostic report (MCAN_GW_DIAG)
static uint32_t mcan_gw_rx_cnt[3] = {0U, 0U, 0U};

static int mcan_gw_index(int addr) {
  int index = -1;
  for (int i = 0; i < MCAN_GW_IDS; i++) {
    if (mcan_gw_ids[i] == addr) {
      index = i;
    }
  }
  return index;
}

static bool mcan_gw_fresh(int index) {
  bool fresh = false;
  if (mcan_gw_seen[index]) {
    fresh = get_ts_elapsed(microsecond_timer_get(), mcan_gw_seen_ts[index]) < MCAN_GW_FRESH_US;
  }
  return fresh;
}

static const addr_checks* mcan_gateway_init(int16_t param) {
  UNUSED(param);
  controls_allowed = false;
  for (int i = 0; i < MCAN_GW_IDS; i++) {
    mcan_gw_seen[i] = false;
  }
  return &default_rx_checks;
}

static int mcan_gateway_rx_hook(CANPacket_t *to_push) {
  UNUSED(to_push);
  return true;
}

// nothing from the host
static int mcan_gateway_tx_hook(CANPacket_t *to_send, bool longitudinal_allowed) {
  UNUSED(to_send);
  UNUSED(longitudinal_allowed);
  return false;
}

static int mcan_gateway_tx_lin_hook(int lin_num, uint8_t *data, int len) {
  UNUSED(lin_num);
  UNUSED(data);
  UNUSED(len);
  return false;
}

// Which panda bus is which side is learned, not fixed: on a black panda the OBD-C cable
// orientation swaps bus 0 and 2 and moves bus 1 (boards/black.h), so the CANBridge pair can
// arrive on any bus (2026-11-11: car frames never reached the HUD with the fixed 0/1).
//   car side: the bus where the car's NM ring frames (0x440-0x44F) arrive
//   HUD side: another bus where the HUD's NM frame 0x454 arrives without any car NM
// Frames we send are not received back, so forwarding does not confuse the detection.
#define MCAN_GW_SIDE_FRESH_US 1000000U
static uint32_t mcan_gw_car_nm_ts[3] = {0U, 0U, 0U};
static bool mcan_gw_car_nm[3] = {false, false, false};
static uint32_t mcan_gw_hud_nm_ts[3] = {0U, 0U, 0U};
static bool mcan_gw_hud_nm[3] = {false, false, false};

static bool mcan_gw_recent(bool seen, uint32_t ts) {
  return seen && (get_ts_elapsed(microsecond_timer_get(), ts) < MCAN_GW_SIDE_FRESH_US);
}

static int mcan_gw_car = 0;
static int mcan_gw_hud = 1;

static int mcan_gw_car_bus(void) {
  int car = -1;
  const int order[3] = {0, 2, 1};
  for (int i = 0; i < 3; i++) {
    int b = order[i];
    if ((car < 0) && mcan_gw_recent(mcan_gw_car_nm[b], mcan_gw_car_nm_ts[b])) {
      car = b;
    }
  }
  return car;
}

static int mcan_gw_hud_bus(int car) {
  int hud = -1;
  const int order[3] = {1, 0, 2};
  for (int i = 0; i < 3; i++) {
    int b = order[i];
    if ((hud < 0) && (b != car) && mcan_gw_recent(mcan_gw_hud_nm[b], mcan_gw_hud_nm_ts[b]) &&
        !mcan_gw_recent(mcan_gw_car_nm[b], mcan_gw_car_nm_ts[b])) {
      hud = b;
    }
  }
  return hud;
}

static int mcan_gateway_fwd_hook(int bus_num, CANPacket_t *to_fwd) {
  int bus_fwd = -1;
  int addr = GET_ADDR(to_fwd);
  int index = mcan_gw_index(addr);

  if ((bus_num >= 0) && (bus_num < 3)) {
    mcan_gw_rx_cnt[bus_num] += 1U;
    if ((addr >= 0x440) && (addr <= 0x44F)) {
      mcan_gw_car_nm_ts[bus_num] = microsecond_timer_get();
      mcan_gw_car_nm[bus_num] = true;
    }
    if (addr == 0x454) {
      mcan_gw_hud_nm_ts[bus_num] = microsecond_timer_get();
      mcan_gw_hud_nm[bus_num] = true;
    }
  }

  // Forward from the first frame with the last known sides (car 0 / HUD 1 until learned;
  // 2026-11-11 22:46 diagnostic log: car frames on bus 0, HUD on bus 1). Waiting for both
  // NM frames before forwarding dropped the frames the car sends once at wake-up, and the
  // stock HUD came up at the wrong height after a cold start. A learned pair is kept until
  // NM frames show a different one.
  int car_seen = mcan_gw_car_bus();
  int hud_seen = mcan_gw_hud_bus(car_seen);
  if ((car_seen >= 0) && (hud_seen >= 0)) {
    mcan_gw_car = car_seen;
    mcan_gw_hud = hud_seen;
  }
  int car = mcan_gw_car;
  int hud = mcan_gw_hud;

  {
    if (bus_num == hud) {
      if (index >= 0) {
        // the main panda's guidance frame: remember it, keep it on the HUD side
        mcan_gw_seen_ts[index] = microsecond_timer_get();
        mcan_gw_seen[index] = true;
      } else {
        bus_fwd = car;
      }
    } else if (bus_num == car) {
      bool block = false;
      if (index >= 0) {
        // the text goes with the turn-by-turn frame
        block = (addr == 0x4A9) ? mcan_gw_fresh(0) : mcan_gw_fresh(index);
      }
      if (!block) {
        bus_fwd = hud;
      }
    } else {
    }
  }
  return bus_fwd;
}

const safety_hooks mcan_gateway_hooks = {
  .init = mcan_gateway_init,
  .rx = mcan_gateway_rx_hook,
  .tx = mcan_gateway_tx_hook,
  .tx_lin = mcan_gateway_tx_lin_hook,
  .fwd = mcan_gateway_fwd_hook,
};
