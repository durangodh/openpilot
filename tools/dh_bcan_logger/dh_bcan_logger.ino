// Genesis DH B-CAN listen-only logger (ESP32-S3, Arduino-ESP32 3.x).
//
// Finds the turn-signal lever message on the low-speed (fault-tolerant) B-CAN at the
// multifunction switch connector M01 (pin 1 green = CAN-H, pin 2 orange = CAN-L, pin 6 = ground).
// The TWAI controller runs in LISTEN_ONLY mode with no TX queue: it never transmits and never
// ACKs, so nothing is put on the car's bus. See README.ko.md for wiring and the procedure.
//
// Serial 115200 commands (one letter, then Enter or just the key):
//   n  record a 5 s baseline: lever neutral, touch nothing
//   l  5 s window: hold the LEFT lever  -> prints what differs from the baseline
//   r  5 s window: hold the RIGHT lever -> same
//   x  5 s control window: touch nothing -> whatever shows up here is NOT the lever
//   s  list every ID seen (count, rate, last data)
//   c  print changes on/off (an ID's data changed since its previous frame)
//   v  print every frame on/off (busy)
//   m  marker line in the log (BOOT button does the same)
//   a  scan bit rates and keep the one that receives frames without errors
//   b  next bit rate by hand
//   h  help
#include <Arduino.h>
#include <driver/twai.h>

// ---- wiring ---------------------------------------------------------------------------------
// Method 1 (default): TJA1055 (3.3 V logic, "T/3") module on the header pins.
//   IO4 -> module TXD, IO5 <- module RXD. EN/STB on IO6/IO7 are driven HIGH (normal mode);
//   tying them to 3.3 V on the module instead is also fine.
// Method 2: low-speed <-> high-speed converter into the board's own CAN terminals:
//   set CAN_TX_PIN 15, CAN_RX_PIN 16 and FT_EN_PIN/FT_STB_PIN -1.
#define CAN_TX_PIN 4
#define CAN_RX_PIN 5
#define FT_EN_PIN 6
#define FT_STB_PIN 7
#define BOOT_BUTTON_PIN 0
// ----------------------------------------------------------------------------------------------

struct Rate { const char *name; uint32_t brp; };  // brp 0: use the IDF preset for that name
static const Rate RATES[] = {
  {"100k", 0}, {"125k", 0}, {"83.3k", 48}, {"50k", 0}, {"33.3k", 120}, {"250k", 0}, {"500k", 0},
};
static const int RATE_CNT = sizeof(RATES) / sizeof(RATES[0]);
static int rateIdx = 0;  // B-CAN on Hyundai is usually 100 kbit/s (DH M-CAN is 100k too)

static bool installed = false;

static twai_timing_config_t timingFor(int i) {
  twai_timing_config_t t = TWAI_TIMING_CONFIG_100KBITS();
  const char *n = RATES[i].name;
  if (!strcmp(n, "125k")) { twai_timing_config_t p = TWAI_TIMING_CONFIG_125KBITS(); t = p; }
  else if (!strcmp(n, "50k")) { twai_timing_config_t p = TWAI_TIMING_CONFIG_50KBITS(); t = p; }
  else if (!strcmp(n, "250k")) { twai_timing_config_t p = TWAI_TIMING_CONFIG_250KBITS(); t = p; }
  else if (!strcmp(n, "500k")) { twai_timing_config_t p = TWAI_TIMING_CONFIG_500KBITS(); t = p; }
  if (RATES[i].brp) {
    // 80 MHz APB / brp, 20 time quanta per bit (1 + tseg_1 15 + tseg_2 4)
    t.quanta_resolution_hz = 0;
    t.brp = RATES[i].brp;
    t.tseg_1 = 15;
    t.tseg_2 = 4;
    t.sjw = 3;
  }
  return t;
}

static bool startCan(int i) {
  if (installed) {
    twai_stop();
    twai_driver_uninstall();
    installed = false;
  }
  twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT((gpio_num_t)CAN_TX_PIN, (gpio_num_t)CAN_RX_PIN,
                                                        TWAI_MODE_LISTEN_ONLY);
  g.tx_queue_len = 0;   // no transmit queue at all
  g.rx_queue_len = 128;
  g.alerts_enabled = TWAI_ALERT_BUS_ERROR | TWAI_ALERT_RX_QUEUE_FULL;
  twai_timing_config_t t = timingFor(i);
  twai_filter_config_t f = TWAI_FILTER_CONFIG_ACCEPT_ALL();
  installed = twai_driver_install(&g, &t, &f) == ESP_OK && twai_start() == ESP_OK;
  rateIdx = i;
  return installed;
}

// ---- output to both the native USB port and UART0, whichever the board's USB-C uses ----------
static void out(const char *s) {
  Serial.print(s);
#if ARDUINO_USB_CDC_ON_BOOT
  Serial0.print(s);
#endif
}
static void outf(const char *fmt, ...) {
  char buf[256];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  out(buf);
}
static int readCmd() {
  if (Serial.available()) return Serial.read();
#if ARDUINO_USB_CDC_ON_BOOT
  if (Serial0.available()) return Serial0.read();
#endif
  return -1;
}

// ---- per-ID table ------------------------------------------------------------------------------
struct Entry {
  uint32_t id;
  bool ext;
  uint8_t dlc;
  uint8_t data[8];
  uint32_t count, firstMs, lastMs;
  // baseline (n): value at the end of the baseline and every bit that toggled during it
  bool hasBase;
  uint8_t base[8], noise[8];
  // learn window (l/r): bits seen different from the baseline value
  uint8_t diff[8];
  uint32_t diffCount, learnFrames;
};
static const int MAX_IDS = 300;
static Entry ids[MAX_IDS];
static int idCnt = 0;
static uint32_t rxTotal = 0, overflowIds = 0;

enum Phase { IDLE, BASELINE, LEARN };
static Phase phase = IDLE;
static char learnSide = 0;
static uint32_t phaseEnd = 0;
static const uint32_t WINDOW_MS = 5000;

static bool printChanges = true, printAll = false;
static int markerNo = 0;

static Entry *lookup(uint32_t id, bool ext) {
  for (int i = 0; i < idCnt; i++) {
    if (ids[i].id == id && ids[i].ext == ext) return &ids[i];
  }
  if (idCnt >= MAX_IDS) { overflowIds++; return nullptr; }
  Entry *e = &ids[idCnt++];
  memset(e, 0, sizeof(*e));
  e->id = id;
  e->ext = ext;
  return e;
}

static void hex8(char *dst, const uint8_t *d, uint8_t n) {
  dst[0] = 0;
  for (uint8_t i = 0; i < n; i++) sprintf(dst + strlen(dst), "%02X ", d[i]);
}

static void onFrame(const twai_message_t &m, uint32_t now) {
  rxTotal++;
  if (m.rtr) return;
  Entry *e = lookup(m.identifier, m.extd);
  if (!e) return;
  uint8_t n = m.data_length_code > 8 ? 8 : m.data_length_code;
  bool changed = e->count > 0 && (n != e->dlc || memcmp(e->data, m.data, n) != 0);

  if (phase == BASELINE && e->count > 0) {
    for (uint8_t i = 0; i < n; i++) e->noise[i] |= e->data[i] ^ m.data[i];
  }
  if (phase == LEARN && e->hasBase) {
    e->learnFrames++;
    bool any = false;
    for (uint8_t i = 0; i < n; i++) {
      uint8_t d = (m.data[i] ^ e->base[i]) & ~e->noise[i];
      e->diff[i] |= d;
      any |= d != 0;
    }
    if (any) e->diffCount++;
  }

  char was[32], now8[32];
  hex8(was, e->data, e->dlc);
  hex8(now8, m.data, n);
  if (printAll) {
    outf("%9lu  %s%03lX [%u] %s\n", (unsigned long)now, m.extd ? "x" : "", (unsigned long)m.identifier, n, now8);
  } else if (printChanges && changed && phase == IDLE) {
    outf("%9lu  %s%03lX  %s-> %s\n", (unsigned long)now, m.extd ? "x" : "", (unsigned long)m.identifier, was, now8);
  }

  e->dlc = n;
  memcpy(e->data, m.data, n);
  if (e->count == 0) e->firstMs = now;
  e->lastMs = now;
  e->count++;
}

static void startBaseline(uint32_t now) {
  for (int i = 0; i < idCnt; i++) memset(ids[i].noise, 0, 8);
  phase = BASELINE;
  phaseEnd = now + WINDOW_MS;
  out("\n=== BASELINE 5 s: lever neutral, hazard off, touch nothing ===\n");
}

static void endBaseline() {
  int n = 0;
  for (int i = 0; i < idCnt; i++) {
    Entry &e = ids[i];
    if (!e.count) continue;
    memcpy(e.base, e.data, 8);
    e.hasBase = true;
    n++;
  }
  phase = IDLE;
  outf("=== BASELINE done: %d IDs. Now press l (left) or r (right) and hold the lever. ===\n\n", n);
}

static const char *sideName(char side) { return side == 'l' ? "LEFT" : side == 'r' ? "RIGHT" : "CONTROL"; }

static void startLearn(char side, uint32_t now) {
  bool any = false;
  for (int i = 0; i < idCnt; i++) {
    memset(ids[i].diff, 0, 8);
    ids[i].diffCount = 0;
    ids[i].learnFrames = 0;
    any |= ids[i].hasBase;
  }
  if (!any) { out("No baseline yet: press n first.\n"); return; }
  learnSide = side;
  phase = LEARN;
  phaseEnd = now + WINDOW_MS;
  if (side == 'x') out("\n=== CONTROL 5 s: touch nothing ===\n");
  else outf("\n=== %s 5 s: hold the %s turn-signal lever now ===\n", sideName(side), sideName(side));
}

static void endLearn() {
  phase = IDLE;
  outf("=== %s result: bits that changed and were quiet in the baseline ===\n", sideName(learnSide));
  int shown = 0;
  for (int i = 0; i < idCnt; i++) {
    Entry &e = ids[i];
    if (!e.hasBase || !e.diffCount) continue;
    for (uint8_t b = 0; b < 8; b++) {
      if (!e.diff[b]) continue;
      unsigned pct = e.learnFrames ? (unsigned)(100UL * e.diffCount / e.learnFrames) : 0U;
      outf("  ID %s%03lX  byte %u  bits 0x%02X   baseline %02X  now %02X   %3u%% of %lu frames%s\n", e.ext ? "x" : "",
           (unsigned long)e.id, b, e.diff[b], e.base[b], e.data[b], pct, (unsigned long)e.learnFrames,
           pct >= 80 ? "  <== held" : "");
      shown++;
    }
  }
  if (shown) out("  '<== held': different in most frames while the lever was held (lever candidates).\n"
                 "  Low %: counters or flashing lamp status, not the lever.\n");
  if (!shown) out("  nothing. Check the bit rate (s shows frames?) and hold the lever for the whole window.\n");
  out("Release the lever and run the other side, then x (no lever). The lever bits: in l and r (different bits),\n"
      "never in x, and the same every repeat.\n\n");
}

static void listIds(uint32_t now) {
  twai_status_info_t st;
  bool ok = installed && twai_get_status_info(&st) == ESP_OK;
  outf("\n=== %s, %d IDs, %lu frames, bus errors %lu, rx missed %lu%s ===\n", RATES[rateIdx].name, idCnt,
       (unsigned long)rxTotal, ok ? (unsigned long)st.bus_error_count : 0UL,
       ok ? (unsigned long)st.rx_missed_count : 0UL, overflowIds ? " (ID table full)" : "");
  for (int i = 0; i < idCnt; i++) {
    Entry &e = ids[i];
    char d[32];
    hex8(d, e.data, e.dlc);
    float secs = (e.lastMs - e.firstMs) / 1000.0f;
    float hz = (e.count > 1 && secs > 0) ? (e.count - 1) / secs : 0.0f;
    outf("  %s%03lX  [%u] %-24s %6lu frames %6.1f Hz%s\n", e.ext ? "x" : "", (unsigned long)e.id, e.dlc, d,
         (unsigned long)e.count, hz, now - e.lastMs > 2000 ? "  (stopped)" : "");
  }
  out("\n");
}

static void drain(uint32_t now) {
  twai_message_t m;
  for (int i = 0; installed && i < 64 && twai_receive(&m, 0) == ESP_OK; i++) onFrame(m, now);
}

static void autoScan() {
  out("\n=== bit-rate scan (1.5 s each, listen-only) ===\n");
  int best = -1;
  uint32_t bestFrames = 0;
  for (int i = 0; i < RATE_CNT; i++) {
    if (!startCan(i)) { outf("  %-6s driver start failed\n", RATES[i].name); continue; }
    uint32_t frames = 0, t0 = millis();
    twai_message_t m;
    while (millis() - t0 < 1500) {
      if (twai_receive(&m, pdMS_TO_TICKS(10)) == ESP_OK) frames++;
    }
    twai_status_info_t st;
    uint32_t errs = twai_get_status_info(&st) == ESP_OK ? st.bus_error_count : 0;
    outf("  %-6s %5lu frames, %5lu bus errors\n", RATES[i].name, (unsigned long)frames, (unsigned long)errs);
    if (frames > bestFrames && errs * 10 <= frames) { best = i; bestFrames = frames; }
  }
  if (best < 0) {
    out("  no rate received clean frames. Check wiring, ignition ON, transceiver EN/STB, CAN-H/L not swapped.\n");
    best = 0;
  } else {
    outf("  -> using %s\n", RATES[best].name);
  }
  startCan(best);
  idCnt = 0;
  rxTotal = 0;
  overflowIds = 0;
  out("\n");
}

static void help() {
  out("\nGenesis DH B-CAN listen-only logger (never transmits)\n"
      "  n baseline 5 s | l left 5 s | r right 5 s | x control 5 s (no lever) | s list IDs | c changes on/off | v all frames on/off\n"
      "  m marker (or BOOT button) | a bit-rate scan | b next bit rate | h help\n");
  outf("  pins TX %d RX %d, bit rate %s, %s\n\n", CAN_TX_PIN, CAN_RX_PIN, RATES[rateIdx].name,
       installed ? "running" : "NOT running");
}

void setup() {
  Serial.begin(115200);
#if ARDUINO_USB_CDC_ON_BOOT
  Serial0.begin(115200);
#endif
  if (FT_EN_PIN >= 0) { pinMode(FT_EN_PIN, OUTPUT); digitalWrite(FT_EN_PIN, HIGH); }
  if (FT_STB_PIN >= 0) { pinMode(FT_STB_PIN, OUTPUT); digitalWrite(FT_STB_PIN, HIGH); }
  pinMode(BOOT_BUTTON_PIN, INPUT_PULLUP);
  delay(1500);
  startCan(rateIdx);
  help();
}

void loop() {
  uint32_t now = millis();
  drain(now);

  uint32_t alerts;
  if (installed && twai_read_alerts(&alerts, 0) == ESP_OK && (alerts & TWAI_ALERT_RX_QUEUE_FULL)) {
    out("! rx queue full: turn off v (print all) if it is on\n");
  }

  if (phase != IDLE && (int32_t)(now - phaseEnd) >= 0) {
    if (phase == BASELINE) endBaseline();
    else endLearn();
  }

  static bool btnPrev = true;
  static uint32_t btnMs = 0;
  bool btn = digitalRead(BOOT_BUTTON_PIN);
  if (!btn && btnPrev && now - btnMs > 300) {
    btnMs = now;
    outf("\n----- MARK %d  %lu ms -----\n", ++markerNo, (unsigned long)now);
  }
  btnPrev = btn;

  int c = readCmd();
  if (c < 0 || c == '\n' || c == '\r') return;
  switch (c) {
    case 'n': startBaseline(now); break;
    case 'l': case 'r': case 'x': startLearn((char)c, now); break;
    case 's': listIds(now); break;
    case 'c': printChanges = !printChanges; outf("changes %s\n", printChanges ? "on" : "off"); break;
    case 'v': printAll = !printAll; outf("all frames %s\n", printAll ? "on" : "off"); break;
    case 'm': outf("\n----- MARK %d  %lu ms -----\n", ++markerNo, (unsigned long)now); break;
    case 'a': autoScan(); break;
    case 'b':
      startCan((rateIdx + 1) % RATE_CNT);
      idCnt = 0; rxTotal = 0; overflowIds = 0;
      outf("bit rate %s\n", RATES[rateIdx].name);
      break;
    default: help(); break;
  }
}
