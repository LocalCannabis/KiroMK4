/**
 * BCR Environmental Monitor v1.0
 * Hardware: ESP32 + DHT20 (I2C) + BH1750 (I2C) + 2x HW-390 Soil + 2x SSR
 *
 * Libraries required (install via Arduino Library Manager):
 *   - "DHT20" by Rob Tillaart
 *   - "BH1750" by Christopher Laws
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
#include <DHT20.h>    // https://github.com/RobTillaart/DHT20
#include <BH1750.h>   // https://github.com/claws/BH1750

// ─── Pin Definitions ─────────────────────────────────────────────────────────

#define SOIL_PIN_1   34   // ADC1_CH6 — HW-390 Capacitive Soil Sensor 1
#define SOIL_PIN_2   35   // ADC1_CH7 — HW-390 Capacitive Soil Sensor 2
#define SSR_PIN_1    25   // GPIO25   — SSR1: Humidifier (Active HIGH)
#define SSR_PIN_2    26   // GPIO26   — SSR2: Reserved / future AC unit (Active HIGH)

// ─── Soil Moisture Calibration ───────────────────────────────────────────────
// Procedure: read ADC with sensor in dry air → DRY_VALUE
//            read ADC with sensor submerged in water → WET_VALUE
// Higher ADC reading = drier soil (capacitive sensors are inverted)

#define SOIL1_DRY_VALUE   3350   // ADC in dry air    (calibrated: range 3312–3390)
#define SOIL1_WET_VALUE   1355   // ADC fully in water (calibrated: range 1320–1390)
#define SOIL2_DRY_VALUE   3350
#define SOIL2_WET_VALUE   1355

// ─── Light / PPFD Conversion ────────────────────────────────────────────────
// The BH1750 measures lux (human-vision weighted). PPFD (µmol/m²/s) is
// estimated using a per-source conversion factor. Adjust for your light type:
//   Full-spectrum LED grow light : ~0.0150
//   Sunlight / daylight spectrum  : ~0.0185
//   HPS / MH                      : ~0.0147
//   CMH / LEC                     : ~0.0150
// NOTE: For accurate PPFD a dedicated quantum/PAR sensor is required.

#define LUX_TO_PPFD   0.0150f   // µmol/m²/s per lux — calibrated for full-spectrum white LED COB
                                // Hardware: 4x CREE COB 800W (200W actual) grow lights

// ─── Control Thresholds ──────────────────────────────────────────────────────

#define HUMIDITY_THRESHOLD_PCT   55.0f   // % RH — humidifier turns ON below this value

// ─── Timing ──────────────────────────────────────────────────────────────────

#define SENSOR_INTERVAL_MS   2000UL   // Sensor read + relay evaluation period (ms)

// ─── Objects & State ─────────────────────────────────────────────────────────

DHT20  dht20;
BH1750 lightMeter;

struct SensorReadings {
  float temperature;   // °C
  float humidity;      // % RH
  float ppfd;          // µmol/m²/s (estimated from lux via LUX_TO_PPFD factor)
  int   soil1;         // % moisture (0–100)
  int   soil2;         // % moisture (0–100)
  bool  dhtValid;      // false if DHT20 read failed this cycle
};

struct RelayState {
  bool ssr1;   // true = ON
  bool ssr2;   // true = ON
};

SensorReadings readings    = { 0, 0, -1, 0, 0, false };
RelayState     relays      = { false, false };
unsigned long  lastSensorTime = 0;

// ─────────────────────────────────────────────────────────────────────────────
//  SETUP
// ─────────────────────────────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  // Brief pause so Serial Monitor can connect before first output
  delay(500);

  Serial.println(F("\n=== BCR Environmental Monitor v1.0 ==="));
  Serial.println(F("WARNING: SSRs switch AC mains — high-voltage hazard!"));
  Serial.println(F("Serial commands: ON1 | OFF1 | ON2 | OFF2\n"));

  setupRelays();
  setupSensors();

  Serial.println(F("[BOOT] Initialisation complete. Starting loop in 10 seconds — read output above now."));
  for (int i = 10; i > 0; i--) {
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
  // Non-blocking serial command processing runs every iteration
  handleSerialCommands();

  // Non-blocking timed sensor cycle
  unsigned long now = millis();
  if (now - lastSensorTime >= SENSOR_INTERVAL_MS) {
    lastSensorTime = now;

    readDHT20();
    readBH1750();
    readSoilMoisture();
    controlRelays();
    printReadings();
  }
}

// ─────────────────────────────────────────────────────────────────────────────
//  SETUP HELPERS
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Configure SSR output pins and guarantee both relays are OFF at boot.
 * SAFETY: Always initialise relay outputs before any other code runs.
 */
void setupRelays() {
  pinMode(SSR_PIN_1, OUTPUT);
  pinMode(SSR_PIN_2, OUTPUT);
  digitalWrite(SSR_PIN_1, LOW);   // LOW = relay OFF (Active HIGH SSR)
  digitalWrite(SSR_PIN_2, LOW);
  relays.ssr1 = false;
  relays.ssr2 = false;
  Serial.println(F("[RELAY] SSR1 and SSR2 initialised → OFF."));
}

/**
 * Scan all 127 I2C addresses and print which ones respond.
 * Run this to verify physical wiring before sensor init.
 * Expected: 0x23 (BH1750), 0x38 (DHT20)
 */
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

/**
 * Initialise I2C bus and both I2C sensors.
 * ESP32 defaults: SDA = GPIO21, SCL = GPIO22.
 */
void setupSensors() {
  Wire.begin();   // SDA=21, SCL=22
  delay(100);     // allow bus to stabilise
  i2cScan();      // print all responding I2C addresses

  // DHT20 — address 0x38, no configuration required
  dht20.begin();
  // Discard first (incomplete) measurement from internal buffer
  delay(100);
  dht20.read();
  Serial.println(F("[SENSOR] DHT20 ready."));

  // BH1750 — default address 0x23 (ADDR pin LOW)
  if (lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE)) {
    Serial.println(F("[SENSOR] BH1750 ready."));
  } else {
    Serial.println(F("[SENSOR] ERROR: BH1750 not found — check I2C wiring!"));
  }

  // HW-390 soil sensors — input-only ADC pins (GPIO34/35 have no pull-up)
  Serial.println(F("[SENSOR] Soil sensors on GPIO34 (soil1), GPIO35 (soil2)."));
}

// ─────────────────────────────────────────────────────────────────────────────
//  SENSOR READ FUNCTIONS
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Read temperature and relative humidity from DHT20 over I2C.
 * Sets readings.dhtValid = false on communication error.
 */
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

/**
 * Read ambient light from BH1750 (lux) and convert to estimated PPFD.
 * Stores -1.0 on sensor error.
 */
void readBH1750() {
  float lux = lightMeter.readLightLevel();

  if (lux < 0) {
    Serial.println(F("[BH1750] Read error — check sensor connection."));
    readings.ppfd = -1.0f;
  } else {
    readings.ppfd = lux * LUX_TO_PPFD;
  }
}

/**
 * Read both capacitive soil sensors and normalise raw ADC to 0–100%.
 * GPIO34 and GPIO35 are 12-bit ADC-only pins on the ESP32.
 */
void readSoilMoisture() {
  int raw1 = analogRead(SOIL_PIN_1);
  int raw2 = analogRead(SOIL_PIN_2);

  readings.soil1 = normalizeSoil(raw1, SOIL1_DRY_VALUE, SOIL1_WET_VALUE);
  readings.soil2 = normalizeSoil(raw2, SOIL2_DRY_VALUE, SOIL2_WET_VALUE);
}

/**
 * Map a raw ADC reading to a 0–100% moisture percentage.
 * Capacitive sensors output HIGH ADC when dry and LOW ADC when wet.
 * @param raw     Raw 12-bit ADC value (0–4095)
 * @param dryVal  ADC reading in dry air (calibration)
 * @param wetVal  ADC reading submerged in water (calibration)
 * @return        Moisture percentage clamped to [0, 100]
 */
int normalizeSoil(int raw, int dryVal, int wetVal) {
  int moisture = map(raw, dryVal, wetVal, 0, 100);
  return constrain(moisture, 0, 100);
}

// ─────────────────────────────────────────────────────────────────────────────
//  RELAY CONTROL
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Evaluate sensor readings and apply automatic relay control logic.
 * SSR2 is intentionally left to manual-only control in this version.
 */
void controlRelays() {
  if (readings.dhtValid) {
    // Humidifier: turn ON when humidity falls below threshold
    setRelay1(readings.humidity < HUMIDITY_THRESHOLD_PCT);
  }
  // SSR2 is reserved — controlled only via serial commands
}

void setRelay1(bool on) {
  if (relays.ssr1 == on) return;   // no change, skip GPIO write
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
//  SERIAL OUTPUT
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Print all current readings as a JSON-compatible block for easy parsing
 * and visual inspection in the Serial Monitor.
 */
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

/**
 * Parse and execute single-line serial commands.
 * Call every loop() iteration — non-blocking, returns immediately if no data.
 *
 * Commands:
 *   ON1 / OFF1 — Force SSR1 (Humidifier) ON or OFF
 *   ON2 / OFF2 — Force SSR2 (Reserved)   ON or OFF
 *
 * Note: Automatic control will resume on the next sensor cycle and may
 * override a manual command if the threshold condition is still met.
 */
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
  } else if (cmd.length() > 0) {
    Serial.print(F("[CMD] Unknown command: \""));
    Serial.print(cmd);
    Serial.println(F("\"  →  Valid: ON1 | OFF1 | ON2 | OFF2"));
  }
}
