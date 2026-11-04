// Genesis DH MM-CAN gateway for a spare black panda (firmware built with -DMCAN_GATEWAY).
//
// The stock AVN keeps sending its own 0x115/0x123 ("no guidance") at 5 Hz even without a
// destination; on a bus shared with the HUD they make the HUD flicker against the
// openpilot (main panda) turn-by-turn frames (2026-11-04 14:13 log). This panda sits in
// series in the MM-CAN wires in front of the HUD, through a low-speed fault-tolerant <->
// high-speed CAN converter on each side. Both buses are on the harness box's 8-pin
// power/OBD connector (no car harness needed):
//   bus 0 = car side (AVN, cluster, ...), pins 6 CAN0-H / 5 CAN0-L
//   bus 1 = HUD side (HUD + the main panda's bus 1), pins 8 CAN3-H / 7 CAN3-L
//   power pin 1 (12 V), GND pin 4
// Everything is forwarded both ways, except:
//   - car -> HUD: the AVN's HUD guidance IDs while the main panda's own frame with that ID
//     was seen on the HUD side within MCAN_GW_FRESH_US (0x4A9 text follows 0x115), so the
//     HUD only gets ours; once ours stop, the AVN's frames pass again (stock navi guidance).
//   - HUD -> car: the main panda's guidance frames are not sent to the car side.
// Nothing bridges the two sides while this panda is unpowered: power it with the car.

#define MCAN_GW_FRESH_US 300000U
#define MCAN_GW_CAR_BUS 0
#define MCAN_GW_HUD_BUS 1
#define MCAN_GW_IDS 4

static const int mcan_gw_ids[MCAN_GW_IDS] = {0x115, 0x123, 0x1E5, 0x4A9};
static uint32_t mcan_gw_seen_ts[MCAN_GW_IDS] = {0U, 0U, 0U, 0U};
static bool mcan_gw_seen[MCAN_GW_IDS] = {false, false, false, false};

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

static int mcan_gateway_fwd_hook(int bus_num, CANPacket_t *to_fwd) {
  int bus_fwd = -1;
  int addr = GET_ADDR(to_fwd);
  int index = mcan_gw_index(addr);

  if (bus_num == MCAN_GW_HUD_BUS) {
    if (index >= 0) {
      // the main panda's guidance frame: remember it, keep it on the HUD side
      mcan_gw_seen_ts[index] = microsecond_timer_get();
      mcan_gw_seen[index] = true;
    } else {
      bus_fwd = MCAN_GW_CAR_BUS;
    }
  } else if (bus_num == MCAN_GW_CAR_BUS) {
    bool block = false;
    if (index >= 0) {
      // the text goes with the turn-by-turn frame
      block = (addr == 0x4A9) ? mcan_gw_fresh(0) : mcan_gw_fresh(index);
    }
    if (!block) {
      bus_fwd = MCAN_GW_HUD_BUS;
    }
  } else {
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
