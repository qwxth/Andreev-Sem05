#pragma once

// ESP32 создаёт свою сеть; ноутбук получает адрес автоматически через DHCP.
constexpr char AP_SSID[] = "WaterMeter";
constexpr char AP_PASSWORD[] = "12341234";
constexpr int AP_CHANNEL = 1;
constexpr int AP_MAX_CLIENTS = 1;

// Геркон: D25 — контакт — GND. Встроенный индикатор D2 задаётся в прошивке.
constexpr int SENSOR_PIN = 25;
constexpr unsigned long DEBOUNCE_MS = 100;
constexpr double LITERS_PER_PULSE = 10.0;

// Историческая опорная отметка. Существующий счётчик NVS не обнуляется.
constexpr double INITIAL_TOTAL_LITERS = 0.0;
