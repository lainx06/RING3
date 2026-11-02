// Host tests for firmware/ring3cell/cell_rules.h (T-06, D-49 E-stop latching, D-50 F2 FAN setpoints).
// The E-stop tests run the real rule functions over a simulated D2 timeline at 1 us resolution:
// - INT0: a falling edge sets the pending flag; the ISR runs `isr_delay_us` later (its entry delay: dispatch plus
//   any interrupt it waits behind) and reads D2 then; extra edges while pending don't queue a second run.
// - loop(): calls estop_hold_update() every `poll_us`.
#include <stdio.h>
#include <string.h>

#include "../ring3cell/cell_rules.h"

static int g_pass = 0;
static int g_fail = 0;

static void check(bool ok, const char *name) {
  printf("%s %s\n", ok ? "ok   " : "FAIL ", name);
  (ok ? g_pass : g_fail)++;
}

// ---- D2 timeline: a list of LOW intervals [from_us, to_us) on an otherwise HIGH line ----
struct low_span {
  uint32_t from_us, to_us;
};

struct timeline {
  low_span spans[64];
  int n;
};

static void add_low(timeline *t, uint32_t from_us, uint32_t to_us) { t->spans[t->n++] = {from_us, to_us}; }

static bool is_low(const timeline *t, uint32_t us) {
  for (int i = 0; i < t->n; i++) {
    if (us >= t->spans[i].from_us && us < t->spans[i].to_us) {
      return true;
    }
  }
  return false;
}

// A press with contact bounce: `bounce_us` of alternating 50 us LOW/HIGH, then solid LOW until `release_us`.
static void add_press(timeline *t, uint32_t at_us, uint32_t bounce_us, uint32_t release_us) {
  uint32_t u = at_us;
  for (; u + 100 <= at_us + bounce_us; u += 100) {
    add_low(t, u, u + 50);
  }
  add_low(t, u, release_us);
}

struct sim_result {
  bool latched;
  uint32_t latch_us;
  bool by_isr;
};

static sim_result simulate(const timeline *t, uint32_t end_us, bool isr_enabled, uint32_t isr_delay_us,
                           uint32_t poll_us) {
  sim_result r = {false, 0, false};
  estop_hold hold;
  estop_hold_init(&hold, is_low(t, 0), 0);
  bool prev_low = is_low(t, 0);
  bool pending = false;
  uint32_t pending_at = 0;
  for (uint32_t us = 1; us < end_us && !r.latched; us++) {
    bool low = is_low(t, us);
    if (low && !prev_low && isr_enabled && !pending) {
      pending = true;  // INTF0 set by the falling edge
      pending_at = us;
    }
    prev_low = low;
    if (pending && us >= pending_at + isr_delay_us) {
      pending = false;
      if (estop_isr_latches(low)) {
        r = {true, us, true};
        break;
      }
    }
    if (us % poll_us == 0 && estop_hold_update(&hold, low, us / 1000)) {
      r = {true, us, false};
    }
  }
  return r;
}

static void test_estop() {
  const uint32_t END = 400000;  // 400 ms
  const uint32_t delays[] = {3, 6, 15};
  const uint32_t polls[] = {500, 1000, 3000};  // loop pass times incl. a slow pass with an LCD line write

  // A real press (1 ms of bounce, held 150 ms) latches through the ISR within the first bounce.
  {
    timeline t = {};
    add_press(&t, 10000, 1000, 160000);
    bool all = true;
    for (uint32_t d : delays) {
      for (uint32_t p : polls) {
        sim_result r = simulate(&t, END, true, d, p);
        all = all && r.latched && r.by_isr && r.latch_us <= 10000 + 1000 + d;
      }
    }
    check(all, "estop: real press (bounce 1 ms, held 150 ms) latches via ISR within the bounce");
  }
  // The same press with INT0 never firing: the loop backstop latches after >= 20 ms of LOW.
  {
    timeline t = {};
    add_press(&t, 10000, 1000, 160000);
    bool all = true;
    for (uint32_t p : polls) {
      sim_result r = simulate(&t, END, false, 0, p);
      // Earliest: 20 ms after the press starts (a poll can land in a LOW half of the bounce). Latest: 20 ms after
      // solid LOW, plus one poll, plus 1 ms of millis() granularity.
      all = all && r.latched && !r.by_isr && r.latch_us >= 10000 + 20000 && r.latch_us <= 11000 + 20000 + p + 1000;
    }
    check(all, "estop: real press latches via the loop backstop alone (ISR disabled), >= 20 ms of LOW");
  }
  // A short tap (held 30 ms) still latches without the ISR.
  {
    timeline t = {};
    add_press(&t, 10000, 500, 40500);
    bool all = true;
    for (uint32_t p : polls) {
      all = all && simulate(&t, END, false, 0, p).latched;
    }
    check(all, "estop: 30 ms tap latches via the loop backstop alone");
  }
  // Glitches shorter than the ISR's entry delay: no latch, by either path.
  {
    bool all = true;
    for (uint32_t d : delays) {
      for (uint32_t w = 1; w < d; w++) {
        for (uint32_t p : polls) {
          timeline t = {};
          add_low(&t, 50000, 50000 + w);
          all = all && !simulate(&t, END, true, d, p).latched;
        }
      }
    }
    check(all, "estop: single glitch shorter than the ISR entry delay (1..delay-1 us) does not latch");
  }
  // A burst of 40 one-microsecond spikes (motor-noise-like) over 4 ms: no latch.
  {
    timeline t = {};
    for (uint32_t i = 0; i < 40; i++) {
      add_low(&t, 60000 + i * 100, 60000 + i * 100 + 1);
    }
    bool all = true;
    for (uint32_t d : delays) {
      for (uint32_t p : polls) {
        all = all && !simulate(&t, END, true, d, p).latched;
      }
    }
    check(all, "estop: burst of 40 x 1 us spikes over 4 ms does not latch");
  }
  // The loop backstop never latches on LOW shorter than 20 ms (ISR disabled to isolate it).
  {
    bool all = true;
    const uint32_t widths[] = {500, 5000, 19000};
    for (uint32_t w : widths) {
      for (uint32_t p : polls) {
        timeline t = {};
        add_low(&t, 70000, 70000 + w);
        all = all && !simulate(&t, END, false, 0, p).latched;
      }
    }
    check(all, "estop: loop backstop ignores LOW of 0.5, 5 and 19 ms");
  }
  // Characterisation, not a pass rule: a glitch that is still LOW when the ISR runs latches (D-49 design).
  {
    timeline t = {};
    add_low(&t, 80000, 80500);
    sim_result r = simulate(&t, END, true, 6, 1000);
    printf("info  estop: a 500 us glitch %s via the ISR (D-49 latches on 'still LOW when the ISR runs'; "
           "open item in docs/evidence/T-06/README.md)\n",
           r.latched ? "LATCHES" : "does not latch");
  }
}

static void test_fan_rx() {
  struct row {
    bool estop_l, pressed, presence, link_lost, clear;
    uint8_t duty;
    fan_rx_action want;
    const char *name;
  };
  const row rows[] = {
      {false, false, false, false, true, 60, FAN_RX_APPLY, "fan_rx: no latch, clear reading -> apply"},
      {false, false, false, false, true, 0, FAN_RX_APPLY, "fan_rx: FAN 0, no latch -> apply"},
      {false, false, false, false, false, 0, FAN_RX_APPLY, "fan_rx: FAN 0 without a clear reading -> apply"},
      {false, false, false, false, false, 60, FAN_RX_REFUSE_NOT_CLEAR, "fan_rx: FAN 60 not clear -> refuse"},
      {true, false, false, false, true, 60, FAN_RX_DISCARD, "fan_rx: E-stop latched -> discard"},
      {false, true, false, false, true, 60, FAN_RX_DISCARD, "fan_rx: E-stop pressed, not yet latched -> discard"},
      {false, false, false, true, true, 60, FAN_RX_DISCARD, "fan_rx: first frame after link loss -> discard"},
      {false, false, true, false, false, 60, FAN_RX_DISCARD, "fan_rx: presence fault, not clear -> discard"},
      {false, false, true, false, true, 60, FAN_RX_CLEAR_PRESENCE,
       "fan_rx: presence fault, clear -> clear fault, discard setpoint"},
      {false, false, true, false, true, 0, FAN_RX_CLEAR_PRESENCE, "fan_rx: FAN 0 clears presence fault"},
      {true, false, true, true, true, 60, FAN_RX_DISCARD, "fan_rx: E-stop + presence + link lost -> discard"},
      {false, false, true, true, true, 60, FAN_RX_DISCARD,
       "fan_rx: link lost + presence, clear -> discard (link restore never clears presence and starts)"},
  };
  for (const row &r : rows) {
    check(fan_rx_decide(r.estop_l, r.pressed, r.presence, r.link_lost, r.clear, r.duty) == r.want, r.name);
  }
}

int main() {
  test_estop();
  test_fan_rx();
  printf("%d/%d rule checks passed, %d failed\n", g_pass, g_pass + g_fail, g_fail);
  return g_fail == 0 ? 0 : 1;
}
