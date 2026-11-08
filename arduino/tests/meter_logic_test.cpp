#include "../sketches/water_meter_esp32/meter_logic.h"

#include <cmath>
#include <cstdlib>
#include <initializer_list>
#include <iostream>

struct Sample {
  bool rawClosed;
  uint32_t time;
  uint32_t pulses;
  bool stableClosed;
};

void check(bool condition, const char* message) {
  if (!condition) {
    std::cerr << message << '\n';
    std::exit(EXIT_FAILURE);
  }
}

void checkFiltered(const char* name, bool initiallyClosed, uint32_t start,
                   std::initializer_list<Sample> samples, uint32_t restoredPulses = 0) {
  DebouncedContact contact(initiallyClosed, start, 100);
  MeterState state{restoredPulses, initiallyClosed};
  for (const auto& sample : samples) {
    if (contact.update(sample.rawClosed, sample.time)) {
      recordStableContact(state, contact.isClosed());
    }
    if (state.pulses != sample.pulses || state.closed != sample.stableClosed ||
        contact.isClosed() != sample.stableClosed) {
      std::cerr << name << ": unexpected filtered state at " << sample.time << " ms\n";
      std::exit(EXIT_FAILURE);
    }
  }
}

void checkLiters(double actual, double expected) {
  if (std::abs(actual - expected) > 1e-9) {
    std::cerr << "Expected " << expected << " liters, got " << actual << '\n';
    std::exit(EXIT_FAILURE);
  }
}

int main() {
  // Три сырых HIGH->LOW на одном замыкании дают один отсчёт лишь после 100 мс LOW.
  // Зубцы при размыкании не дают нового отсчёта; HIGH тоже подтверждается 100 мс.
  checkFiltered("closure/release bounce, then a genuine new closure", false, 0, {
      {true, 10, 0, false}, {false, 11, 0, false},
      {true, 12, 0, false}, {false, 13, 0, false}, {true, 14, 0, false},
      {true, 113, 0, false}, {true, 114, 1, true}, {true, 11000, 1, true},
      {false, 11010, 1, true}, {true, 11011, 1, true},
      {false, 11012, 1, true}, {true, 11013, 1, true}, {false, 11014, 1, true},
      {false, 11113, 1, true}, {false, 11114, 1, false},
      {true, 12000, 1, false}, {true, 12099, 1, false}, {true, 12100, 2, true},
  });
  checkFiltered("a 99 ms release cannot rearm the counter", false, 0, {
      {true, 10, 0, false}, {true, 110, 1, true},
      {false, 120, 1, true}, {false, 219, 1, true},
      {true, 220, 1, true}, {true, 1000, 1, true},
  });
  checkFiltered("a 99 ms closure is ignored", false, 0, {
      {true, 10, 0, false}, {false, 109, 0, false}, {false, 1000, 0, false},
  });
  checkFiltered("boot closed, hold, release, then first accepted closure", true, 0, {
      {true, 0, 0, true}, {true, 100, 0, true},
      {false, 110, 0, true}, {false, 210, 0, false}, {false, 1000, 0, false},
      {true, 1010, 0, false}, {true, 1109, 0, false}, {true, 1110, 1, true},
  });
  checkFiltered("boot open and hold do not create a pulse", false, 0, {
      {false, 0, 0, false}, {false, 1000, 0, false}, {false, 10000, 0, false},
  });
  checkFiltered("millis wraparound on both levels", false, UINT32_MAX - 50, {
      {true, UINT32_MAX - 40, 0, false}, {true, 58, 0, false},
      {true, 59, 1, true},
      {false, 100, 1, true}, {false, 199, 1, true}, {false, 200, 1, false},
      {true, 210, 1, false}, {true, 310, 2, true},
  });
  checkFiltered("restored closed contact is not counted twice", true, 0, {
      {true, 100, 3, true}, {false, 110, 3, true}, {false, 210, 3, false},
      {true, 220, 3, false}, {true, 320, 4, true},
  }, 3);

  const double expected[] = {1090, 1100, 1110, 1120, 1130};
  for (uint32_t pulses = 0; pulses < 5; ++pulses) {
    checkLiters(meteredLiters(pulses, 10.0, 1090.0), expected[pulses]);
  }
  checkLiters(meteredLiters(3, 0.1, 1000.0), 1000.3);
  MeterState release{1, true};
  check(!recordStableContact(release, false), "Opening added a pulse");
  checkLiters(meteredLiters(release.pulses, 10.0, 1090.0), 1100.0);
  check(recordStableContact(release, true), "New stable closure was not counted");
  checkLiters(meteredLiters(release.pulses, 10.0, 1090.0), 1110.0);

  check(validPulseCount(0), "Empty/new NVS count should be valid");
  check(validPulseCount(uint32_t(INT32_MAX)), "INTEGER limit should be valid");
  check(!validPulseCount(uint32_t(INT32_MAX) + 1), "Oversized NVS count accepted");
  check(!validPulseCount(UINT32_MAX), "UINT32_MAX NVS count should be invalid");
  MeterState limit{uint32_t(INT32_MAX) - 1, false};
  check(recordStableContact(limit, true), "Last allowed closure was rejected");
  check(limit.pulses == uint32_t(INT32_MAX), "Wrong saturated count");
  check(!recordStableContact(limit, false) && !limit.closed, "Saturated release state wrong");
  check(!recordStableContact(limit, true) && limit.closed, "Saturated closure state wrong");
  check(limit.pulses == uint32_t(INT32_MAX), "Count exceeded INTEGER limit");

  std::cout << "PASS: 7 filtered contact scenarios, 100 ms LOW/HIGH, closure/release bounce, "
               "boot/wrap/restore, +10 and NVS limits\n";
}
