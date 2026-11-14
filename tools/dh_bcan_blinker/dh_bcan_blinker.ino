// Genesis DH automatic turn signal: B-CAN one-touch tap on request from EON (ESP32-S3, Arduino-ESP32 3.x).
//
// openpilot lane changes from the Bluetooth remote and NOO only raise a virtual blinker. EON asks this
// board over Wi-Fi (phone hotspot) for one stock one-touch lever tap; the body controller then flashes the
// exterior lamps 5 times, exactly like the driver's one-touch. EON side: selfdrive/eon_cluster/bcan_blinker.py.
//
// Verified 2026-11-14 with dh_bcan_tx_test: one tap = 5 exterior flashes, no warnings.
//   0x138 byte 0: 0x30 neutral, 0x32 LEFT, 0x31 RIGHT (bits 0..1), sent by the switch every ~0.2 s
//   0x168 byte 6 = lamp (0x80 left / 0x20 right), 0x102 byte 5 = BCM turn state (0xC0)
//
// Wiring: board CAN H/L straight to M01 pin 1 (green, H) / pin 2 (orange, L). Board CAN jumper NC.
// Power: board DC terminal from ignition-switched 12 V (the board turns off with the car).
//
// Safety:
//   - Listen-only all the time; normal (transmit) mode only for one 0.16 s tap.
//   - A tap copies the newest stock 0x138 (< 0.5 s old) and changes only bits 0..1 of byte 0.
//   - Refused when the driver's lever is not neutral, the lamps flashed in the last 0.8 s
//     (driver blinker, hazard, previous tap), no fresh stock 0x138, or within 3.5 s of the last tap.
//   - Any bus error / error-passive / bus-off / failed frame aborts at once.
//
// Network (phone hotspot, 2.4 GHz):
//   EON -> board  UDP 7290 "DHBL1 <seq> L|R" (sent 3 times, same seq)
//   board -> EON  UDP 7291 broadcast every second and after each request:
//                 {"t":"dhbl","v":1,"rssi":-55,"bcan":1,"lamp":0,"err":0,"seq":3,"res":"ok"}
//
// Serial 115200 (USB, for checking): l = LEFT tap   r = RIGHT tap   s = status   h = help
#include <Arduino.h>
#include <stdarg.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <driver/twai.h>

// ===== change these =====
#define WIFI_SSID "G80"
#define WIFI_PASS "PUT_HOTSPOT_PASSWORD_HERE"
// ========================

#define CAN_TX_PIN 15
#define CAN_RX_PIN 16
#define COMMAND_PORT 7290
#define STATUS_PORT 7291
#define LEVER_ID 0x138
#define LEVER_MASK 0x03
#define LEFT_BITS 0x02
#define RIGHT_BITS 0x01
#define TAP_MS 160
#define RESEND_MS 40
#define COOLDOWN_MS 3500  // from the end of a tap; EON repeats a long lane change every 4.5 s
#define STOCK_FRESH_MS 500
#define WAIT_STOCK_MS 600
#define LAMP_QUIET_MS 800
#define STATUS_MS 1000
#define ERR_HOLD_MS 60000

// Arduino IDE puts prototypes for every function above the includes, so no function takes or
// returns a TWAI or Wi-Fi type and none is static; those values live in these globals.
static twai_timing_config_t gTiming = TWAI_TIMING_CONFIG_100KBITS();
static twai_message_t gRx;
static twai_message_t gTx;
static WiFiUDP gUdp;

static bool installed = false;
static bool txMode = false;

static uint8_t stock[8];
static bool stockValid = false;
static uint32_t stockMs = 0;

enum TapState { TAP_IDLE, TAP_WAIT_STOCK, TAP_SENDING };
static TapState tap = TAP_IDLE;
static uint8_t tapBits = 0;
static uint32_t cmdMs = 0, tapStart = 0, lastSend = 0, lastTapEnd = 0;
static uint32_t tapFrames = 0, txFail = 0;

static uint32_t lampOnMs = 0;
static bool lampSeen = false;
static uint8_t last138 = 0xFF, last168 = 0xFF, last102 = 0xFF;
static uint32_t errMs = 0;
static bool errSeen = false;

static int lastSeq = -1;
static char lastRes[12] = "";
static uint32_t lastStatus = 0;
static bool wifiUp = false;

void out(const char *s) {
  Serial.print(s);
#if ARDUINO_USB_CDC_ON_BOOT
  Serial0.print(s);
#endif
}
void outf(const char *fmt, ...) {
  char buf[220];
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
  g.alerts_enabled = TWAI_ALERT_TX_FAILED | TWAI_ALERT_BUS_ERROR | TWAI_ALERT_ERR_PASS | TWAI_ALERT_BUS_OFF;
  twai_filter_config_t f = TWAI_FILTER_CONFIG_ACCEPT_ALL();
  installed = twai_driver_install(&g, &gTiming, &f) == ESP_OK && twai_start() == ESP_OK;
  txMode = installed && tx;
  return installed;
}

bool stockFresh(uint32_t now) { return stockValid && now - stockMs <= STOCK_FRESH_MS; }
bool lampRecent(uint32_t now) { return lampSeen && now - lampOnMs < LAMP_QUIET_MS; }
bool errRecent(uint32_t now) { return errSeen && now - errMs < ERR_HOLD_MS; }

void sendStatus(uint32_t now) {
  lastStatus = now;
  if (!wifiUp) return;
  char buf[160];
  snprintf(buf, sizeof(buf), "{\"t\":\"dhbl\",\"v\":1,\"rssi\":%d,\"bcan\":%d,\"lamp\":%d,\"err\":%d,\"seq\":%d,\"res\":\"%s\"}",
           (int)WiFi.RSSI(), stockFresh(now) ? 1 : 0, lampRecent(now) ? 1 : 0, errRecent(now) ? 1 : 0,
           lastSeq < 0 ? 0 : lastSeq, lastRes);
  gUdp.beginPacket(WiFi.broadcastIP(), STATUS_PORT);
  gUdp.write((const uint8_t *)buf, strlen(buf));
  gUdp.endPacket();
}

void finish(const char *res, uint32_t now) {
  strncpy(lastRes, res, sizeof(lastRes) - 1);
  lastRes[sizeof(lastRes) - 1] = 0;
  sendStatus(now);
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

void endTap(const char *res) {
  bool done = !strcmp(res, "ok");
  if (done && tap == TAP_SENDING) sendLever(0);  // the switch's own release: neutral copy
  delay(5);  // let the last frame leave (an 8-byte frame takes ~1.1 ms at 100 kbit/s)
  startCan(false);
  tap = TAP_IDLE;
  uint32_t now = millis();
  lastTapEnd = now;
  if (!done && strcmp(res, "nobcan")) {
    errSeen = true;
    errMs = now;
  }
  outf("=== tap end (%s): %lu frames, tx fail %lu ===\n", res, (unsigned long)tapFrames, (unsigned long)txFail);
  finish(res, now);
}

// Returns "" when the tap started, else the refusal reason.
const char *requestTap(char side, uint32_t now) {
  if (tap != TAP_IDLE) return "busy";
  if (lastTapEnd && now - lastTapEnd < COOLDOWN_MS) return "cooldown";
  if (!stockFresh(now)) return "nobcan";
  if (stock[0] & LEVER_MASK) return "lever";
  if (lampRecent(now)) return "lamp";
  tapBits = side == 'L' ? LEFT_BITS : RIGHT_BITS;
  tapFrames = 0;
  txFail = 0;
  if (!startCan(true)) {
    startCan(false);
    return "notx";
  }
  tap = TAP_WAIT_STOCK;
  cmdMs = now;
  outf("\n=== %s tap: waiting for the next stock 0x138 ===\n", side == 'L' ? "LEFT" : "RIGHT");
  return "";
}

void handleRequest(char side, uint32_t now) {
  const char *why = requestTap(side, now);
  if (*why) {
    outf("%9lu  %s tap refused: %s\n", (unsigned long)now, side == 'L' ? "LEFT" : "RIGHT", why);
    finish(why, now);
  } else {
    lastRes[0] = 0;  // the result follows when the tap ends
  }
}

void onFrame(uint32_t now) {  // frame in gRx
  const twai_message_t &m = gRx;
  if (m.extd || m.rtr || m.data_length_code < 8) return;
  if (m.identifier == LEVER_ID) {
    memcpy(stock, m.data, 8);
    stockValid = true;
    stockMs = now;
    if (m.data[0] != last138) {
      outf("%9lu  0x138 lever  %02X\n", (unsigned long)now, m.data[0]);
      last138 = m.data[0];
    }
    if (tap == TAP_WAIT_STOCK) {  // right behind the stock frame: ~0.2 s until the next one
      tap = TAP_SENDING;
      tapStart = lastSend = now;
      sendLever(tapBits);
    } else if (tap == TAP_SENDING && (m.data[0] & LEVER_MASK) == 0) {
      sendLever(tapBits);  // the switch just sent neutral during our tap: answer it at once
    }
  } else if (m.identifier == 0x102) {
    uint8_t v = m.data[5] & 0xC0;
    if (v) {
      lampOnMs = now;
      lampSeen = true;
    }
    if (v != last102) outf("%9lu  0x102 BCM turn state  %02X\n", (unsigned long)now, v);
    last102 = v;
  } else if (m.identifier == 0x168) {
    uint8_t v = m.data[6] & 0xA0;
    if (v) {
      lampOnMs = now;
      lampSeen = true;
    }
    if (v != last168) outf("%9lu  0x168 lamp  %02X\n", (unsigned long)now, v);
    last168 = v;
  }
}

void pollUdp(uint32_t now) {
  int n = gUdp.parsePacket();
  if (n <= 0) return;
  char buf[48];
  int len = gUdp.read((uint8_t *)buf, sizeof(buf) - 1);
  if (len <= 0) return;
  buf[len] = 0;
  int seq = -1;
  char side = 0;
  if (sscanf(buf, "DHBL1 %d %c", &seq, &side) != 2 || seq < 0 || (side != 'L' && side != 'R')) return;
  if (seq == lastSeq) return;  // repeats of the same request
  lastSeq = seq;
  outf("%9lu  EON request %d %c from %s\n", (unsigned long)now, seq, side, gUdp.remoteIP().toString().c_str());
  handleRequest(side, now);
}

void help() {
  out("\nGenesis DH automatic turn signal (B-CAN one-touch on EON request)\n"
      "  l = LEFT tap   r = RIGHT tap   s = status   h = help\n");
  outf("  Wi-Fi \"%s\", UDP in %d / status %d, CAN pins TX %d RX %d 100k\n\n", WIFI_SSID, COMMAND_PORT,
       STATUS_PORT, CAN_TX_PIN, CAN_RX_PIN);
}

void status(uint32_t now) {
  outf("\nWi-Fi: %s", wifiUp ? "connected " : "NOT connected\n");
  if (wifiUp) outf("%s  rssi %d dBm\n", WiFi.localIP().toString().c_str(), (int)WiFi.RSSI());
  outf("stock 0x138: %s", stockValid ? "" : "none yet\n");
  if (stockValid) {
    outf("%02X %02X %02X %02X %02X %02X %02X %02X  (%lu ms ago)\n", stock[0], stock[1], stock[2], stock[3],
         stock[4], stock[5], stock[6], stock[7], (unsigned long)(now - stockMs));
  }
  outf("lamp recent %d, error recent %d, last request %d result \"%s\", %s\n\n", lampRecent(now) ? 1 : 0,
       errRecent(now) ? 1 : 0, lastSeq, lastRes, txMode ? "TRANSMIT" : "listen-only");
}

void setup() {
  Serial.begin(115200);
#if ARDUINO_USB_CDC_ON_BOOT
  Serial0.begin(115200);
#endif
  delay(1000);
  startCan(false);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // no power-save delay on incoming requests
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  gUdp.begin(COMMAND_PORT);
  help();
}

void loop() {
  uint32_t now = millis();

  for (int i = 0; installed && i < 32 && twai_receive(&gRx, 0) == ESP_OK; i++) onFrame(now);

  uint32_t alerts;
  if (installed && twai_read_alerts(&alerts, 0) == ESP_OK && txMode &&
      (alerts & (TWAI_ALERT_BUS_ERROR | TWAI_ALERT_ERR_PASS | TWAI_ALERT_BUS_OFF | TWAI_ALERT_TX_FAILED))) {
    out("! bus problem while transmitting: abort\n");
    endTap("abort");
  }

  if (tap == TAP_WAIT_STOCK && now - cmdMs > WAIT_STOCK_MS) endTap("nobcan");
  if (tap == TAP_SENDING) {
    if (now - tapStart >= TAP_MS) {
      endTap("ok");
    } else if (now - lastSend >= RESEND_MS) {
      lastSend = now;
      sendLever(tapBits);
    }
  }

  bool up = WiFi.status() == WL_CONNECTED;
  if (up != wifiUp) {
    wifiUp = up;
    if (up) outf("Wi-Fi connected: %s, rssi %d dBm\n", WiFi.localIP().toString().c_str(), (int)WiFi.RSSI());
    else out("Wi-Fi disconnected\n");
  }
  if (wifiUp && tap == TAP_IDLE) pollUdp(now);
  if (now - lastStatus >= STATUS_MS) sendStatus(now);

  int c = readCmd();
  if (c < 0 || c == '\n' || c == '\r') return;
  if (c == 'l' || c == 'r') handleRequest(c == 'l' ? 'L' : 'R', now);
  else if (c == 's') status(now);
  else help();
}
