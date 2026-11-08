#include <Arduino.h>

#if !defined(ARDUINO_ARCH_ESP32)
#error "Для этой прошивки выбери плату ESP32 Dev Module вместо Arduino Nano"
#else

#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include <driver/gpio.h>
#include <esp_timer.h>
#include <math.h>
#include "meter_logic.h"

#if __has_include("config.h")
#include "config.h"
#else
#include "config.example.h"
#endif

static_assert(GPIO_IS_VALID_GPIO(SENSOR_PIN),
              "SENSOR_PIN недоступен на выбранной модели ESP32: проверь распиновку");

constexpr int LED_PIN = 2;  // Проверенный встроенный LED D2: HIGH = горит.

Preferences storage;
WebServer readingServer(80);
portMUX_TYPE counterMux = portMUX_INITIALIZER_UNLOCKED;
MeterState meterState;
String deviceId;
uint32_t savedPulses = 0;
bool storageReady = false;
bool savedSnapshotValid = false;
bool calibrationReady = false;
bool accessPointReady = false;

MeterState readState() {
  portENTER_CRITICAL(&counterMux);
  const MeterState value = meterState;
  portEXIT_CRITICAL(&counterMux);
  return value;
}

double totalLiters(uint32_t pulses) {
  return meteredLiters(pulses, LITERS_PER_PULSE, INITIAL_TOTAL_LITERS);
}

void sensorTask(void*) {
  // Уже замкнутый при старте контакт является исходным уровнем, а не импульсом.
  DebouncedContact contact(digitalRead(SENSOR_PIN) == LOW, millis(), DEBOUNCE_MS);
  portENTER_CRITICAL(&counterMux);
  meterState.closed = contact.isClosed();
  portEXIT_CRITICAL(&counterMux);
  digitalWrite(LED_PIN, contact.isClosed() ? HIGH : LOW);
  for (;;) {
    if (contact.update(digitalRead(SENSOR_PIN) == LOW, millis())) {
      const bool closed = contact.isClosed();
      portENTER_CRITICAL(&counterMux);
      recordStableContact(meterState, closed);
      portEXIT_CRITICAL(&counterMux);
      digitalWrite(LED_PIN, closed ? HIGH : LOW);
    }
    // Опрос и фильтр продолжают работать во время обслуживания HTTP.
    vTaskDelay(pdMS_TO_TICKS(5) > 0 ? pdMS_TO_TICKS(5) : 1);
  }
}

bool saveSnapshot(uint32_t current) {
  if (!storageReady || !validPulseCount(current)) return false;
  if (savedSnapshotValid && current == savedPulses) return true;
  // Формат рабочего счётчика сохранён: blob из одного uint32_t, 4 байта.
  if (storage.putBytes("pulses", &current, sizeof(current)) == sizeof(current)) {
    savedPulses = current;
    savedSnapshotValid = true;
    return true;
  }
  return false;
}

void handleReading() {
  const MeterState current = readState();
  const double liters = totalLiters(current.pulses);
  const bool valid = calibrationReady && validPulseCount(current.pulses) && isfinite(liters);
  // Успешно отвечаем только уже сохранённым снимком, а не более новым значением из RAM.
  const bool ready = valid && saveSnapshot(current.pulses);

  String payload;
  payload.reserve(220);
  payload = "{\"liters\":";
  payload += ready ? String(liters, 3) : String("null");
  payload += ",\"ready\":";
  payload += ready ? "true" : "false";
  payload += ",\"storage_ok\":";
  payload += ready ? "true" : "false";
  payload += ",\"contact_closed\":";
  payload += current.closed ? "true" : "false";
  payload += ",\"device_id\":\"";
  payload += deviceId;
  payload += "\",\"uptime_ms\":";
  payload += String(static_cast<unsigned long long>(esp_timer_get_time() / 1000));
  if (!ready) {
    payload += ",\"error\":\"";
    payload += !valid ? "invalid_calibration_or_counter" : "storage_unavailable";
    payload += "\"";
  }
  payload += "}";
  readingServer.sendHeader("Cache-Control", "no-store");
  readingServer.send(ready ? 200 : 503, "application/json", payload);
}

void setup() {
  pinMode(SENSOR_PIN, INPUT_PULLUP);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, digitalRead(SENSOR_PIN) == LOW ? HIGH : LOW);
  calibrationReady = isfinite(INITIAL_TOTAL_LITERS) && INITIAL_TOTAL_LITERS >= 0 &&
                     isfinite(LITERS_PER_PULSE) && LITERS_PER_PULSE > 0;

  // Действующее пространство и ключ не меняем при переходе на собственную Wi-Fi сеть.
  storageReady = storage.begin("water-deb10-v4", false);
  if (storageReady && storage.isKey("pulses")) {
    uint32_t restored = 0;
    if (storage.getBytesLength("pulses") == sizeof(restored) &&
        storage.getBytes("pulses", &restored, sizeof(restored)) == sizeof(restored) &&
        validPulseCount(restored)) {
      meterState.pulses = savedPulses = restored;
      savedSnapshotValid = true;
    } else {
      // Повреждённый blob не перезаписываем нулём. /reading будет отвечать 503.
      storageReady = false;
    }
  }

  meterState.closed = digitalRead(SENSOR_PIN) == LOW;
  digitalWrite(LED_PIN, meterState.closed ? HIGH : LOW);
  if (xTaskCreate(sensorTask, "reed", 2048, nullptr, 2, nullptr) != pdPASS) {
    while (true) delay(1000);
  }

  const IPAddress localIp(192, 168, 4, 1);
  const IPAddress subnet(255, 255, 255, 0);
  if (!WiFi.mode(WIFI_AP) || !WiFi.softAPConfig(localIp, localIp, subnet) ||
      !WiFi.softAP(AP_SSID, AP_PASSWORD, AP_CHANNEL, 0, AP_MAX_CLIENTS)) {
    // Даже без сети loop продолжит сохранять изменения счётчика.
    return;
  }
  // MAC точки доступа устойчив для этой ESP32 и не зависит от адреса ПК по DHCP.
  deviceId = WiFi.softAPmacAddress();
  readingServer.on("/reading", HTTP_GET, handleReading);
  readingServer.on("/health", HTTP_GET, handleReading);
  readingServer.on("/", HTTP_GET, []() {
    readingServer.send(200, "application/json",
                       "{\"reading\":\"/reading\",\"health\":\"/health\"}");
  });
  readingServer.onNotFound([]() {
    readingServer.send(404, "application/json", "{\"error\":\"not_found\"}");
  });
  readingServer.begin();
  accessPointReady = true;
}

void loop() {
  // Каждый новый отсчёт сохраняется независимо от наличия подключённого ПК.
  // saveSnapshot не пишет flash повторно, если счётчик не изменился.
  saveSnapshot(readState().pulses);
  if (accessPointReady) readingServer.handleClient();
  delay(5);
}

#endif  // ARDUINO_ARCH_ESP32
