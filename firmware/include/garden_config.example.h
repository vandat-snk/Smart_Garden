#pragma once
#define WIFI_SSID "YOUR_WIFI"
#define WIFI_PASSWORD "YOUR_WIFI_PASSWORD"
#define API_BASE "http://192.168.1.10:8000"
#define DEVICE_ID "garden-01"
#define DEVICE_KEY "COPY_DEVICE_KEY_FROM_ENV"
#define SOIL_PIN 34
#define DHT_PIN 4
#define RELAY_PIN 26
#define RELAY_ON LOW
#define RELAY_OFF HIGH
#define SOIL_DRY_ADC 3000
#define SOIL_WET_ADC 1200
#define SOIL_STOP_PERCENT 60.0f
#define MAX_PUMP_MS 30000UL
// Optional float switch: LOW means enough water; unplugged = empty (pull-up).
#define USE_RESERVOIR_SWITCH false
#define RESERVOIR_PIN 27
