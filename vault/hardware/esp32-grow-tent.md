---
type: hardware
name: ESP32 Grow Tent Controller
persona: JACK
status: in-progress
sensors:
  - temperature
  - humidity
  - vpd
  - light (PAR/lux)
  - soil_moisture
actuators:
  - humidity_control
  - watering
protocol: wifi
board: ESP32
code_ref: arduino/BCR_WiFi.ino
---
3.

















# ESP32 Grow Tent Controller

Physical hardware node living in the grow tent. Reports environmental telemetry
to JACK and, eventually, receives automation commands back.

## Sensors
- **Temperature** — ambient air
- **Humidity** — ambient RH → feeds VPD calculation
- **VPD** — derived (temp + RH), critical for transpiration management
- **Light** — PAR or lux depending on sensor fitted
- **Soil moisture** — per-pot or aggregate

## Planned Actuators
- **Humidity control** — relay to humidifier/dehumidifier
- **Watering** — relay or valve control for automated fertigation

## Data Flow
ESP32 → WiFi → Beast → JACK intent router → response/automation command

## Code
See [arduino/BCR_WiFi.ino](../arduino/BCR_WiFi.ino) for current firmware.

## Wiring Diagram

```
                    ┌──────────────────────┐
                    │        ESP32         │
                    │                      │
      3V3 ──────────┼─── VCC sensors       │
      GND ──────────┼─── GND (common rail) │
                    │                      │
 SDA GPIO21 ────────┼─── DHT20 SDA         │
 SCL GPIO22 ────────┼─── DHT20 SCL         │
                    │        │             │
                    │        ├──────────────┐
                    │        │              │
                    │   BH1750 (I2C)       │
                    │   SDA ────────────────┘
                    │   SCL ────────────────┘
                    │                      │
 GPIO34 ────────────┼─── Soil Sensor 1 OUT │
 GPIO35 ────────────┼─── Soil Sensor 2 OUT │
                    │                      │
 GPIO25 ────────────┼─── SSR 1 (Humidifier)│
 GPIO26 ────────────┼─── SSR 2 (AC future) │
                    └──────────────────────┘
```

### Pin Summary

| GPIO | Function | Device |
|------|----------|--------|
| 21 (SDA) | I²C data | DHT20 + BH1750 |
| 22 (SCL) | I²C clock | DHT20 + BH1750 |
| 34 | Analog in | Soil moisture sensor 1 |
| 35 | Analog in | Soil moisture sensor 2 |
| 25 | Digital out | SSR 1 — Humidifier |
| 26 | Digital out | SSR 2 — AC (future) |
