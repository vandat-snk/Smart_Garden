#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <DHT.h>
#include <BH1750.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <time.h>
#if __has_include("garden_config.h")
#include "garden_config.h"
#else
#include "garden_config.example.h"
#endif

// Only this task writes the relay after setup. HTTP latency cannot extend a pulse.
struct PumpRequest { bool start; uint32_t duration; char id[40]; };
struct AckEvent { char id[40]; char status[16]; char reason[80]; float elapsed; };
QueueHandle_t pumpQueue, ackQueue;
portMUX_TYPE stateMux = portMUX_INITIALIZER_UNLOCKED;
volatile bool pumpOn = false, sensorOk = false, reservoirOk = true;
volatile uint32_t lastContact = 0, lastSensor = 0;
volatile float soil = 0;
float temperature = 0, humidity = 0, lightLux = 0;
DHT dht(DHT_PIN, DHT22);
BH1750 lightSensor;
Preferences preferences;
String lastCommand;
uint32_t bootId, sequence = 0;

void pushAck(const char *id, const char *status, const char *reason, float elapsed) {
  AckEvent event{};
  strlcpy(event.id, id, sizeof(event.id));
  strlcpy(event.status, status, sizeof(event.status));
  strlcpy(event.reason, reason, sizeof(event.reason));
  event.elapsed = elapsed;
  // A full queue never delays a physical stop. Backend times out unacknowledged commands.
  xQueueSend(ackQueue, &event, 0);
}

void safetyTask(void *) {
  uint32_t started = 0, duration = 0;
  char activeId[40] = "";
  for (;;) {
    PumpRequest request{};
    bool healthy, water, on;
    uint32_t contact, sensed;
    float moisture;
    portENTER_CRITICAL(&stateMux);
    healthy = sensorOk; water = reservoirOk; on = pumpOn;
    contact = lastContact; sensed = lastSensor; moisture = soil;
    portEXIT_CRITICAL(&stateMux);
    uint32_t now = millis();
    if (xQueueReceive(pumpQueue, &request, 0) == pdTRUE) {
      if (!request.start) {
        digitalWrite(RELAY_PIN, RELAY_OFF);
        if (on) pushAck(activeId, "stopped", "Remote stop", (now - started)/1000.0f);
        on = false;
        pushAck(request.id, "stopped", "Relay off", 0);
      } else if (!on && healthy && water && now-sensed < 6000 && now-contact < 10000 && moisture < SOIL_STOP_PERCENT) {
        strlcpy(activeId, request.id, sizeof(activeId));
        duration = min(request.duration, (uint32_t)MAX_PUMP_MS);
        started = now; on = true;
        digitalWrite(RELAY_PIN, RELAY_ON);
      } else {
        pushAck(request.id, "rejected", "Local interlock", 0);
      }
    }
    if (on) {
      const char *reason = nullptr;
      if (now-started >= duration || now-started >= MAX_PUMP_MS) reason = "Pulse completed";
      else if (!healthy || now-sensed >= 6000) reason = "Sensor invalid or stale";
      else if (!water) reason = "Reservoir empty";
      else if (moisture >= SOIL_STOP_PERCENT) reason = "Soil stop threshold";
      else if (now-contact >= 10000 || WiFi.status() != WL_CONNECTED) reason = "Connection lost";
      if (reason) {
        digitalWrite(RELAY_PIN, RELAY_OFF); on = false;
        pushAck(activeId, "completed", reason, (now-started)/1000.0f);
      }
    }
    portENTER_CRITICAL(&stateMux); pumpOn = on; portEXIT_CRITICAL(&stateMux);
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

bool api(const String &path, const String &method, const String &body, JsonDocument &response) {
  if (WiFi.status() != WL_CONNECTED) return false;
  HTTPClient http;
  http.setConnectTimeout(1000); http.setTimeout(1200);
  http.begin(String(API_BASE) + path);
  http.addHeader("X-Device-Key", DEVICE_KEY);
  http.addHeader("Content-Type", "application/json");
  int code = method == "GET" ? http.GET() : http.POST(body);
  bool ok = code >= 200 && code < 300;
  if (ok) ok = !deserializeJson(response, http.getString());
  http.end();
  if (ok) { portENTER_CRITICAL(&stateMux); lastContact = millis(); portEXIT_CRITICAL(&stateMux); }
  return ok;
}

bool ackNow(const AckEvent &event) {
  JsonDocument body, result;
  body["device_id"] = DEVICE_ID; body["status"] = event.status;
  body["reason"] = event.reason; body["actual_duration_seconds"] = min(event.elapsed, 31.0f);
  String encoded; serializeJson(body, encoded);
  // Terminal backend states are final; no need to replay an old acknowledgement.
  return api("/api/device/commands/" + String(event.id) + "/ack", "POST", encoded, result);
}

void readSensors() {
  float t = dht.readTemperature(), h = dht.readHumidity();
  float lux = lightSensor.readLightLevel();
  int raw = 0;
  for (int i = 0; i < 16; ++i) raw += analogRead(SOIL_PIN);
  raw /= 16;
  bool ok = isfinite(t) && isfinite(h) && isfinite(lux) && t >= -40 && t <= 80 && h >= 0 && h <= 100 && lux >= 0 && lux <= 65535 && raw > 50 && raw < 4090 && SOIL_DRY_ADC > SOIL_WET_ADC;
  float percent = constrain(100.0f*(SOIL_DRY_ADC-raw)/(SOIL_DRY_ADC-SOIL_WET_ADC), 0.0f, 100.0f);
  bool water = !USE_RESERVOIR_SWITCH || digitalRead(RESERVOIR_PIN) == LOW;
  temperature = t; humidity = h; lightLux = lux;
  portENTER_CRITICAL(&stateMux);
  sensorOk = ok; reservoirOk = water; lastSensor = millis(); soil = percent;
  portEXIT_CRITICAL(&stateMux);
  Serial.printf("soil_adc=%d soil=%.1f temp=%.1f humidity=%.1f lux=%.1f ok=%d\n", raw,percent,t,h,lux,ok);
}

String timestamp(time_t value) {
  struct tm tm{}; gmtime_r(&value, &tm);
  char buffer[25]; strftime(buffer, sizeof(buffer), "%Y-%m-%dT%H:%M:%SZ", &tm);
  return String(buffer);
}

void telemetry() {
  time_t now = time(nullptr);
  if (now < 1700000000) return; // No valid NTP clock: never send an invented timestamp.
  JsonDocument body, response;
  body["device_id"] = DEVICE_ID;
  body["sample_id"] = String(bootId, HEX) + "-" + String(++sequence);
  body["timestamp"] = timestamp(now);
  bool ok = sensorOk;
  if (ok) {
    body["soil_moisture"] = soil; body["temperature"] = temperature;
    body["humidity"] = humidity; body["light_intensity"] = lightLux;
  }
  body["sensor_ok"] = ok; body["reservoir_ok"] = reservoirOk; body["pump_on"] = pumpOn;
  String encoded; serializeJson(body, encoded);
  api("/api/telemetry", "POST", encoded, response);
}

void pollCommand() {
  JsonDocument response;
  if (!api("/api/device/commands?device_id=" DEVICE_ID, "GET", "", response)) return;
  JsonObject c = response["command"].as<JsonObject>();
  if (c.isNull()) return;
  String id = c["id"] | "", action = c["action"] | "";
  if (id.length() != 36 || (action != "start" && action != "stop")) return;
  if (action == "stop") {
    PumpRequest request{}; strlcpy(request.id, id.c_str(), sizeof(request.id));
    xQueueSendToFront(pumpQueue, &request, 0);
    return;
  }
  if (id == lastCommand) { pushAck(id.c_str(), "rejected", "Already handled; never replay a pulse", 0); return; }
  int seconds = c["duration_seconds"] | 0;
  const char *expires = c["expires_at"] | "";
  struct tm expiry{};
  bool timeOk = strptime(expires, "%Y-%m-%dT%H:%M:%S", &expiry) != nullptr;
  time_t now = time(nullptr), deadline = timeOk ? mktime(&expiry) : 0;
  if (!timeOk || now < 1700000000 || now >= deadline || seconds < 1 || seconds*1000UL > MAX_PUMP_MS) {
    pushAck(id.c_str(), "rejected", "Expired or invalid command", 0); return;
  }
  // Persist before enabling relay; a power reset cannot replay an already seen pulse.
  lastCommand = id;
  if (preferences.putString("last_cmd", id) == 0) { pushAck(id.c_str(), "rejected", "NVS write failed", 0); return; }
  JsonDocument ack, result;
  ack["device_id"] = DEVICE_ID; ack["status"] = "started";
  ack["reason"] = "Accepted by firmware";
  String encoded; serializeJson(ack, encoded);
  if (!api("/api/device/commands/" + id + "/ack", "POST", encoded, result) || !result["accepted"].as<bool>()) return;
  PumpRequest request{}; request.start = true; request.duration = seconds*1000UL;
  strlcpy(request.id, id.c_str(), sizeof(request.id));
  if (xQueueSend(pumpQueue, &request, 0) != pdTRUE) pushAck(id.c_str(), "rejected", "Local queue full", 0);
}

void setup() {
  digitalWrite(RELAY_PIN, RELAY_OFF); pinMode(RELAY_PIN, OUTPUT); digitalWrite(RELAY_PIN, RELAY_OFF);
  Serial.begin(115200); pinMode(RESERVOIR_PIN, INPUT_PULLUP);
  analogReadResolution(12); analogSetPinAttenuation(SOIL_PIN, ADC_11db);
  Wire.begin(21, 22); dht.begin(); lightSensor.begin(BH1750::CONTINUOUS_HIGH_RES_MODE);
  pumpQueue = xQueueCreate(4, sizeof(PumpRequest)); ackQueue = xQueueCreate(12, sizeof(AckEvent));
  if (!pumpQueue || !ackQueue) { while (true) delay(1000); }
  preferences.begin("smartgarden", false); lastCommand = preferences.getString("last_cmd", "");
  bootId = esp_random();
  xTaskCreatePinnedToCore(safetyTask, "pump_safety", 4096, nullptr, 3, nullptr, 1);
  WiFi.mode(WIFI_STA); WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  configTime(0, 0, "pool.ntp.org", "time.google.com");
  setenv("TZ", "UTC0", 1); tzset();
}

void loop() {
  static uint32_t sensorAt=0, telemetryAt=0, pollAt=0, reconnectAt=0;
  uint32_t now = millis();
  if (now-sensorAt >= 2500) { sensorAt=now; readSensors(); }
  if (WiFi.status() != WL_CONNECTED) {
    if (now-reconnectAt >= 10000) { reconnectAt=now; WiFi.reconnect(); }
    delay(10); return;
  }
  AckEvent event{};
  if (xQueuePeek(ackQueue, &event, 0) == pdTRUE && ackNow(event)) xQueueReceive(ackQueue, &event, 0);
  if (now-telemetryAt >= 10000) { telemetryAt=now; telemetry(); }
  if (now-pollAt >= 2000) { pollAt=now; pollCommand(); }
  delay(10);
}
