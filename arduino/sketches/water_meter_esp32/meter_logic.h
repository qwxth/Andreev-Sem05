#pragma once

#include <stdint.h>

// Подтверждаем оба уровня: LOW для замыкания, HIGH для разрешения нового счёта.
class DebouncedContact {
 public:
  DebouncedContact(bool initiallyClosed, uint32_t now, uint32_t debounceMs)
      : candidate_(initiallyClosed), stable_(initiallyClosed),
        changedAt_(now), debounceMs_(debounceMs) {}

  bool update(bool closed, uint32_t now) {
    if (closed != candidate_) {
      candidate_ = closed;
      changedAt_ = now;
    }
    if (candidate_ == stable_ || uint32_t(now - changedAt_) < debounceMs_) {
      return false;
    }
    stable_ = candidate_;
    return true;
  }

  bool isClosed() const { return stable_; }

 private:
  bool candidate_;
  bool stable_;
  uint32_t changedAt_;
  uint32_t debounceMs_;
};

struct MeterState {
  uint32_t pulses = 0;
  bool closed = false;
};

static_assert(sizeof(uint32_t) == 4, "NVS pulses must contain one uint32_t value");

inline bool validPulseCount(uint32_t pulses) {
  return pulses <= uint32_t(INT32_MAX);
}

// Вызывается для подтверждённого фильтром уровня. Размыкание не добавляет литров.
inline bool recordStableContact(MeterState& state, bool closed) {
  const bool falling = !state.closed && closed;
  state.closed = closed;
  if (falling && state.pulses < uint32_t(INT32_MAX)) {
    ++state.pulses;
    return true;
  }
  return false;
}

inline double meteredLiters(uint32_t pulses, double litersPerPulse,
                            double initialLiters) {
  return initialLiters + pulses * litersPerPulse;
}
