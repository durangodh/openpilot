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
// Hotspots (no new upload when the phone changes): up to 4 networks are saved in the board's flash and
// tried in turn. When none connects for 30 s the board opens its own setup Wi-Fi "DH-Blinker-Setup"
// (password dhblinker); join it with the phone and the setup page (http://192.168.4.1) adds or deletes
// networks. The page also opens on demand with the serial command "wp".
//
// Serial 115200 (USB, one command per line, "New Line" ending):
//   l = LEFT tap   r = RIGHT tap   s = status   h = help
//   w NAME|PASSWORD = save a hotspot   wl = list   wd N = delete number N   wp = open the setup Wi-Fi
#include <Arduino.h>
#include <stdarg.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <Preferences.h>
#include <driver/twai.h>

// ===== first hotspot, used only while nothing is saved yet (optional) =====
#define WIFI_SSID "G80"
#define WIFI_PASS "PUT_HOTSPOT_PASSWORD_HERE"
// ==========================================================================
#define SETUP_AP_SSID "DH-Blinker-Setup"
#define SETUP_AP_PASS "dhblinker"
#define MAX_NETS 4
#define NET_TRY_MS 12000          // per saved network
#define NET_TRY_PORTAL_MS 30000   // slower while the setup Wi-Fi is open (the radio shares one channel)
#define PORTAL_AFTER_MS 30000
#define PORTAL_IDLE_MS 120000     // closes this long after the last page view once connected

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
static WebServer gWeb(80);
static DNSServer gDns;
static Preferences gPrefs;

static char netSsid[MAX_NETS][33];
static char netPass[MAX_NETS][65];
static int netCount = 0, netIdx = -1;
static uint32_t netTryMs = 0, wifiDownMs = 0, portalUseMs = 0;
static bool portalOn = false, webStarted = false, netTryNow = true;  // try the first saved hotspot at once
static char lineBuf[120];
static int lineLen = 0;

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

// ---------- saved hotspots ----------
void saveNets() {
  gPrefs.begin("dhbl", false);
  gPrefs.putInt("n", netCount);
  char key[4];
  for (int i = 0; i < MAX_NETS; i++) {
    snprintf(key, sizeof(key), "s%d", i);
    if (i < netCount) gPrefs.putString(key, netSsid[i]); else gPrefs.remove(key);
    snprintf(key, sizeof(key), "p%d", i);
    if (i < netCount) gPrefs.putString(key, netPass[i]); else gPrefs.remove(key);
  }
  gPrefs.end();
}

void loadNets() {
  gPrefs.begin("dhbl", true);
  netCount = gPrefs.getInt("n", 0);
  if (netCount < 0 || netCount > MAX_NETS) netCount = 0;
  char key[4];
  for (int i = 0; i < netCount; i++) {
    snprintf(key, sizeof(key), "s%d", i);
    netSsid[i][0] = 0;
    gPrefs.getString(key, netSsid[i], sizeof(netSsid[i]));
    snprintf(key, sizeof(key), "p%d", i);
    netPass[i][0] = 0;
    gPrefs.getString(key, netPass[i], sizeof(netPass[i]));
  }
  gPrefs.end();
  if (netCount == 0 && strlen(WIFI_SSID) && strcmp(WIFI_PASS, "PUT_HOTSPOT_PASSWORD_HERE")) {
    strncpy(netSsid[0], WIFI_SSID, 32);
    netSsid[0][32] = 0;
    strncpy(netPass[0], WIFI_PASS, 64);
    netPass[0][64] = 0;
    netCount = 1;
  }
}

// Saves (or updates) a hotspot as the first one to try. Returns "" or the reason it was refused.
const char *addNet(const char *ssid, const char *pass) {
  size_t ls = strlen(ssid), lp = strlen(pass);
  if (ls < 1 || ls > 32) return "name must be 1-32 characters";
  if (lp && (lp < 8 || lp > 63)) return "password must be 8-63 characters (or empty)";
  int found = -1;
  for (int i = 0; i < netCount; i++) if (!strcmp(netSsid[i], ssid)) found = i;
  int last = found >= 0 ? found : (netCount < MAX_NETS ? netCount++ : MAX_NETS - 1);
  for (int i = last; i > 0; i--) {
    memcpy(netSsid[i], netSsid[i - 1], sizeof(netSsid[i]));
    memcpy(netPass[i], netPass[i - 1], sizeof(netPass[i]));
  }
  strncpy(netSsid[0], ssid, 32);
  netSsid[0][32] = 0;
  strncpy(netPass[0], pass, 64);
  netPass[0][64] = 0;
  saveNets();
  netIdx = -1;
  netTryNow = true;  // switch to it right away, even if another hotspot is connected
  return "";
}

bool deleteNet(int i) {
  if (i < 0 || i >= netCount) return false;
  for (int k = i; k < netCount - 1; k++) {
    memcpy(netSsid[k], netSsid[k + 1], sizeof(netSsid[k]));
    memcpy(netPass[k], netPass[k + 1], sizeof(netPass[k]));
  }
  netCount--;
  saveNets();
  netIdx = -1;
  return true;
}

void listNets() {
  outf("saved hotspots (%d/%d):\n", netCount, MAX_NETS);
  for (int i = 0; i < netCount; i++) outf("  %d: %s%s\n", i + 1, netSsid[i], i == netIdx ? "  <- current" : "");
  if (!netCount) out("  none: w NAME|PASSWORD, or join the setup Wi-Fi " SETUP_AP_SSID "\n");
}

void startNextNet(uint32_t now) {
  netTryMs = now;
  if (!netCount) return;
  netIdx = (netIdx + 1) % netCount;
  WiFi.disconnect(false, false);
  WiFi.begin(netSsid[netIdx], netPass[netIdx]);
  outf("%9lu  Wi-Fi: trying \"%s\"\n", (unsigned long)now, netSsid[netIdx]);
}

// ---------- setup Wi-Fi and page ----------
void htmlEsc(String &o, const char *s) {
  for (; *s; s++) {
    if (*s == '<') o += "&lt;";
    else if (*s == '>') o += "&gt;";
    else if (*s == '&') o += "&amp;";
    else if (*s == '"') o += "&quot;";
    else o += *s;
  }
}

void sendPage(const char *msg) {
  portalUseMs = millis();
  String o;
  o.reserve(3000);
  o += "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
       "<title>DH 깜빡이 설정</title><style>body{font-family:sans-serif;margin:16px;max-width:480px}"
       "input{width:100%;padding:10px;margin:4px 0 12px;font-size:16px;box-sizing:border-box}"
       "button{padding:10px 16px;font-size:16px}li{margin:8px 0}.m{padding:10px;background:#e8f5e9;border-radius:6px}"
       "</style></head><body><h2>DH 깜빡이 Wi-Fi 설정</h2>";
  if (msg && *msg) {
    o += "<p class='m'>";
    htmlEsc(o, msg);
    o += "</p>";
  }
  o += "<p>현재: ";
  if (WiFi.status() == WL_CONNECTED) {
    o += "<b>";
    htmlEsc(o, WiFi.SSID().c_str());
    o += "</b> 연결됨 (";
    o += WiFi.RSSI();
    o += " dBm)";
  } else {
    o += "핫스팟 연결 안 됨";
  }
  o += "<br>B-CAN: ";
  o += stockFresh(millis()) ? "수신 중" : "없음";
  o += "</p><h3>저장된 핫스팟</h3><ol>";
  for (int i = 0; i < netCount; i++) {
    o += "<li>";
    htmlEsc(o, netSsid[i]);
    o += " &nbsp;<a href='/del?i=";
    o += i;
    o += "'>삭제</a></li>";
  }
  if (!netCount) o += "<li>없음</li>";
  o += "</ol><h3>핫스팟 추가</h3><form method='post' action='/add'>"
       "이름(SSID)<input name='ssid' maxlength='32' autocapitalize='none' autocorrect='off'>"
       "비밀번호<input name='pass' type='password' maxlength='63'>"
       "<button type='submit'>저장하고 연결</button></form>"
       "<p>휴대폰 핫스팟은 <b>2.4GHz</b>로 설정하세요. 최대 4개, 새로 저장한 것을 먼저 연결합니다.</p>"
       "</body></html>";
  gWeb.send(200, "text/html; charset=utf-8", o);
}

void webRoot() { sendPage(""); }

void webAdd() {
  const char *why = addNet(gWeb.arg("ssid").c_str(), gWeb.arg("pass").c_str());
  outf("setup page: save \"%s\": %s\n", gWeb.arg("ssid").c_str(), *why ? why : "ok");
  sendPage(*why ? why : "저장했습니다. 연결을 시도합니다. 잠시 뒤 휴대폰 핫스팟을 켜 두세요.");
}

void webDel() {
  int i = gWeb.arg("i").toInt();
  sendPage(deleteNet(i) ? "삭제했습니다." : "없는 번호입니다.");
}

void webOther() {  // captive portal: every other address shows the setup page
  gWeb.sendHeader("Location", "http://192.168.4.1/", true);
  gWeb.send(302, "text/plain", "");
}

void startPortal(uint32_t now) {
  if (portalOn) return;
  WiFi.mode(WIFI_AP_STA);
  WiFi.softAP(SETUP_AP_SSID, SETUP_AP_PASS);
  gDns.start(53, "*", WiFi.softAPIP());
  if (!webStarted) {
    gWeb.on("/", webRoot);
    gWeb.on("/add", HTTP_POST, webAdd);
    gWeb.on("/del", webDel);
    gWeb.onNotFound(webOther);
    gWeb.begin();
    webStarted = true;
  }
  portalOn = true;
  portalUseMs = now;
  outf("%9lu  setup Wi-Fi open: \"%s\" password %s, page http://192.168.4.1\n", (unsigned long)now, SETUP_AP_SSID,
       SETUP_AP_PASS);
}

void stopPortal() {
  if (!portalOn) return;
  gDns.stop();
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_STA);
  portalOn = false;
  out("setup Wi-Fi closed\n");
}

void wifiTick(uint32_t now) {
  bool up = WiFi.status() == WL_CONNECTED;
  if (up != wifiUp) {
    wifiUp = up;
    if (up) {
      outf("%9lu  Wi-Fi connected: \"%s\" %s, rssi %d dBm\n", (unsigned long)now, WiFi.SSID().c_str(),
           WiFi.localIP().toString().c_str(), (int)WiFi.RSSI());
    } else {
      out("Wi-Fi disconnected\n");
      wifiDownMs = now;
    }
  }
  if (netTryNow || (!up && now - netTryMs >= (portalOn ? NET_TRY_PORTAL_MS : NET_TRY_MS))) {
    netTryNow = false;
    startNextNet(now);
  }
  if (!up && !portalOn && now - wifiDownMs >= (netCount ? PORTAL_AFTER_MS : 3000)) startPortal(now);
  if (portalOn) {
    gDns.processNextRequest();
    gWeb.handleClient();
    if (up && now - portalUseMs > PORTAL_IDLE_MS) stopPortal();
  }
}

// ---------- serial ----------
void serialLine(char *line, uint32_t now) {
  while (*line == ' ') line++;
  if (!*line) return;
  if (line[0] == 'w') {
    if (line[1] == ' ') {
      char *sep = strchr(line + 2, '|');
      if (!sep) {
        out("use: w NAME|PASSWORD\n");
        return;
      }
      *sep = 0;
      const char *why = addNet(line + 2, sep + 1);
      outf("save \"%s\": %s\n", line + 2, *why ? why : "ok, connecting");
    } else if (line[1] == 'l') {
      listNets();
    } else if (line[1] == 'd') {
      outf(deleteNet(atoi(line + 2) - 1) ? "deleted\n" : "no such number (wl lists them)\n");
      listNets();
    } else if (line[1] == 'p') {
      startPortal(now);
    } else {
      help();
    }
    return;
  }
  if (line[1]) {
    help();
    return;
  }
  if (line[0] == 'l' || line[0] == 'r') handleRequest(line[0] == 'l' ? 'L' : 'R', now);
  else if (line[0] == 's') status(now);
  else help();
}

void help() {
  out("\nGenesis DH automatic turn signal (B-CAN one-touch on EON request)\n"
      "  l = LEFT tap   r = RIGHT tap   s = status   h = help\n"
      "  w NAME|PASSWORD = save hotspot   wl = list   wd N = delete   wp = open setup Wi-Fi\n");
  outf("  UDP in %d / status %d, CAN pins TX %d RX %d 100k\n", COMMAND_PORT, STATUS_PORT, CAN_TX_PIN, CAN_RX_PIN);
  listNets();
  out("\n");
}

void status(uint32_t now) {
  outf("\nWi-Fi: %s", wifiUp ? "connected " : "NOT connected\n");
  if (wifiUp) outf("\"%s\" %s  rssi %d dBm\n", WiFi.SSID().c_str(), WiFi.localIP().toString().c_str(), (int)WiFi.RSSI());
  if (portalOn) outf("setup Wi-Fi open: \"%s\" password %s\n", SETUP_AP_SSID, SETUP_AP_PASS);
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
  loadNets();
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // no power-save delay on incoming requests
  WiFi.setAutoReconnect(false);  // wifiTick() rotates through the saved hotspots
  gUdp.begin(COMMAND_PORT);
  wifiDownMs = millis();
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

  if (tap == TAP_IDLE) wifiTick(now);  // nothing slow during the 0.16 s tap
  if (wifiUp && tap == TAP_IDLE) pollUdp(now);
  if (now - lastStatus >= STATUS_MS) sendStatus(now);

  for (int c = readCmd(); c >= 0; c = readCmd()) {
    if (c == '\n' || c == '\r') {
      lineBuf[lineLen] = 0;
      lineLen = 0;
      serialLine(lineBuf, now);
    } else if (lineLen < (int)sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = (char)c;
    }
  }
}
