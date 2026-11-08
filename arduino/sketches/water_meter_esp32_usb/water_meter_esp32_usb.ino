#include <Arduino.h>

// Первый этап: только геркон, встроенный светодиод D2 и монитор порта USB.
constexpr int SENSOR_PIN = 25;  // На плате подписан D25.
constexpr int LED_PIN = 2;      // Встроенный индикатор D2, не индикатор PWR.
constexpr bool LED_ACTIVE_LOW = false;
constexpr unsigned long DEBOUNCE_MS = 30;
constexpr unsigned long STATUS_INTERVAL_MS = 2000;

bool candidateClosed = false;
bool stableClosed = false;
unsigned long changedAt = 0;
unsigned long lastStatusAt = 0;
uint32_t pulseCount = 0;

void updateLed() {
  const bool highLevel = LED_ACTIVE_LOW ? !stableClosed : stableClosed;
  digitalWrite(LED_PIN, highLevel ? HIGH : LOW);
}

void printStatus() {
  const double liters = (pulseCount / 2) * 10.0 + (pulseCount % 2) * 8.0;
  Serial.printf("Геркон: %s | D25=%d | импульсов: %lu | литров: %.1f\n",
                stableClosed ? "ЗАМКНУТ" : "РАЗОМКНУТ",
                stableClosed ? 0 : 1,
                static_cast<unsigned long>(pulseCount), liters);
  lastStatusAt = millis();
}

void setup() {
  pinMode(SENSOR_PIN, INPUT_PULLUP);
  pinMode(LED_PIN, OUTPUT);
  Serial.begin(115200);

  // Уже замкнутый при включении контакт включает LED, но не создаёт импульс.
  candidateClosed = stableClosed = digitalRead(SENSOR_PIN) == LOW;
  changedAt = millis();
  updateLed();
  Serial.println("=== Проверка геркона по USB: D25 + GND, LED D2 ===");
  Serial.println("Расчёт +8/+2 л. При перезапуске тестовые показания обнуляются.");
  printStatus();
}

void loop() {
  const unsigned long now = millis();
  const bool closed = digitalRead(SENSOR_PIN) == LOW;

  if (closed != candidateClosed) {
    candidateClosed = closed;
    changedAt = now;
  }
  if (candidateClosed != stableClosed &&
      static_cast<unsigned long>(now - changedAt) >= DEBOUNCE_MS) {
    stableClosed = candidateClosed;
    if (stableClosed) ++pulseCount;
    updateLed();
    printStatus();
  }

  // Сообщение появится даже если монитор открыли позже запуска платы.
  if (static_cast<unsigned long>(millis() - lastStatusAt) >= STATUS_INTERVAL_MS) {
    printStatus();
  }
  delay(1);
}
