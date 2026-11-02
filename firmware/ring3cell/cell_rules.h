// RING3 work-cell decision rules (T-06, D-49, D-50 F2). Plain C++, no Arduino headers: the firmware calls these
// and firmware/test/test_rules.cpp tests the same code on the host.
#ifndef RING3_CELL_RULES_H
#define RING3_CELL_RULES_H

#include <stdint.h>

#define CELL_ESTOP_HOLD_MS 20u  // loop backstop: D2 LOW this long latches even if the ISR didn't (D-49)

// D-49, INT0 ISR: called on a falling edge of D2 with the level read inside the ISR. A spike that is already
// gone when the ISR runs (shorter than the ISR's entry delay, a few microseconds) doesn't latch; a press does.
static inline bool estop_isr_latches(bool pin_low_now) { return pin_low_now; }

// D-49, loop backstop: continuous-LOW tracker. Call every loop pass with the raw level.
struct estop_hold {
  bool low;
  uint32_t since_ms;  // when the current level started
};

static inline void estop_hold_init(estop_hold *h, bool low, uint32_t now_ms) {
  h->low = low;
  h->since_ms = now_ms;
}

// Returns true when D2 has read LOW on every pass for at least CELL_ESTOP_HOLD_MS.
static inline bool estop_hold_update(estop_hold *h, bool low, uint32_t now_ms) {
  if (low != h->low) {
    h->low = low;
    h->since_ms = now_ms;
  }
  return h->low && (uint32_t)(now_ms - h->since_ms) >= CELL_ESTOP_HOLD_MS;
}

// D-50 F2: what a received FAN setpoint does, given the latches at the moment it arrives.
enum fan_rx_action : uint8_t {
  FAN_RX_APPLY = 0,            // becomes the new setpoint
  FAN_RX_DISCARD,              // a latch is set: the setpoint is dropped, fan_cmd stays 0
  FAN_RX_CLEAR_PRESENCE,       // presence fault latched + valid clear reading: fault cleared, setpoint dropped
  FAN_RX_REFUSE_NOT_CLEAR,     // no latch, duty > 0, but no valid clear reading: refused, presence fault latched
};

// link_was_lost: LINK_LOST was set when this frame arrived (the frame itself restores the link).
static inline fan_rx_action fan_rx_decide(bool estop_latched, bool estop_pressed, bool presence_fault,
                                          bool link_was_lost, bool reading_clear, uint8_t duty) {
  if (estop_latched || estop_pressed || link_was_lost) {
    return FAN_RX_DISCARD;
  }
  if (presence_fault) {
    return reading_clear ? FAN_RX_CLEAR_PRESENCE : FAN_RX_DISCARD;
  }
  if (duty > 0 && !reading_clear) {
    return FAN_RX_REFUSE_NOT_CLEAR;
  }
  return FAN_RX_APPLY;
}

#endif
