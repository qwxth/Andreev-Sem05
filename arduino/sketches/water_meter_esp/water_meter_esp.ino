#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>

const char* WIFI_SSID = "CarameL_";
const char* WIFI_PASSWORD = "A22N28D17G17";
const char* SERVER_URL = "http://192.168.1.11:5000/water";

String inputBuffer = "";

void setup() {
// Hardware Serial для Arduino - НЕ для USB!
Serial.begin(9600);
delay(3000);

pinMode(2, OUTPUT);
digitalWrite(2, HIGH);

WiFi.mode(WIFI_STA);
WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

// Ждём WiFi БЕЗ логов (Serial занят для Arduino)
int attempts = 0;
while (WiFi.status() != WL_CONNECTED && attempts < 30) {
delay(500);
digitalWrite(2, !digitalRead(2)); // Мигать LED при подключении
attempts++;
}

if (WiFi.status() == WL_CONNECTED) {
digitalWrite(2, HIGH); // LED OFF = WiFi OK
} else {
// Быстро мигать = ошибка WiFi
for(int i=0; i<20; i++) {
digitalWrite(2, !digitalRead(2));
delay(100);
}
}
}

void loop() {
// Читать ТОЛЬКО от Hardware Serial (Arduino)
while (Serial.available()) {
char c = Serial.read();
if (c == '\n' || c == '\r') {
inputBuffer.trim();
if (inputBuffer.startsWith("{") && inputBuffer.endsWith("}") && inputBuffer.length() > 5) {
sendToServer(inputBuffer);
inputBuffer = "";
} else {
inputBuffer = ""; // Игнорировать мусор
}
} else {
inputBuffer += c;
if (inputBuffer.length() > 100) inputBuffer = ""; // Защита от переполнения
}
}
}

void sendToServer(String jsonData) {
if (WiFi.status() != WL_CONNECTED) return;

digitalWrite(2, LOW); // LED ON при отправке

WiFiClient client;
HTTPClient http;
http.begin(client, SERVER_URL);
http.addHeader("Content-Type", "application/json");
http.POST(jsonData);
http.end();

digitalWrite(2, HIGH); // LED OFF
}
