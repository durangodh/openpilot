// Genesis DH MM-CAN gateway on an ESP32-C6 (two TWAI controllers), in place of the spare black
// panda (panda/board/safety/safety_mcan_gateway.h, the same rules).
//
// The stock AVN keeps sending its own HUD guidance frames ("no guidance") at 5 Hz; on a bus shared
// with the HUD they make the HUD flicker against openpilot's turn-by-turn frames from the main
// panda. This board sits in series in the MM-CAN wires in front of the HUD and forwards everything
// both ways, except:
//   car -> HUD: the AVN's 0x115 / 0x123 / 0x1E5 / 0x4A9 while the main panda's own frame with that
//               ID was seen on the HUD side within 300 ms (0x4A9, the text, follows 0x115);
//   HUD -> car: the main panda's guidance frames are never sent to the car side.
// When ours stop, the AVN's frames pass again (stock navigation guidance).
//
// Wiring (the panda's place in the existing harness; CANBridge-LS, the toggle switch and the 12 V
// feed stay as they are):
//   CAN module A (3.3 V high-speed transceiver, SN65HVD230): TX GPIO2, RX GPIO3
//       CANH / CANL = the pair that went to the panda's 26-pin 4 / 6 (CANBridge-LS high-speed side)
//   CAN module B: TX GPIO6, RX GPIO7
//       CANH / CANL = the pair that went to the panda's 8-pin 8 / 7 (HUD side, from the switch)
//   5 V and GND from a 12 V -> 5 V converter (or a car USB charger) on the ignition-switched feed.
// Which module is the car side and which the HUD side is learned from the network management
// frames (car 0x440..0x44F, HUD 0x454), so A and B may be swapped; only H / L must be right.
// Nothing bridges the two sides while this board is unpowered or restarting: the toggle switch's
// "car" position is the bypass.
//
// Both buses run at 100 kbit/s, normal mode (the board acknowledges frames on both sides).
//
// Phone: the board joins a saved hotspot (serial "w NAME|PASSWORD", or its own setup Wi-Fi
// "DH-MCAN-GW" / dhmcangw at http://192.168.4.1 when none connects for 30 s):
//   http://<board IP>/        status      /log  the log      /update  firmware (user dh / dhmcangw)
// Arduino-ESP32 3.x, board "ESP32C6 Dev Module".
#include <Arduino.h>
#include <stdarg.h>
#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include <Update.h>
#include <driver/twai.h>

#define FW_VERSION "2026-11-26a"
#define SETUP_AP_SSID "DH-MCAN-GW"
#define SETUP_AP_PASS "dhmcangw"
#define OTA_USER "dh"

#define CAN_A_TX 2
#define CAN_A_RX 3
#define CAN_B_TX 6
#define CAN_B_RX 7

#define GW_FRESH_US 300000      // our guidance frame seen this recently: the AVN's is blocked
#define SIDE_FRESH_US 1000000   // NM frames this recent tell the sides
#define GW_IDS 4
static const uint32_t GW_ID[GW_IDS] = {0x115, 0x123, 0x1E5, 0x4A9};

static twai_handle_t gBus[2] = {NULL, NULL};
static volatile bool gBusUp[2] = {false, false};

// All of this is written by the two forwarding tasks and read by the web page: plain 32-bit
// values, no lock (a torn 64-bit time would matter, so times are kept as 32-bit microseconds).
static volatile uint32_t seenUs[GW_IDS];
static volatile bool seen[GW_IDS];
static volatile uint32_t carNmUs[2], hudNmUs[2];
static volatile bool carNm[2], hudNm[2];
static volatile int gCar = 0, gHud = 1;       // module A = car, B = HUD until learned
static volatile bool sidesLearned = false;
static volatile uint32_t rxCnt[2], fwdCnt[2], blockCnt, keptCnt, dropCnt[2], busErr[2], busOffCnt[2];

static WebServer gWeb(80);
static Preferences gPrefs;
static bool webStarted = false, apOpen = false;
static uint32_t wifiTryMs = 0;
static String gSsid, gPass;

// ---------- log ----------
#define LOG_RING 6000
static char logRing[LOG_RING];
static uint32_t logHead = 0;
static portMUX_TYPE logMux = portMUX_INITIALIZER_UNLOCKED;

void out(const char *s) {
  int n = (int)strlen(s);
  portENTER_CRITICAL(&logMux);
  for (int i = 0; i < n; i++) logRing[(logHead++) % LOG_RING] = s[i];
  portEXIT_CRITICAL(&logMux);
  if (Serial && Serial.availableForWrite() >= n) Serial.print(s);
}

void outf(const char *fmt, ...) {
  char buf[200];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  out(buf);
}

// ---------- gateway ----------
static inline bool recentUs(bool was, uint32_t ts, uint32_t now, uint32_t window) {
  return was && (uint32_t)(now - ts) < window;
}

static int gwIndex(uint32_t id) {
  for (int i = 0; i < GW_IDS; i++) if (GW_ID[i] == id) return i;
  return -1;
}

// Car side: the bus where the car's NM ring frames arrive. HUD side: the other bus, when the HUD's
// NM frame arrives there without any car NM. A learned pair is kept until the frames show another.
static void learnSides(uint32_t now) {
  int car = -1;
  for (int b = 0; b < 2 && car < 0; b++) if (recentUs(carNm[b], carNmUs[b], now, SIDE_FRESH_US)) car = b;
  if (car < 0) return;
  int hud = 1 - car;
  if (!recentUs(hudNm[hud], hudNmUs[hud], now, SIDE_FRESH_US) ||
      recentUs(carNm[hud], carNmUs[hud], now, SIDE_FRESH_US)) {
    return;
  }
  if (!sidesLearned || car != gCar) {
    gCar = car;
    gHud = hud;
    sidesLearned = true;
    outf("%9lu  sides: car = module %c, HUD = module %c\n", (unsigned long)millis(), 'A' + car, 'A' + hud);
  }
}

// One received frame: where it goes (-1: nowhere).
static int route(int bus, const twai_message_t &m, uint32_t now) {
  uint32_t id = m.identifier;
  if (!m.extd) {
    if (id >= 0x440 && id <= 0x44F) {
      carNmUs[bus] = now;
      carNm[bus] = true;
    } else if (id == 0x454) {
      hudNmUs[bus] = now;
      hudNm[bus] = true;
    }
  }
  learnSides(now);
  int idx = m.extd ? -1 : gwIndex(id);
  if (bus == gHud) {
    if (idx >= 0) {            // the main panda's guidance frame: remember it, keep it on the HUD side
      seenUs[idx] = now;
      seen[idx] = true;
      keptCnt++;
      return -1;
    }
    return gCar;
  }
  if (idx >= 0) {              // the AVN's guidance frame: not while ours is fresh
    int ref = id == 0x4A9 ? 0 : idx;     // the text goes with the turn-by-turn frame
    if (recentUs(seen[ref], seenUs[ref], now, GW_FRESH_US)) {
      blockCnt++;
      return -1;
    }
  }
  return gHud;
}

static void forwardTask(void *arg) {
  int bus = (int)(intptr_t)arg;
  twai_message_t m;
  for (;;) {
    if (!gBusUp[bus]) {
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }
    if (twai_receive_v2(gBus[bus], &m, pdMS_TO_TICKS(100)) != ESP_OK) continue;
    rxCnt[bus]++;
    int to = route(bus, m, (uint32_t)micros());
    if (to < 0 || !gBusUp[to]) continue;
    m.self = 0;
    m.ss = 0;
    if (twai_transmit_v2(gBus[to], &m, 0) == ESP_OK) fwdCnt[bus]++;
    else dropCnt[to]++;
  }
}

static bool startBus(int bus) {
  twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT_V2(bus, (gpio_num_t)(bus ? CAN_B_TX : CAN_A_TX),
                                                           (gpio_num_t)(bus ? CAN_B_RX : CAN_A_RX), TWAI_MODE_NORMAL);
  g.rx_queue_len = 64;
  g.tx_queue_len = 64;
  g.alerts_enabled = TWAI_ALERT_BUS_OFF | TWAI_ALERT_BUS_RECOVERED | TWAI_ALERT_BUS_ERROR | TWAI_ALERT_RX_QUEUE_FULL;
  twai_timing_config_t t = TWAI_TIMING_CONFIG_100KBITS();
  twai_filter_config_t f = TWAI_FILTER_CONFIG_ACCEPT_ALL();
  if (twai_driver_install_v2(&g, &t, &f, &gBus[bus]) != ESP_OK) return false;
  if (twai_start_v2(gBus[bus]) != ESP_OK) return false;
  gBusUp[bus] = true;
  return true;
}

// Bus-off (e.g. a wire off): recover and start again; counted, shown on the status page.
static void watchBuses() {
  for (int b = 0; b < 2; b++) {
    if (gBus[b] == NULL) continue;
    uint32_t alerts = 0;
    if (twai_read_alerts_v2(gBus[b], &alerts, 0) != ESP_OK) continue;
    if (alerts & TWAI_ALERT_BUS_ERROR) busErr[b]++;
    if (alerts & TWAI_ALERT_RX_QUEUE_FULL) dropCnt[b]++;
    if (alerts & TWAI_ALERT_BUS_OFF) {
      busOffCnt[b]++;
      gBusUp[b] = false;
      outf("%9lu  module %c bus-off: recovering\n", (unsigned long)millis(), 'A' + b);
      twai_initiate_recovery_v2(gBus[b]);
    }
    if (alerts & TWAI_ALERT_BUS_RECOVERED) {
      twai_start_v2(gBus[b]);
      gBusUp[b] = true;
      outf("%9lu  module %c recovered\n", (unsigned long)millis(), 'A' + b);
    }
  }
}

// ---------- web ----------
static const char *PAGE_HEAD =
    "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,"
    "initial-scale=1'><title>DH MCAN gateway</title><style>body{font-family:sans-serif;margin:16px}"
    "input,button{font-size:18px;margin:8px 0;width:100%}td{padding:2px 10px 2px 0}</style></head><body>";

void webRoot() {
  uint32_t now = (uint32_t)micros();
  String o = PAGE_HEAD;
  o += "<h2>DH M-CAN 게이트웨이</h2><p>펌웨어 " FW_VERSION "</p><table>";
  o += "<tr><td>차량 쪽</td><td>모듈 ";
  o += (char)('A' + gCar);
  o += sidesLearned ? " (확인됨)" : " (기본값, 아직 확인 전)";
  o += "</td></tr><tr><td>HUD 쪽</td><td>모듈 ";
  o += (char)('A' + gHud);
  o += "</td></tr>";
  for (int b = 0; b < 2; b++) {
    o += "<tr><td>모듈 ";
    o += (char)('A' + b);
    o += "</td><td>받음 " + String((unsigned long)rxCnt[b]) + " / 넘김 " + String((unsigned long)fwdCnt[b]) +
         " / 못 보냄 " + String((unsigned long)dropCnt[b]) + " / 버스오류 " + String((unsigned long)busErr[b]) +
         " / 버스오프 " + String((unsigned long)busOffCnt[b]) + (gBusUp[b] ? "" : " <b>(꺼짐)</b>") + "</td></tr>";
  }
  bool ours = recentUs(seen[0], seenUs[0], now, GW_FRESH_US);
  o += "<tr><td>우리 안내</td><td>";
  o += ours ? "나가는 중 (순정 안내 막음)" : "없음 (순정 안내 통과)";
  o += "</td></tr><tr><td>막은 순정 안내</td><td>" + String((unsigned long)blockCnt) + "</td></tr>";
  o += "<tr><td>HUD 쪽에 둔 우리 안내</td><td>" + String((unsigned long)keptCnt) + "</td></tr></table>";
  o += "<p><a href='/log'>기록</a> · <a href='/update'>펌웨어 업데이트</a> · <a href='/wifi'>Wi-Fi 설정</a></p></body></html>";
  gWeb.send(200, "text/html; charset=utf-8", o);
}

void webLog() {
  uint32_t n = logHead < LOG_RING ? logHead : LOG_RING;
  String o;
  o.reserve(n + 64);
  o += "now ";
  o += String((unsigned long)millis());
  o += " ms\n";
  for (uint32_t i = logHead - n; i != logHead; i++) {
    char ch = logRing[i % LOG_RING];
    if (ch) o += ch;
  }
  gWeb.send(200, "text/plain; charset=utf-8", o);
}

void webWifiPage() {
  String o = PAGE_HEAD;
  o += "<h2>Wi-Fi 설정</h2><p>지금 저장된 핫스팟: ";
  o += gSsid.length() ? gSsid : String("(없음)");
  o += "</p><form method='post' action='/wifi'>이름(SSID)<input name='ssid' maxlength='32' autocapitalize='none'>"
       "비밀번호<input name='pass' maxlength='64' autocapitalize='none'><button type='submit'>저장</button></form></body></html>";
  gWeb.send(200, "text/html; charset=utf-8", o);
}

void webWifiSave() {
  String ssid = gWeb.arg("ssid"), pass = gWeb.arg("pass");
  ssid.trim();
  if (!ssid.length()) return gWeb.send(400, "text/plain; charset=utf-8", "이름이 비었습니다");
  gPrefs.putString("ssid", ssid);
  gPrefs.putString("pass", pass);
  gWeb.send(200, "text/html; charset=utf-8", "<meta charset='utf-8'><h2>저장했습니다. 보드를 다시 시작합니다.</h2>");
  delay(500);
  ESP.restart();
}

static bool otaAuthed = false, otaStarted = false;

void webUpdatePage() {
  if (!gWeb.authenticate(OTA_USER, SETUP_AP_PASS)) return gWeb.requestAuthentication();
  String o = PAGE_HEAD;
  o += "<h2>펌웨어 업데이트</h2><p>현재 버전: " FW_VERSION "</p><p><b>P단에서</b> 하세요. 올리는 동안과 다시 "
       "시작하는 몇 초 동안 순정 HUD 가 차와 끊깁니다.</p><form method='post' action='/update' enctype='multipart/form-data'>"
       "<input type='file' name='fw' accept='.bin'><button type='submit'>올리기</button></form></body></html>";
  gWeb.send(200, "text/html; charset=utf-8", o);
}

void webUpdateDone() {
  bool ok = otaAuthed && otaStarted && !Update.hasError();
  if (!otaAuthed) return gWeb.requestAuthentication();
  gWeb.send(200, "text/html; charset=utf-8",
            ok ? "<meta charset='utf-8'><h2>완료. 보드를 다시 시작합니다.</h2>"
               : "<meta charset='utf-8'><h2>실패. 기존 펌웨어 그대로입니다.</h2><a href='/update'>다시</a>");
  if (ok) {
    out("firmware update done, restarting\n");
    delay(500);
    ESP.restart();
  }
}

void webUpdateUpload() {
  HTTPUpload &u = gWeb.upload();
  if (u.status == UPLOAD_FILE_START) {
    otaAuthed = gWeb.authenticate(OTA_USER, SETUP_AP_PASS);
    otaStarted = false;
    if (!otaAuthed) return;
    outf("firmware update: %s\n", u.filename.c_str());
    otaStarted = Update.begin(UPDATE_SIZE_UNKNOWN);
  } else if (u.status == UPLOAD_FILE_WRITE) {
    if (otaStarted && Update.write(u.buf, u.currentSize) != u.currentSize) otaStarted = false;
  } else if (u.status == UPLOAD_FILE_END) {
    if (otaStarted) otaStarted = Update.end(true);
    outf("firmware update: %u bytes, %s\n", (unsigned)u.totalSize, otaStarted ? "ok" : "FAILED");
  } else if (u.status == UPLOAD_FILE_ABORTED) {
    if (otaStarted) Update.abort();
    otaStarted = false;
  }
}

static void startWeb() {
  if (webStarted) return;
  gWeb.on("/", webRoot);
  gWeb.on("/log", webLog);
  gWeb.on("/wifi", HTTP_GET, webWifiPage);
  gWeb.on("/wifi", HTTP_POST, webWifiSave);
  gWeb.on("/update", HTTP_GET, webUpdatePage);
  gWeb.on("/update", HTTP_POST, webUpdateDone, webUpdateUpload);
  gWeb.begin();
  webStarted = true;
}

// The hotspot when one is saved; the setup Wi-Fi when it does not connect for 30 s (3 s with
// nothing saved). The gateway itself never waits for Wi-Fi.
static void wifiTick(uint32_t now) {
  static bool wasUp = false;
  bool up = WiFi.status() == WL_CONNECTED;
  if (up && !wasUp) {
    outf("%9lu  Wi-Fi \"%s\" %s (http://%s/)\n", (unsigned long)now, gSsid.c_str(),
         WiFi.localIP().toString().c_str(), WiFi.localIP().toString().c_str());
    startWeb();
  }
  wasUp = up;
  if (up || apOpen) return;
  if (now - wifiTryMs > (gSsid.length() ? 30000UL : 3000UL)) {
    WiFi.mode(WIFI_AP_STA);
    WiFi.softAP(SETUP_AP_SSID, SETUP_AP_PASS);
    apOpen = true;
    outf("%9lu  setup Wi-Fi \"%s\" open (http://192.168.4.1/)\n", (unsigned long)now, SETUP_AP_SSID);
    startWeb();
  }
}

// ---------- serial ----------
static void serialLine(char *line) {
  while (*line == ' ') line++;
  if (line[0] == 'w' && line[1] == ' ') {       // w NAME|PASSWORD
    char *bar = strchr(line + 2, '|');
    if (!bar) return out("w NAME|PASSWORD\n");
    *bar = 0;
    gPrefs.putString("ssid", line + 2);
    gPrefs.putString("pass", bar + 1);
    out("hotspot saved, restarting\n");
    delay(300);
    ESP.restart();
  } else if (line[0] == 's') {
    outf("car = module %c%s, HUD = module %c | A rx %lu fwd %lu | B rx %lu fwd %lu | blocked %lu kept %lu\n",
         'A' + gCar, sidesLearned ? "" : " (default)", 'A' + gHud, (unsigned long)rxCnt[0], (unsigned long)fwdCnt[0],
         (unsigned long)rxCnt[1], (unsigned long)fwdCnt[1], (unsigned long)blockCnt, (unsigned long)keptCnt);
  } else if (line[0]) {
    out("s = status   w NAME|PASSWORD = save the hotspot\n");
  }
}

void setup() {
  Serial.begin(115200);
  // The gateway first: the car sends some frames only once at wake-up.
  bool a = startBus(0), b = startBus(1);
  xTaskCreate(forwardTask, "gw-a", 4096, (void *)(intptr_t)0, configMAX_PRIORITIES - 2, NULL);
  xTaskCreate(forwardTask, "gw-b", 4096, (void *)(intptr_t)1, configMAX_PRIORITIES - 2, NULL);
  outf("\nGenesis DH MM-CAN gateway (ESP32-C6), firmware " FW_VERSION "\n"
       "  module A TX %d RX %d %s, module B TX %d RX %d %s, 100 kbit/s\n",
       CAN_A_TX, CAN_A_RX, a ? "ok" : "FAILED", CAN_B_TX, CAN_B_RX, b ? "ok" : "FAILED");
  gPrefs.begin("mcangw", false);
  gSsid = gPrefs.getString("ssid", "");
  gPass = gPrefs.getString("pass", "");
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  if (gSsid.length()) {
    WiFi.begin(gSsid.c_str(), gPass.c_str());
    outf("  Wi-Fi: trying \"%s\"\n", gSsid.c_str());
  }
  wifiTryMs = millis();
}

void loop() {
  static char line[120];
  static int len = 0;
  static uint32_t lastReport = 0;
  uint32_t now = millis();
  watchBuses();
  wifiTick(now);
  if (webStarted) gWeb.handleClient();
  while (Serial.available()) {
    char ch = (char)Serial.read();
    if (ch == '\n' || ch == '\r') {
      line[len] = 0;
      if (len) serialLine(line);
      len = 0;
    } else if (len < (int)sizeof(line) - 1) {
      line[len++] = ch;
    }
  }
  if (now - lastReport >= 60000) {      // one line a minute: enough to see a side stop
    lastReport = now;
    outf("%9lu  A rx %lu fwd %lu drop %lu err %lu | B rx %lu fwd %lu drop %lu err %lu | blocked %lu kept %lu\n",
         (unsigned long)now, (unsigned long)rxCnt[0], (unsigned long)fwdCnt[0], (unsigned long)dropCnt[0],
         (unsigned long)busErr[0], (unsigned long)rxCnt[1], (unsigned long)fwdCnt[1], (unsigned long)dropCnt[1],
         (unsigned long)busErr[1], (unsigned long)blockCnt, (unsigned long)keptCnt);
  }
  delay(2);
}
