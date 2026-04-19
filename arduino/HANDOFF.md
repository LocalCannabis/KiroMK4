# BCR Environmental Monitor — Agent Handoff Document

## Project Overview

An ESP32-based environmental monitoring and relay control system for a cannabis grow room.
Firmware is complete and validated on hardware. Next phase is Flask server integration and
AI grow hub agent connection.

---

## Hardware

| Component | Model | Interface |
|---|---|---|
| Microcontroller | ESP32-WROOM-32 (ESP-32S) | USB (CP2102) |
| Temp/Humidity | DHT20 | I2C — address `0x38` |
| Light sensor | BH1750 | I2C — address `0x23` (ADO pin tied to GND) |
| Soil sensors (×2) | HW-390 Capacitive | Analog — GPIO34 (soil1), GPIO35 (soil2) |
| Relay 1 (SSR) | Solid State Relay | GPIO25 — Active HIGH — controls humidifier |
| Relay 2 (SSR) | Solid State Relay | GPIO26 — Active HIGH — reserved (future AC unit) |

**I2C wiring:** SDA = GPIO21, SCL = GPIO22 (ESP32 defaults)

**Grow lights:** 4× CREE COB 800W (200W actual), full-spectrum white LED

---

## Firmware Summary

**File:** `BCR/BCR.ino`
**Framework:** Arduino (ESP32 Arduino core by Espressif)
**Serial baud:** 115200

### Libraries Required
Install via Arduino Library Manager:
- `DHT20` by Rob Tillaart
- `BH1750` by Christopher Laws
- `Wire.h` (built-in)

### Key Configuration Constants

```cpp
// Soil calibration (measured on actual hardware)
#define SOIL1_DRY_VALUE   3350   // ADC dry air    (range 3312–3390)
#define SOIL1_WET_VALUE   1355   // ADC in water   (range 1320–1390)
#define SOIL2_DRY_VALUE   3350
#define SOIL2_WET_VALUE   1355

// PPFD conversion (BH1750 lux → estimated µmol/m²/s)
#define LUX_TO_PPFD       0.0150f   // Full-spectrum white COB LED

// Humidity control threshold
#define HUMIDITY_THRESHOLD_PCT   55.0f   // Humidifier ON below this % RH

// Sensor polling interval
#define SENSOR_INTERVAL_MS   2000UL     // 2 seconds
```

### Serial Output Format

The ESP32 emits a JSON block over Serial every 2 seconds at 115200 baud:

```json
{
  "temp": 22.7,
  "humidity": 46.4,
  "ppfd": 1.6,
  "soil1": 38,
  "soil2": 42,
  "humidifier": "ON",
  "ssr2": "OFF"
}
```

| Field | Unit | Notes |
|---|---|---|
| `temp` | °C | `null` if DHT20 read fails |
| `humidity` | % RH | `null` if DHT20 read fails |
| `ppfd` | µmol/m²/s | Estimated from lux × 0.015. BH1750 saturates at 65535 lux (~983 µmol/m²/s). `null` on sensor error |
| `soil1` | % moisture | 0 = dry air, 100 = submerged. Calibrated to actual hardware |
| `soil2` | % moisture | Same calibration as soil1 |
| `humidifier` | `"ON"` / `"OFF"` | Auto-controlled by humidity threshold |
| `ssr2` | `"ON"` / `"OFF"` | Manual-only via serial command in current firmware |

### Serial Commands (manual relay override)

Send over serial (newline-terminated, case-insensitive):

| Command | Action |
|---|---|
| `ON1` | Force SSR1 (humidifier) ON |
| `OFF1` | Force SSR1 OFF |
| `ON2` | Force SSR2 ON |
| `OFF2` | Force SSR2 OFF |

> **Note:** Auto relay logic re-evaluates every 2 seconds and will override manual commands if the threshold condition is still active.

### Relay Control Logic

- **SSR1 (Humidifier):** Turns ON automatically when `humidity < 55.0%`. Turns OFF otherwise.
- **SSR2:** Manual control only (reserved for future AC unit).
- Both relays initialise to **OFF** on boot — safe default.

---

## Flask Integration Plan

### Recommended Approach

Read the ESP32's serial JSON output on the host machine and ingest into Flask.

**Option A — Direct USB Serial (simple, local)**
- ESP32 connected via USB to the Flask server machine
- Python reads `/dev/tty.SLAB_USBtoUART` (macOS) or `/dev/ttyUSB0` (Linux) at 115200 baud
- Parse JSON each cycle and store/forward to Flask endpoint or database

```python
import serial, json

ser = serial.Serial('/dev/tty.SLAB_USBtoUART', 115200, timeout=3)
buffer = ''

while True:
    line = ser.readline().decode('utf-8', errors='ignore').strip()
    buffer += line
    if line == '}':
        try:
            data = json.loads(buffer)
            # POST to Flask or write to DB
        except json.JSONDecodeError:
            pass
        buffer = ''
```

**Option B — WiFi/MQTT (next firmware phase)**
- Add WiFi + MQTT publish to the ESP32 firmware (WiFi credentials + broker config)
- Flask subscribes to MQTT topic
- Enables wireless, multi-room deployment

### Suggested Flask Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/readings` | `GET` | Latest sensor snapshot |
| `/api/readings/history` | `GET` | Time-series data (requires DB) |
| `/api/relay/<id>` | `POST` | Manual relay control (body: `{"state": "ON"}`) |
| `/api/config/thresholds` | `GET/POST` | Read/update control thresholds |

### AI Grow Hub Agent Context

Feed the following as system context / tool state:

```
Sensor data arrives as JSON every 2 seconds.
Units: temp=°C, humidity=%RH, ppfd=µmol/m²/s, soil=%moisture(0-100), relays=ON/OFF string.
PPFD is estimated (BH1750 lux × 0.015), not measured by quantum sensor.
Soil 0% = dry air, 100% = submerged in water — real substrate will read 30–70% when healthy.
Humidifier (SSR1) is auto-controlled at 55% RH threshold; SSR2 is manual-only.
Lights: 4× CREE COB 800W (200W actual), full-spectrum white LED.
Target grow parameters (cannabis): temp 22–28°C, humidity 50–70% (veg) / 40–50% (flower),
PPFD 400–600 (veg) / 600–1000+ (flower), soil 40–70%.
```

---

## Known Limitations / Future Work

| Item | Notes |
|---|---|
| PPFD estimation | BH1750 is a lux sensor, not a quantum sensor. Accuracy ±20% vs dedicated PAR meter. Upgrade to Apogee SQ-500 for precision. |
| BH1750 saturation | Max readable lux = 65535 → ~983 µmol/m²/s. COBs at close range may exceed this. |
| Soil calibration | Calibrated to water immersion. Accuracy in substrate depends on medium (coco, soil, hydro have different dielectric constants). Re-calibrate in actual media if needed. |
| WiFi not yet added | Firmware currently requires USB serial connection to host. Option B above outlines the upgrade path. |
| SSR2 auto-control | Not yet implemented — placeholder for AC unit / dehumidifier logic. |
| Single humidity sensor | DHT20 is one-point measurement. Large grow rooms may need multiple sensors. |
| No data persistence | ESP32 has no local logging. All historical data must be captured by the Flask host. |

---

## File Structure

```
BCR/
├── BCR.ino          # Complete ESP32 firmware (Arduino)
└── HANDOFF.md       # This document
```

---

## Validated Hardware Status

All sensors confirmed working on physical hardware:

- [x] DHT20 — temperature and humidity reading correctly
- [x] BH1750 — PPFD (lux) reading correctly (ADO pin wired to GND)
- [x] Soil sensor 1 (GPIO34) — calibrated, reading correctly
- [x] Soil sensor 2 (GPIO35) — calibrated, reading correctly
- [x] SSR1 GPIO25 — relay switching confirmed, auto humidity control active
- [x] SSR2 GPIO26 — relay switching confirmed via serial command
- [x] Serial JSON output — clean, parseable, 2-second interval
