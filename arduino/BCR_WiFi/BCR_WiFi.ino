/**
 * BCR Environmental Monitor v2.0 — WiFi Edition
 * Hardware: ESP32 + DHT20 (I2C) + BH1750 (I2C) + 2x HW-390 Soil + 2x SSR
 *
 * Changes from v1.0:
 *   - WiFi connectivity to Beast server over local network / Tailscale
 *   - HTTP POST sensor readings to /api/grow/readings every cycle
 *   - Receives relay commands in the POST response
 *   - Serial JSON output retained for debugging
 *   - Graceful WiFi reconnect on disconnection
 *
 * Libraries required (install via Arduino Library Manager):
 *   - "DHT20" by Rob Tillaart
 *   - "BH1750" by Christopher Laws
 *   - WiFi + HTTPClient are built into ESP32 Arduino core
 *
 * ============================================================================
 * ⚠ WARNING — HIGH VOLTAGE HAZARD ⚠
 * This firmware controls solid-state relays (SSRs) wired to AC mains voltage
 * (120V or 240V AC). Exposure to mains voltage is potentially lethal.
 * - All AC-side wiring must be performed by a qualified electrician.
 * - Use properly rated SSRs, fuses, and enclosed terminal blocks.
 * - Never probe or touch AC terminals while the system is energised.
 * - Test this firmware with low-voltage loads before connecting AC loads.
 * ============================================================================
 *
 * Serial commands (115200 baud, send with newline):
 *   ON1  / OFF1  — Manual control of SSR1 (Humidifier)
 *   ON2  / OFF2  — Manual control of SSR2 (Reserved / future AC unit)
 */

#include <Wire.h>
#include <DHT20.h>      // https://github.com/RobTillaart/DHT20
#include <BH1750.h>     // https://github.com/claws/BH1750
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h> // https://github.com/bblanchon/ArduinoJson — v7+

// ─── WiFi Configuration ─────────────────────────────────────────────────────
// ⚠ Change these to match your network. For Tailscale, use the Beast's
//   Tailscale IP or DNS name and ensure the ESP32 is on a network that
//   can reach it (same LAN or via Tailscale subnet router).

const char* WIFI_SSID     = "NOKIA-E8C7";      // ← CHANGE THIS
const char* WIFI_PASSWORD = "ZfhXgPbLdNg4zJ";   // ← CHANGE THIS

// ─── Beast Server Configuration ──────────────────────────────────────────────
// Use the LAN IP for fastest response. Tailscale hostname works too if
// the ESP32's network can resolve it.

const char* BEAST_HOST    = "192.168.1.7";        // ← CHANGE THIS (Beast LAN IP)
const int   BEAST_PORT    = 5199;
const char* API_ENDPOINT  = "/api/grow/readings";

// How many seconds between WiFi POSTs (independent of sensor read interval).
// Sensor reads happen every SENSOR_INTERVAL_MS. WiFi POST happens every
// POST_INTERVAL_MS. Set POST higher to reduce network load.
#define POST_INTERVAL_MS   10000UL  // 10 seconds between HTTP POSTs

// ─── Pin Definitions ─────────────────────────────────────────────────────────

#define SOIL_PIN_1   34   // ADC1_CH6 — HW-390 Capacitive Soil Sensor 1
#define SOIL_PIN_2   35   // ADC1_CH7 — HW-390 Capacitive Soil Sensor 2
#define SSR_PIN_1    25   // GPIO25   — SSR1: Humidifier (Active HIGH)
#define SSR_PIN_2    26   // GPIO26   — SSR2: Reserved / future AC unit (Active HIGH)

// ─── Soil Moisture Calibration ───────────────────────────────────────────────

#define SOIL1_DRY_VALUE   3350
#define SOIL1_WET_VALUE   1355
#define SOIL2_DRY_VALUE   3350
#define SOIL2_WET_VALUE   1355

// ─── Light / PPFD Conversion ────────────────────────────────────────────────

#define LUX_TO_PPFD   0.0150f   // µmol/m²/s per lux — full-spectrum white LED COB

// ─── Control Thresholds ──────────────────────────────────────────────────────

#define HUMIDITY_THRESHOLD_PCT   55.0f   // % RH — humidifier turns ON below this

// ─── Timing ──────────────────────────────────────────────────────────────────

#define SENSOR_INTERVAL_MS   2000UL   // Sensor read + relay evaluation (ms)

// ─── Objects & State ─────────────────────────────────────────────────────────

DHT20  dht20;
BH1750 lightMeter;

struct SensorReadings {
  float temperature;
  float humidity;
  float ppfd;
  int   soil1;
  int   soil2;
  bool  dhtValid;
};

struct RelayState {
  bool ssr1;
  bool ssr2;
};

SensorReadings readings       = { 0, 0, -1, 0, 0, false };
RelayState     relays         = { false, false };
unsigned long  lastSensorTime = 0;
unsigned long  lastPostTime   = 0;
bool           wifiConnected  = false;
int            postFailCount  = 0;

// ─────────────────────────────────────────────────────────────────────────────
//  SETUP
// ─────────────────────────────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  delay(500);

  Serial.println(F("\n=== BCR Environmental Monitor v2.0 (WiFi) ==="));
  Serial.println(F("WARNING: SSRs switch AC mains — high-voltage hazard!"));
  Serial.println(F("Serial commands: ON1 | OFF1 | ON2 | OFF2\n"));

  setupRelays();
  setupSensors();
  setupWiFi();

  Serial.println(F("[BOOT] Initialisation complete. Starting loop in 5 seconds..."));
  for (int i = 5; i > 0; i--) {
    Serial.print(F("[BOOT] Starting in "));
    Serial.print(i);
    Serial.println(F("s..."));
    delay(1000);
  }
  Serial.println(F("[BOOT] Loop started.\n"));
}

// ─────────────────────────────────────────────────────────────────────────────
//  MAIN LOOP
// ─────────────────────────────────────────────────────────────────────────────

void loop() {
  handleSerialCommands();
  maintainWiFi();

  unsigned long now = millis();

  // Sensor read cycle (every 2s)
  if (now - lastSensorTime >= SENSOR_INTERVAL_MS) {
    lastSensorTime = now;

    readDHT20();
    readBH1750();
    readSoilMoisture();
    controlRelays();
    printReadings();
  }

  // WiFi POST cycle (every POST_INTERVAL_MS)
  if (wifiConnected && (now - lastPostTime >= POST_INTERVAL_MS)) {
    lastPostTime = now;
    postReadings();
  }
}

// ─────────────────────────────────────────────────────────────────────────────
//  WiFi SETUP & MAINTENANCE
// ─────────────────────────────────────────────────────────────────────────────

void setupWiFi() {
  Serial.print(F("[WIFI] Connecting to "));
  Serial.print(WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  // Wait up to 15 seconds for connection
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(F("."));
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    wifiConnected = true;
    Serial.println(F(" connected!"));
    Serial.print(F("[WIFI] IP: "));
    Serial.println(WiFi.localIP());
    Serial.print(F("[WIFI] Target: http://"));
    Serial.print(BEAST_HOST);
    Serial.print(F(":"));
    Serial.print(BEAST_PORT);
    Serial.println(API_ENDPOINT);
  } else {
    wifiConnected = false;
    Serial.println(F(" FAILED — running in offline mode (serial only)."));
    Serial.println(F("[WIFI] Will retry every 30 seconds."));
  }
}

/**
 * Non-blocking WiFi reconnection check.
 * Called every loop iteration — returns immediately if connected.
 */
void maintainWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    if (!wifiConnected) {
      wifiConnected = true;
      postFailCount = 0;
      Serial.println(F("[WIFI] Reconnected."));
      Serial.print(F("[WIFI] IP: "));
      Serial.println(WiFi.localIP());
    }
    return;
  }

  // Disconnected
  if (wifiConnected) {
    wifiConnected = false;
    Serial.println(F("[WIFI] Connection lost — will auto-reconnect."));
  }

  // Non-blocking reconnect attempt every ~30 seconds
  static unsigned long lastReconnect = 0;
  unsigned long now = millis();
  if (now - lastReconnect >= 30000UL) {
    lastReconnect = now;
    Serial.println(F("[WIFI] Attempting reconnect..."));
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
//  HTTP POST TO BEAST
// ─────────────────────────────────────────────────────────────────────────────

/**
 * POST current sensor readings to Beast's /api/grow/readings endpoint.
 * Parses the response for relay commands (piggy-backed on the 201 response).
 */
void postReadings() {
  HTTPClient http;

  String url = String("http://") + BEAST_HOST + ":" + String(BEAST_PORT) + API_ENDPOINT;
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(5000);  // 5 second timeout

  // Build JSON payload — matches the field names the API expects
  JsonDocument doc;
  if (readings.dhtValid) {
    doc["temp"]     = round(readings.temperature * 10.0f) / 10.0f;
    doc["humidity"] = round(readings.humidity * 10.0f) / 10.0f;
  }
  if (readings.ppfd >= 0) {
    doc["ppfd"] = round(readings.ppfd * 10.0f) / 10.0f;
  }
  doc["soil1"]      = readings.soil1;
  doc["soil2"]      = readings.soil2;
  doc["humidifier"] = relays.ssr1 ? "ON" : "OFF";
  doc["ssr2"]       = relays.ssr2 ? "ON" : "OFF";
  doc["source"]     = "esp32";

  String payload;
  serializeJson(doc, payload);

  int httpCode = http.POST(payload);

  if (httpCode == 201 || httpCode == 200) {
    postFailCount = 0;

    // Parse response for relay commands
    String response = http.getString();
    JsonDocument respDoc;
    DeserializationError err = deserializeJson(respDoc, response);

    if (!err && respDoc.containsKey("commands")) {
      JsonArray commands = respDoc["commands"].as<JsonArray>();
      for (JsonVariant cmd : commands) {
        executeRemoteCommand(cmd.as<const char*>());
      }
    }

    // Print success indicator (compact)
    Serial.print(F("[POST] OK ("));
    Serial.print(httpCode);
    Serial.println(F(")"));
  } else {
    postFailCount++;
    Serial.print(F("[POST] FAIL code="));
    Serial.print(httpCode);
    if (httpCode < 0) {
      Serial.print(F(" err="));
      Serial.print(http.errorToString(httpCode));
    }
    Serial.println();

    // Back off if we're failing repeatedly
    if (postFailCount >= 10) {
      Serial.println(F("[POST] 10 consecutive failures — backing off for 60s."));
      lastPostTime += 50000UL;  // Skip ahead ~50s extra
      postFailCount = 0;
    }
  }

  http.end();
}

/**
 * Execute a relay command received from the Beast server.
 * Format: "ON1", "OFF1", "ON2", "OFF2"
 */
void executeRemoteCommand(const char* cmd) {
  if (!cmd) return;

  String command = String(cmd);
  command.trim();
  command.toUpperCase();

  Serial.print(F("[REMOTE] Executing: "));
  Serial.println(command);

  if (command == "ON1") {
    setRelay1(true);
  } else if (command == "OFF1") {
    setRelay1(false);
  } else if (command == "ON2") {
    setRelay2(true);
  } else if (command == "OFF2") {
    setRelay2(false);
  } else {
    Serial.print(F("[REMOTE] Unknown command: "));
    Serial.println(command);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
//  SETUP HELPERS
// ─────────────────────────────────────────────────────────────────────────────

void setupRelays() {
  pinMode(SSR_PIN_1, OUTPUT);
  pinMode(SSR_PIN_2, OUTPUT);
  digitalWrite(SSR_PIN_1, LOW);
  digitalWrite(SSR_PIN_2, LOW);
  relays.ssr1 = false;
  relays.ssr2 = false;
  Serial.println(F("[RELAY] SSR1 and SSR2 initialised → OFF."));
}

void i2cScan() {
  Serial.println(F("[I2C] Scanning bus..."));
  int found = 0;
  for (byte addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.print(F("[I2C]   Device found at 0x"));
      if (addr < 16) Serial.print(F("0"));
      Serial.println(addr, HEX);
      found++;
    }
  }
  if (found == 0) Serial.println(F("[I2C]   No devices found — check wiring!"));
  else { Serial.print(F("[I2C] ")); Serial.print(found); Serial.println(F(" device(s) found.")); }
}

void setupSensors() {
  Wire.begin();
  delay(100);
  i2cScan();

  dht20.begin();
  delay(100);
  dht20.read();
  Serial.println(F("[SENSOR] DHT20 ready."));

  if (lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE)) {
    Serial.println(F("[SENSOR] BH1750 ready."));
  } else {
    Serial.println(F("[SENSOR] ERROR: BH1750 not found — check I2C wiring!"));
  }

  Serial.println(F("[SENSOR] Soil sensors on GPIO34 (soil1), GPIO35 (soil2)."));
}

// ─────────────────────────────────────────────────────────────────────────────
//  SENSOR READ FUNCTIONS
// ─────────────────────────────────────────────────────────────────────────────

void readDHT20() {
  int status = dht20.read();
  if (status == DHT20_OK) {
    readings.temperature = dht20.getTemperature();
    readings.humidity    = dht20.getHumidity();
    readings.dhtValid    = true;
  } else {
    Serial.print(F("[DHT20] Read failed, error code: "));
    Serial.println(status);
    readings.dhtValid = false;
  }
}

void readBH1750() {
  float lux = lightMeter.readLightLevel();
  if (lux < 0) {
    Serial.println(F("[BH1750] Read error — check sensor connection."));
    readings.ppfd = -1.0f;
  } else {
    readings.ppfd = lux * LUX_TO_PPFD;
  }
}

void readSoilMoisture() {
  int raw1 = analogRead(SOIL_PIN_1);
  int raw2 = analogRead(SOIL_PIN_2);
  readings.soil1 = normalizeSoil(raw1, SOIL1_DRY_VALUE, SOIL1_WET_VALUE);
  readings.soil2 = normalizeSoil(raw2, SOIL2_DRY_VALUE, SOIL2_WET_VALUE);
}

int normalizeSoil(int raw, int dryVal, int wetVal) {
  int moisture = map(raw, dryVal, wetVal, 0, 100);
  return constrain(moisture, 0, 100);
}

// ─────────────────────────────────────────────────────────────────────────────
//  RELAY CONTROL
// ─────────────────────────────────────────────────────────────────────────────

void controlRelays() {
  if (readings.dhtValid) {
    setRelay1(readings.humidity < HUMIDITY_THRESHOLD_PCT);
  }
}

void setRelay1(bool on) {
  if (relays.ssr1 == on) return;
  relays.ssr1 = on;
  digitalWrite(SSR_PIN_1, on ? HIGH : LOW);
  Serial.print(F("[RELAY] SSR1 (Humidifier) → "));
  Serial.println(on ? F("ON") : F("OFF"));
}

void setRelay2(bool on) {
  if (relays.ssr2 == on) return;
  relays.ssr2 = on;
  digitalWrite(SSR_PIN_2, on ? HIGH : LOW);
  Serial.print(F("[RELAY] SSR2 (Reserved)   → "));
  Serial.println(on ? F("ON") : F("OFF"));
}

// ─────────────────────────────────────────────────────────────────────────────
//  SERIAL OUTPUT (kept for debugging — same JSON format as v1.0)
// ─────────────────────────────────────────────────────────────────────────────

void printReadings() {
  Serial.println(F("{"));

  Serial.print(F("  \"temp\": "));
  if (readings.dhtValid) Serial.print(readings.temperature, 1);
  else                   Serial.print(F("null"));
  Serial.println(F(","));

  Serial.print(F("  \"humidity\": "));
  if (readings.dhtValid) Serial.print(readings.humidity, 1);
  else                   Serial.print(F("null"));
  Serial.println(F(","));

  Serial.print(F("  \"ppfd\": "));
  if (readings.ppfd >= 0) Serial.print(readings.ppfd, 1);
  else                    Serial.print(F("null"));
  Serial.println(F(","));

  Serial.print(F("  \"soil1\": "));
  Serial.print(readings.soil1);
  Serial.println(F(","));

  Serial.print(F("  \"soil2\": "));
  Serial.print(readings.soil2);
  Serial.println(F(","));

  Serial.print(F("  \"humidifier\": \""));
  Serial.print(relays.ssr1 ? F("ON") : F("OFF"));
  Serial.println(F("\","));

  Serial.print(F("  \"ssr2\": \""));
  Serial.print(relays.ssr2 ? F("ON") : F("OFF"));
  Serial.println(F("\""));

  Serial.println(F("}"));
  Serial.println();
}

// ─────────────────────────────────────────────────────────────────────────────
//  SERIAL COMMAND HANDLER
// ─────────────────────────────────────────────────────────────────────────────

void handleSerialCommands() {
  if (!Serial.available()) return;

  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  cmd.toUpperCase();

  if (cmd == "ON1") {
    setRelay1(true);
    Serial.println(F("[CMD] SSR1 forced ON (manual override)."));
  } else if (cmd == "OFF1") {
    setRelay1(false);
    Serial.println(F("[CMD] SSR1 forced OFF (manual override)."));
  } else if (cmd == "ON2") {
    setRelay2(true);
    Serial.println(F("[CMD] SSR2 forced ON (manual override)."));
  } else if (cmd == "OFF2") {
    setRelay2(false);
    Serial.println(F("[CMD] SSR2 forced OFF (manual override)."));
  } else if (cmd == "STATUS") {
    Serial.println(F("[STATUS] WiFi: "));
    Serial.println(wifiConnected ? F("connected") : F("disconnected"));
    Serial.print(F("[STATUS] IP: "));
    Serial.println(WiFi.localIP());
    Serial.print(F("[STATUS] POST fails: "));
    Serial.println(postFailCount);
  } else if (cmd.length() > 0) {
    Serial.print(F("[CMD] Unknown command: \""));
    Serial.print(cmd);
    Serial.println(F("\"  →  Valid: ON1 | OFF1 | ON2 | OFF2 | STATUS"));
  }
}
