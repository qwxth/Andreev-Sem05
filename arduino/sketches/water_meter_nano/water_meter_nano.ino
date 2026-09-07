#include <SoftwareSerial.h>

const int SENSOR_PIN = 2;
const int LED_PIN = 12;
const unsigned long DEBOUNCE_TIME = 3000;  // 3 секунды защиты от помех
const int LED_FLASH_TIME = 500;

SoftwareSerial espSerial(10, 11); // RX, TX

volatile bool newPulse = false;
unsigned long lastValidPulse = 0;
int pulseCount = 0;
float totalLiters = 0;
bool isFirstPulse = true;

void setup() {
  Serial.begin(9600);
  espSerial.begin(9600);

  pinMode(SENSOR_PIN, INPUT);  // БЕЗ pullup для схемы с +5V
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  attachInterrupt(digitalPinToInterrupt(SENSOR_PIN), detectPulse, RISING);  // RISING для +5V

  Serial.println("=== Счетчик готов ===");
  delay(2000);
}

void loop() {
  if (newPulse) {
    unsigned long currentTime = millis();

    if (currentTime - lastValidPulse > DEBOUNCE_TIME) {
      lastValidPulse = currentTime;
      pulseCount++;

      if (isFirstPulse) {
        totalLiters += 10.0;
        isFirstPulse = false;
      } else {
        totalLiters += 2.0;
        isFirstPulse = true;
      }

      digitalWrite(LED_PIN, HIGH);
      delay(LED_FLASH_TIME);
      digitalWrite(LED_PIN, LOW);

      String data = "{\"pulses\":" + String(pulseCount) +
                    ",\"liters\":" + String(totalLiters, 1) + "}";

      espSerial.println(data);

      Serial.print("TX: ");
      Serial.println(data);
    } else {
      Serial.println("Помеха игнорирована");
    }

    newPulse = false;
  }

  delay(50);
}

void detectPulse() {
  newPulse = true;
}
