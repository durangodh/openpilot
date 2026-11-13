// Genesis DH B-CAN turn-signal TAP test (ESP32-S3, Arduino-ESP32 3.x).
//
// Sends one short "one-touch" lever tap on the low-speed B-CAN, only when asked over serial,
// to see whether the body controller flashes the exterior lamps (stock one-touch: 5 flashes).
//
// Lever message found with dh_bcan_logger (2026-11-13, DH B-CAN 100 kbit/s):
//   0x138 byte 0: 0x30 neutral, 0x32 LEFT (lever down), 0x31 RIGHT (lever up)  -> bits 0..1
//   stock one-touch: 0x138 changes for 0.14-0.20 s, BCM then flashes 5 times (~3.5 s)
//   feedback: 0x168 byte 6 = lamp (0x80 left / 0x20 right, 0.35 s on/off), 0x102 byte 5 = BCM state
//
// REQUIRES a low-speed (fault-tolerant) <-> high-speed CAN converter between the board and the car
// (the same TJA1054 type as the M-CAN one). Never transmit with the board's high-speed transceiver
// wired straight onto the low-speed B-CAN.
//
// Safety:
//   - Listen-only all the time; the controller goes to normal (transmit) mode only for one tap.
//   - A tap copies the newest stock 0x138 (seen < 0.5 s ago) and changes only bits 0..1 of byte 0.
//   - Refused if the driver's lever is not neutral, or no fresh stock 0x138.
//   - Waits for the next stock 0x138 and sends right behind it (the switch sends every ~0.2 s),
//     resends every 40 ms for 0.16 s, then sends the neutral copy once and returns to listen-only.
//   - 4 s cooldown between taps. Any bus error / error-passive / bus-off aborts at once.
//
// Serial 115200 commands:  l = LEFT tap   r = RIGHT tap   s = status   h = help
#include <Arduino.h>
#include <stdarg.h>
#include <driver/twai.h>

#define CAN_TX_PIN 15        // Waveshare ESP32-S3-RS485-CAN own CAN terminals (behind the converter)
#define CAN_RX_PIN 16
#define LEVER_ID 0x138
#define LEVER_MASK 0x03
#define LEFT_BITS 0x02
#define RIGHT_BITS 0x01
#define TAP_MS 160
#define RESEND_MS 40
#define COOLDOWN_MS 4000
#define STOCK_FRESH_MS 500
#define WAIT_STOCK_MS 600
#define WATCH_MS 5000

// Arduino IDE puts prototypes for every function above the includes, so no function takes or
// returns a TWAI type and none is static; TWAI values live in these globals.
static twai_timing_config_t gTiming = TWAI_TIMING_CONFIG_100KBITS();
static twai_message_t gRx;
static twai_message_t gTx;

static bool installed = false;
static bool txMode = false;

static uint8_t stock[8];
static bool stockValid = false;
static uint32_t stockMs = 0, stockCount = 0;

enum TapState { TAP_IDLE, TAP_WAIT_STOCK, TAP_SENDING };
static TapState tap = TAP_IDLE;
static uint8_t tapBits = 0;
static uint32_t cmdMs = 0, tapStart = 0, lastSend = 0, lastTapEnd = 0;
static uint32_t tapFrames = 0;

static uint32_t txOk = 0, txFail = 0, busErr = 0;
static uint32_t watchUntil = 0, flashes = 0;
static uint8_t last102 = 0, last168 = 0, last138 = 0;
static bool seen102 = false, seen168 = false;

void out(const char *s) {
  Serial.print(s);
#if ARDUINO_USB_CDC_ON_BOOT
  Serial0.print(s);
#endif
}
void outf(const char *fmt, ...) {
  char buf[200];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  out(buf);
}
int readCmd() {
  if (Serial.available()) return Serial.read();
#if ARDUINO_USB_CDC_ON_BOOT
  if (Serial0.available()) return Serial0.read();
#endif
  return -1;
}

bool startCan(bool tx) {
  if (installed) {
    twai_stop();
    twai_driver_uninstall();
    installed = false;
  }
  twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT((gpio_num_t)CAN_TX_PIN, (gpio_num_t)CAN_RX_PIN,
                                                        tx ? TWAI_MODE_NORMAL : TWAI_MODE_LISTEN_ONLY);
  g.tx_queue_len = tx ? 4 : 0;
  g.rx_queue_len = 64;
  g.alerts_enabled = TWAI_ALERT_TX_SUCCESS | TWAI_ALERT_TX_FAILED | TWAI_ALERT_BUS_ERROR |
                     TWAI_ALERT_ERR_PASS | TWAI_ALERT_BUS_OFF;
  twai_filter_config_t f = TWAI_FILTER_CONFIG_ACCEPT_ALL();
  installed = twai_driver_install(&g, &gTiming, &f) == ESP_OK && twai_start() == ESP_OK;
  txMode = installed && tx;
  return installed;
}

void sendLever(uint8_t bits) {
  gTx = twai_message_t{};
  gTx.identifier = LEVER_ID;
  gTx.extd = 0;
  gTx.data_length_code = 8;
  memcpy(gTx.data, stock, 8);
  gTx.data[0] = (stock[0] & ~LEVER_MASK) | bits;
  if (twai_transmit(&gTx, 0) != ESP_OK) txFail++;
  tapFrames++;
}

void endTap(const char *why) {
  bool done = !strcmp(why, "done");
  if (done && tap == TAP_SENDING) sendLever(0);  // the switch's own release: neutral copy
  delay(5);  // let the last frame leave (an 8-byte frame takes ~1.1 ms at 100 kbit/s)
  startCan(false);
  tap = TAP_IDLE;
  lastTapEnd = millis();
  outf("=== tap end (%s): %lu frames sent, tx ok %lu, tx fail %lu, bus errors %lu ===\n", why,
       (unsigned long)tapFrames, (unsigned long)txOk, (unsigned long)txFail, (unsigned long)busErr);
  if (done) out("watching 0x168 lamp for 5 s...\n");
}

void requestTap(char side, uint32_t now) {
  const char *name = side == 'l' ? "LEFT" : "RIGHT";
  if (tap != TAP_IDLE) { out("busy\n"); return; }
  if (now - lastTapEnd < COOLDOWN_MS && lastTapEnd) { out("cooldown: wait 4 s between taps\n"); return; }
  if (!stockValid || now - stockMs > STOCK_FRESH_MS) {
    out("refused: no fresh stock 0x138 (ignition on? wiring? converter?)\n");
    return;
  }
  if (stock[0] & LEVER_MASK) { out("refused: the lever is not neutral\n"); return; }
  tapBits = side == 'l' ? LEFT_BITS : RIGHT_BITS;
  tapFrames = 0;
  txOk = txFail = busErr = 0;
  if (!startCan(true)) { out("refused: CAN driver did not start in transmit mode\n"); startCan(false); return; }
  tap = TAP_WAIT_STOCK;
  cmdMs = now;
  outf("\n=== %s tap: waiting for the next stock 0x138 ===\n", name);
}

void onFrame(uint32_t now) {  // frame in gRx
  const twai_message_t &m = gRx;
  if (m.extd || m.rtr || m.data_length_code < 8) return;
  if (m.identifier == LEVER_ID) {
    memcpy(stock, m.data, 8);
    stockValid = true;
    stockMs = now;
    stockCount++;
    if (m.data[0] != last138) {
      outf("%9lu  0x138 lever  %02X -> %02X\n", (unsigned long)now, last138, m.data[0]);
      last138 = m.data[0];
    }
    if (tap == TAP_WAIT_STOCK) {  // right behind the stock frame: ~0.2 s until the next one
      tap = TAP_SENDING;
      tapStart = lastSend = now;
      watchUntil = now + WATCH_MS;  // the first flash starts ~20 ms after the lever change
      flashes = 0;
      sendLever(tapBits);
      outf("%9lu  sent 0x138 %02X (copy of stock, lever bits %02X)\n", (unsigned long)now,
           (stock[0] & ~LEVER_MASK) | tapBits, tapBits);
    } else if (tap == TAP_SENDING && (m.data[0] & LEVER_MASK) == 0) {
      sendLever(tapBits);  // the switch just sent neutral during our tap: answer it at once
    }
  } else if (m.identifier == 0x102) {
    uint8_t v = m.data[5] & 0xC0;
    if (!seen102 || v != last102) outf("%9lu  0x102 BCM turn state  %02X\n", (unsigned long)now, v);
    last102 = v;
    seen102 = true;
  } else if (m.identifier == 0x168) {
    uint8_t v = m.data[6] & 0xA0;
    if (seen168 && v && !last168 && (int32_t)(watchUntil - now) > 0) flashes++;
    if (!seen168 || v != last168) outf("%9lu  0x168 lamp  %02X\n", (unsigned long)now, v);
    last168 = v;
    seen168 = true;
  }
}

void help() {
  out("\nGenesis DH B-CAN turn-signal TAP test (needs the low-speed <-> high-speed converter)\n"
      "  l = LEFT tap (0.16 s)   r = RIGHT tap   s = status   h = help\n"
      "  P, parked, hazard off, lever neutral. Look at the exterior lamps: success = 5 flashes.\n");
  outf("  pins TX %d RX %d, 100k, %s\n\n", CAN_TX_PIN, CAN_RX_PIN,
       installed ? (txMode ? "TRANSMIT" : "listen-only") : "NOT running");
}

void status(uint32_t now) {
  outf("\nstock 0x138: %s", stockValid ? "" : "none yet\n");
  if (stockValid) {
    outf("%02X %02X %02X %02X %02X %02X %02X %02X  (%lu frames, %lu ms ago)\n", stock[0], stock[1], stock[2],
         stock[3], stock[4], stock[5], stock[6], stock[7], (unsigned long)stockCount,
         (unsigned long)(now - stockMs));
  }
  twai_status_info_t st;
  if (installed && twai_get_status_info(&st) == ESP_OK) {
    outf("bus errors %lu, tx err counter %lu, rx err counter %lu, %s\n\n", (unsigned long)st.bus_error_count,
         (unsigned long)st.tx_error_counter, (unsigned long)st.rx_error_counter,
         txMode ? "TRANSMIT" : "listen-only");
  }
}

void setup() {
  Serial.begin(115200);
#if ARDUINO_USB_CDC_ON_BOOT
  Serial0.begin(115200);
#endif
  delay(1500);
  startCan(false);
  help();
}

void loop() {
  uint32_t now = millis();

  for (int i = 0; installed && i < 32 && twai_receive(&gRx, 0) == ESP_OK; i++) onFrame(now);

  uint32_t alerts;
  if (installed && twai_read_alerts(&alerts, 0) == ESP_OK) {
    if (alerts & TWAI_ALERT_TX_SUCCESS) txOk++;
    if (alerts & TWAI_ALERT_TX_FAILED) txFail++;
    if (alerts & TWAI_ALERT_BUS_ERROR) busErr++;
    if (txMode && (alerts & (TWAI_ALERT_BUS_ERROR | TWAI_ALERT_ERR_PASS | TWAI_ALERT_BUS_OFF | TWAI_ALERT_TX_FAILED))) {
      out("! bus problem while transmitting: abort\n");
      endTap("abort");
    }
  }

  if (tap == TAP_WAIT_STOCK && now - cmdMs > WAIT_STOCK_MS) endTap("no stock 0x138 to follow");
  if (tap == TAP_SENDING) {
    if (now - tapStart >= TAP_MS) {
      endTap("done");
    } else if (now - lastSend >= RESEND_MS) {
      lastSend = now;
      sendLever(tapBits);
    }
  }

  if (watchUntil && (int32_t)(now - watchUntil) >= 0) {
    outf("=== lamp flashes seen on 0x168: %lu (stock one-touch: 5) ===\n\n", (unsigned long)flashes);
    watchUntil = 0;
  }

  int c = readCmd();
  if (c < 0 || c == '\n' || c == '\r') return;
  if (c == 'l' || c == 'r') requestTap((char)c, now);
  else if (c == 's') status(now);
  else help();
}
