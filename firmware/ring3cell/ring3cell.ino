// RING3 work-cell firmware (T-06): the second safety layer under the kernel (INV-13). Hand-written for RING3,
// trusted, flashed by the user only. Uses the standard Servo and LiquidCrystal libraries.
// Link: COM3 frames over USB serial, hardware/protocol.md + protocol.h. Pins: pins.h / hardware/wiring.md.
//
// Rules this file keeps (docs/tasks/T-06.md, ARCHITECTURE §3a, D-43):
// - Boots into the safe state: fan off, servo 90, LCD "SAFE - waiting for kernel".
// - Own clamps (servo 15-165, fan <= 80 %), reported with the CLAMP flag; bad frames rejected and flagged.
// - E-stop on INT0: the ISR latches and cuts the fan with digitalWrite (which also stops the Timer0 PWM); every
//   fan write happens with interrupts off and only while unlatched; every loop pass re-forces the fan off while
//   latched or pressed. Only ESTOP_RESET with the button released for 50 ms clears the latch.
// - Presence cutoff: < 100 mm, or 3 no-echo readings in a row, while the fan runs -> fan off in the same pass,
//   latched until the next accepted FAN with a valid clear reading. Echo timed on INT1 with micros().
// - Hardware watchdog (reset cause captured in .init3), link watchdog (1 s -> safe state + alarm).
// - Non-blocking loop: no delay, no pulse timing calls, no tone, no LCD clear/home outside setup(). LCD lines
//   are rewritten only when they change, as 16 padded characters after setCursor, one line per loop pass.

#include <LiquidCrystal.h>
#include <Servo.h>
#include <avr/io.h>
#include <avr/pgmspace.h>
#include <avr/wdt.h>

#include "frame.h"
#include "pins.h"
#include "protocol.h"

// ---------------------------------------------------------------------------------------------------------
// Reset cause, captured before the C runtime and before any constructor runs (.init3).
// MCUSR is read first. The Uno's optiboot clears MCUSR itself; newer optiboot versions pass the original value
// in r2, older ones (4.4, the version in the arduino:avr core) don't, so r2 is used only when MCUSR reads 0 and
// r2 looks like an MCUSR value (bits 0-3 only). [UNVERIFIED] on this board: see docs/evidence/T-06/README.md.
uint8_t reset_mcusr __attribute__((section(".noinit")));
uint8_t reset_r2 __attribute__((section(".noinit")));

void early_reset_hook(void) __attribute__((naked, used, section(".init3")));
void early_reset_hook(void) {
  __asm__ __volatile__("sts reset_r2, r2" ::: "memory");
  reset_mcusr = MCUSR;
  MCUSR = 0;  // WDRF must be cleared before the watchdog can be turned off
  wdt_disable();
}

static bool wdt_reset_seen;

// ---------------------------------------------------------------------------------------------------------
static Servo servo;
static LiquidCrystal lcd(PIN_LCD_RS, PIN_LCD_E, PIN_LCD_D4, PIN_LCD_D5, PIN_LCD_D6, PIN_LCD_D7);

// ---- E-stop (INT0) ----
static volatile uint8_t estop_latched;  // set only by the ISR, cleared only by ESTOP_RESET
static volatile uint8_t fan_applied;    // duty actually on the pin, %
static bool estop_seen;                 // loop's copy, to act once on a new latch
static bool estop_pressed;              // debounced, for STATE.estop
static bool estop_raw_last = false;
static uint32_t estop_raw_since;        // millis() of the last raw change

static void estop_isr() {
  digitalWrite(PIN_FAN_EN, LOW);  // also disconnects the PWM output from the pin
  fan_applied = 0;
  estop_latched = 1;
}

static inline bool estop_raw_pressed() { return digitalRead(PIN_ESTOP) == LOW; }

// ---- Presence sensor (INT1 echo timing, non-blocking) ----
enum { ECHO_IDLE, ECHO_ARMED, ECHO_HIGH, ECHO_DONE };
static volatile uint8_t echo_state = ECHO_IDLE;
static volatile uint32_t echo_rise_us;
static volatile uint32_t echo_width_us;
static uint32_t echo_trig_ms;
static uint32_t next_meas_ms;
static uint16_t distance_mm = CELL_DIST_NO_ECHO;
static uint32_t distance_ms;       // millis() of the latest reading
static uint8_t no_echo_count;

static const uint32_t MEAS_PERIOD_MS = 50;
static const uint32_t READING_MAX_AGE_MS = 200;

static void echo_isr() {
  uint32_t now = micros();
  bool high = (PIND & _BV(PD3)) != 0;  // D3 = PD3
  if (high && echo_state == ECHO_ARMED) {
    echo_rise_us = now;
    echo_state = ECHO_HIGH;
  } else if (!high && echo_state == ECHO_HIGH) {
    echo_width_us = now - echo_rise_us;
    echo_state = ECHO_DONE;
  }
}

// ---- Cell state ----
static uint8_t fan_cmd;           // clamped duty the kernel asked for; 0 after any cut
static uint8_t servo_cmd = CELL_SERVO_PARK;
static bool presence_fault;
static bool presence_by_no_echo;  // why the fault latched (LCD text)
static bool clamp_flag;
static bool bad_frame_flag;
static bool link_lost;
static bool link_ever;            // an accepted kernel frame since boot
static uint32_t last_rx_ms;
static uint16_t ack_seq;
static uint16_t tx_seq;
static uint32_t next_state_ms;
static uint8_t alarm_pattern;     // CELL_ALARM_*

// ---- LCD: wanted text vs what is on the glass ----
static char lcd_want[CELL_LCD_LINES][CELL_LCD_COLS];
static char lcd_shown[CELL_LCD_LINES][CELL_LCD_COLS];
static char kernel_line0[CELL_LCD_COLS];
static bool kernel_line0_set;

static void line_fill(char *dst, const char *src, uint8_t n) {
  uint8_t i = 0;
  for (; i < n && i < CELL_LCD_COLS; i++) {
    dst[i] = src[i];
  }
  for (; i < CELL_LCD_COLS; i++) {
    dst[i] = ' ';
  }
}

static void line_fill_F(char *dst, const __FlashStringHelper *s) {
  PGM_P p = reinterpret_cast<PGM_P>(s);
  uint8_t i = 0;
  for (; i < CELL_LCD_COLS; i++) {
    char c = (char)pgm_read_byte(p + i);
    if (c == 0) {
      break;
    }
    dst[i] = c;
  }
  for (; i < CELL_LCD_COLS; i++) {
    dst[i] = ' ';
  }
}

// Appends an unsigned number at dst[*pos], bounded by the line width.
static void line_put_u(char *dst, uint8_t *pos, uint16_t v) {
  char tmp[5];
  uint8_t n = 0;
  do {
    tmp[n++] = (char)('0' + v % 10);
    v /= 10;
  } while (v && n < sizeof tmp);
  while (n && *pos < CELL_LCD_COLS) {
    dst[(*pos)++] = tmp[--n];
  }
}

static void line_put_F(char *dst, uint8_t *pos, const __FlashStringHelper *s) {
  PGM_P p = reinterpret_cast<PGM_P>(s);
  for (char c; (c = (char)pgm_read_byte(p)) != 0 && *pos < CELL_LCD_COLS; p++) {
    dst[(*pos)++] = c;
  }
}

static void set_last_command(const __FlashStringHelper *name, uint16_t value, bool clamped) {
  char *d = lcd_want[1];
  line_fill(d, "", 0);
  uint8_t pos = 0;
  line_put_F(d, &pos, F("> "));
  line_put_F(d, &pos, name);
  line_put_u(d, &pos, value);
  if (clamped) {
    line_put_F(d, &pos, F(" clamp"));
  }
}

// Line 0: a local condition always wins over the kernel's text.
static void update_line0() {
  char *d = lcd_want[0];
  if (estop_latched) {
    line_fill_F(d, F("E-STOP LATCHED"));
  } else if (presence_fault) {
    line_fill_F(d, presence_by_no_echo ? F("STOP: NO ECHO") : F("STOP: PRESENCE"));
  } else if (link_lost && link_ever) {
    line_fill_F(d, F("LINK LOST - STOP"));
  } else if (!link_ever) {
    line_fill_F(d, F("SAFE - waiting"));
  } else if (kernel_line0_set) {
    line_fill(d, kernel_line0, CELL_LCD_COLS);
  } else {
    line_fill(d, "", 0);
    uint8_t pos = 0;
    line_put_F(d, &pos, F("LINK F"));
    line_put_u(d, &pos, fan_applied);
    line_put_F(d, &pos, F(" S"));
    line_put_u(d, &pos, servo_cmd);
  }
}

static void lcd_service() {
  for (uint8_t l = 0; l < CELL_LCD_LINES; l++) {
    bool same = true;
    for (uint8_t i = 0; i < CELL_LCD_COLS; i++) {
      if (lcd_want[l][i] != lcd_shown[l][i]) {
        same = false;
        break;
      }
    }
    if (same) {
      continue;
    }
    lcd.setCursor(0, l);
    for (uint8_t i = 0; i < CELL_LCD_COLS; i++) {
      lcd.write((uint8_t)lcd_want[l][i]);
      lcd_shown[l][i] = lcd_want[l][i];
    }
    return;  // at most one line per loop pass
  }
}

// ---- Buzzer + RGB ----
static uint8_t beep_phases;      // remaining on/off half-periods of a one-shot burst
static uint32_t beep_phase_ms;
static uint16_t beep_len_ms = 100;

static void start_beeps(uint8_t n, uint16_t len_ms) {
  beep_phases = (uint8_t)(n * 2);
  beep_len_ms = len_ms;
  beep_phase_ms = millis();
  digitalWrite(PIN_BUZZER, HIGH);
}

static void alarm_service(uint32_t now) {
  bool buzz;
  if (beep_phases) {
    if (now - beep_phase_ms >= beep_len_ms) {
      beep_phase_ms = now;
      beep_phases--;
    }
    buzz = beep_phases && (beep_phases & 1) == 0;
  } else {
    buzz = alarm_pattern == CELL_ALARM_REPEAT && (now % 1000) < 100;
  }
  digitalWrite(PIN_BUZZER, buzz ? HIGH : LOW);

  bool blink = (now % 500) < 250;
  bool r = false, g = false, b = false;
  if (estop_latched) {
    r = true;                               // red steady: E-stop
  } else if (presence_fault || (link_lost && link_ever) || alarm_pattern == CELL_ALARM_REPEAT) {
    r = blink;                              // red blinking: fault
  } else if (!link_ever) {
    b = true;                               // blue: waiting for the kernel
  } else if (fan_applied) {
    r = true;                               // yellow (red + green): fan running
    g = true;
  } else {
    g = true;                               // green: linked, idle
  }
  digitalWrite(PIN_RGB_R, r ? HIGH : LOW);
  digitalWrite(PIN_RGB_G, g ? HIGH : LOW);
  digitalWrite(PIN_RGB_B, b ? HIGH : LOW);
}

// ---- Fan ----
static bool reading_clear(uint32_t now) {
  return distance_mm != CELL_DIST_NO_ECHO && distance_mm >= CELL_PRESENCE_MM &&
         now - distance_ms <= READING_MAX_AGE_MS;
}

// The only place the fan pin gets a PWM value. Interrupts are off, so the E-stop ISR can't run between the
// latch check and the write; if the button goes down during this block, the ISR runs right after and cuts it.
static void fan_update() {
  uint8_t want = fan_cmd;
  noInterrupts();
  bool blocked = estop_latched || estop_raw_pressed() || presence_fault || link_lost;
  if (want > 0 && !blocked) {
    analogWrite(PIN_FAN_EN, (uint8_t)(((uint16_t)want * 255U) / 100U));
    fan_applied = want;
  } else {
    digitalWrite(PIN_FAN_EN, LOW);
    fan_applied = 0;
  }
  interrupts();
}

static void fan_cut() {
  fan_cmd = 0;
  fan_update();
}

// ---- Safe state (boot and link watchdog) ----
static void enter_safe_state() {
  fan_cut();
  servo_cmd = CELL_SERVO_PARK;
  servo.write(servo_cmd);
}

// ---- Presence measurement ----
static void presence_reading(uint16_t mm, uint32_t now) {
  distance_mm = mm;
  distance_ms = now;
  if (mm == CELL_DIST_NO_ECHO) {
    if (no_echo_count < 255) {
      no_echo_count++;
    }
  } else {
    no_echo_count = 0;
  }
  if (fan_applied > 0 || fan_cmd > 0) {
    bool too_close = mm != CELL_DIST_NO_ECHO && mm < CELL_PRESENCE_MM;
    bool lost = no_echo_count >= CELL_NO_ECHO_LIMIT;
    if (too_close || lost) {
      presence_fault = true;
      presence_by_no_echo = !too_close;
      fan_cut();
      start_beeps(2, 100);
    }
  }
}

static void presence_service(uint32_t now) {
  uint8_t st;
  uint32_t w;
  noInterrupts();
  st = echo_state;
  w = echo_width_us;
  interrupts();

  if (st == ECHO_DONE) {
    noInterrupts();
    echo_state = ECHO_IDLE;
    interrupts();
    uint32_t mm = (w * 343UL) / 2000UL;  // ~0.343 mm/us, there and back
    presence_reading(mm >= CELL_DIST_NO_ECHO ? (uint16_t)(CELL_DIST_NO_ECHO - 1) : (uint16_t)mm, now);
  } else if ((st == ECHO_ARMED || st == ECHO_HIGH) && now - echo_trig_ms >= CELL_ECHO_TIMEOUT_MS) {
    noInterrupts();
    echo_state = ECHO_IDLE;
    interrupts();
    presence_reading(CELL_DIST_NO_ECHO, now);
  }

  if (echo_state == ECHO_IDLE && (int32_t)(now - next_meas_ms) >= 0) {
    next_meas_ms = now + MEAS_PERIOD_MS;
    if (PIND & _BV(PD3)) {
      // ECHO already high before the trigger: unplugged (pull-up) or stuck. Counts as no echo.
      presence_reading(CELL_DIST_NO_ECHO, now);
      return;
    }
    noInterrupts();
    echo_state = ECHO_ARMED;
    interrupts();
    echo_trig_ms = now;
    digitalWrite(PIN_TRIG, HIGH);
    delayMicroseconds(10);  // the HC-SR04 trigger pulse; 10 us, not a loop delay
    digitalWrite(PIN_TRIG, LOW);
  }
}

// ---- E-stop service (loop side) ----
static void estop_service(uint32_t now) {
  bool raw = estop_raw_pressed();
  if (raw != estop_raw_last) {
    estop_raw_last = raw;
    estop_raw_since = now;
  }
  if (raw != estop_pressed && now - estop_raw_since >= 20) {
    estop_pressed = raw;
  }
  // Belt and braces: every pass, the fan is off while latched or pressed, whatever happened before.
  if (estop_latched || raw) {
    noInterrupts();
    digitalWrite(PIN_FAN_EN, LOW);
    fan_applied = 0;
    interrupts();
  }
  if (estop_latched && !estop_seen) {
    estop_seen = true;
    fan_cmd = 0;
    line_fill_F(lcd_want[1], F("reset at console"));
    start_beeps(1, 400);
  }
}

static bool estop_released_long_enough(uint32_t now) {
  return !estop_raw_last && !estop_raw_pressed() && now - estop_raw_since >= CELL_ESTOP_RELEASE_MS;
}

// ---- Link: receive ----
static uint8_t rx_buf[CELL_MAX_ENCODED];
static uint8_t rx_len;
static bool rx_overflow;
static uint8_t scratch[CELL_MAX_DECODED];

static void handle_frame(const frame_view &f, uint32_t now) {
  last_rx_ms = now;
  if (!link_ever) {
    line_fill_F(lcd_want[1], F("kernel linked"));
  }
  link_ever = true;
  link_lost = false;
  ack_seq = f.seq;
  switch (f.type) {
    case CELL_T_PING:
      break;
    case CELL_T_FAN: {
      bool c;
      uint8_t d = clamp_fan(f.body[0], &c);
      clamp_flag = c;
      if (reading_clear(now)) {
        presence_fault = false;
      } else if (d > 0) {
        presence_fault = true;  // refuse to start without a valid clear reading
        presence_by_no_echo = distance_mm == CELL_DIST_NO_ECHO;
      }
      fan_cmd = d;
      fan_update();
      set_last_command(F("FAN "), d, c);
      break;
    }
    case CELL_T_SERVO: {
      bool c;
      uint8_t d = clamp_servo(f.body[0], &c);
      clamp_flag = c;
      // Second layer of the interlock: no servo move while the E-stop is latched or pressed, or without a valid
      // clear reading. The servo holds where it is.
      if (!estop_latched && !estop_raw_pressed() && reading_clear(now)) {
        servo_cmd = d;
        servo.write(servo_cmd);
        set_last_command(F("SERVO "), d, c);
      } else {
        set_last_command(F("HOLD SERVO "), d, c);
      }
      break;
    }
    case CELL_T_LCD: {
      uint8_t line = f.body[0];
      const char *text = reinterpret_cast<const char *>(f.body + 1);
      uint8_t n = (uint8_t)(f.len - 1);
      if (line == 0) {
        line_fill(kernel_line0, text, n);
        kernel_line0_set = true;
      } else {
        line_fill(lcd_want[1], text, n);
      }
      break;
    }
    case CELL_T_ALARM:
      alarm_pattern = f.body[0];
      if (alarm_pattern == CELL_ALARM_BEEP) {
        start_beeps(1, 100);
        alarm_pattern = CELL_ALARM_OFF;
      }
      break;
    case CELL_T_ESTOP_RESET:
      if (estop_latched && estop_released_long_enough(now)) {
        noInterrupts();
        estop_latched = 0;
        interrupts();
        estop_seen = false;
        alarm_pattern = CELL_ALARM_OFF;
        line_fill_F(lcd_want[1], F("> ESTOP_RESET"));
      }
      break;
    default:
      break;  // frame_parse already rejected anything else
  }
}

static void rx_service(uint32_t now) {
  int avail = Serial.available();
  while (avail-- > 0) {
    uint8_t c = (uint8_t)Serial.read();
    if (c != 0x00) {
      if (rx_len < sizeof rx_buf) {
        rx_buf[rx_len++] = c;
      } else {
        rx_overflow = true;  // too long for any valid frame: drop it whole at the delimiter
      }
      continue;
    }
    if (rx_overflow) {
      bad_frame_flag = true;
    } else if (rx_len > 0) {
      frame_view f;
      frame_err e = frame_parse(rx_buf, rx_len, scratch, sizeof scratch, CELL_MAX_BODY, DIR_TO_DEVICE, &f);
      if (e == FE_OK) {
        handle_frame(f, now);
      } else {
        bad_frame_flag = true;
      }
    }
    rx_len = 0;
    rx_overflow = false;
  }
}

// ---- Link: watchdog + STATE ----
static void link_service(uint32_t now) {
  if (!link_lost && now - last_rx_ms > CELL_LINK_TIMEOUT_MS) {
    link_lost = true;
    enter_safe_state();
    if (link_ever) {
      start_beeps(3, 100);
      line_fill_F(lcd_want[1], F("fan 0, servo 90"));
    }
  }
}

static void send_state(uint32_t now) {
  uint8_t body[CELL_STATE_LEN];
  uint8_t flags = 0;
  if (wdt_reset_seen) flags |= CELL_F_WDT_RESET;
  if (clamp_flag) flags |= CELL_F_CLAMP;
  if (bad_frame_flag) flags |= CELL_F_BAD_FRAME;
  if (estop_latched) flags |= CELL_F_ESTOP_LATCHED;
  if (presence_fault) flags |= CELL_F_PRESENCE_FAULT;
  if (link_lost) flags |= CELL_F_LINK_LOST;
  put_u32le(body + CELL_ST_T_MS, now);
  body[CELL_ST_FAN] = fan_applied;
  body[CELL_ST_SERVO] = servo_cmd;
  put_u16le(body + CELL_ST_DIST, distance_mm);
  body[CELL_ST_ESTOP] = estop_pressed ? 1 : 0;
  body[CELL_ST_FLAGS] = flags;
  put_u16le(body + CELL_ST_ACK_SEQ, ack_seq);

  uint8_t dec[CELL_MIN_DECODED + CELL_STATE_LEN];
  uint8_t wire[CELL_MAX_WIRE];
  size_t n = frame_build(CELL_T_STATE, tx_seq, body, CELL_STATE_LEN, dec, sizeof dec, wire, sizeof wire);
  if (n > 0 && (size_t)Serial.availableForWrite() >= n) {
    Serial.write(wire, n);
    tx_seq++;
    bad_frame_flag = false;  // reported in this STATE
  }
}

// ---------------------------------------------------------------------------------------------------------
void setup() {
  // Fan off before anything else can happen.
  pinMode(PIN_FAN_EN, OUTPUT);
  digitalWrite(PIN_FAN_EN, LOW);
  pinMode(PIN_BUZZER, OUTPUT);
  digitalWrite(PIN_BUZZER, LOW);
  pinMode(PIN_RGB_R, OUTPUT);
  pinMode(PIN_RGB_G, OUTPUT);
  pinMode(PIN_RGB_B, OUTPUT);
  pinMode(PIN_TRIG, OUTPUT);
  digitalWrite(PIN_TRIG, LOW);
  pinMode(PIN_ECHO, INPUT_PULLUP);  // an unplugged ECHO wire reads HIGH = no echo (fail closed)
  pinMode(PIN_ESTOP, INPUT_PULLUP);

  uint8_t cause = reset_mcusr;
  if (cause == 0 && reset_r2 != 0 && (reset_r2 & 0xF0) == 0) {
    cause = reset_r2;
  }
  wdt_reset_seen = (cause & _BV(WDRF)) != 0;

  // A button already held at power-up counts as a press.
  estop_raw_last = estop_raw_pressed();
  estop_raw_since = millis();
  estop_pressed = estop_raw_last;
  if (estop_raw_last) {
    estop_latched = 1;
  }
  attachInterrupt(digitalPinToInterrupt(PIN_ESTOP), estop_isr, FALLING);
  attachInterrupt(digitalPinToInterrupt(PIN_ECHO), echo_isr, CHANGE);

  servo.write(CELL_SERVO_PARK);
  servo.attach(PIN_SERVO);

  lcd.begin(CELL_LCD_COLS, CELL_LCD_LINES);
  lcd.clear();
  for (uint8_t l = 0; l < CELL_LCD_LINES; l++) {
    for (uint8_t i = 0; i < CELL_LCD_COLS; i++) {
      lcd_shown[l][i] = ' ';
    }
  }
  line_fill_F(lcd_want[0], F("SAFE - waiting"));
  line_fill_F(lcd_want[1], F("for kernel"));

  Serial.begin(CELL_BAUD);

  uint32_t now = millis();
  last_rx_ms = now;
  next_state_ms = now;
  next_meas_ms = now;
  enter_safe_state();

  wdt_enable(WDTO_250MS);
}

void loop() {
  wdt_reset();
  uint32_t now = millis();

  estop_service(now);
  rx_service(now);
  presence_service(now);
  link_service(now);
  fan_update();  // re-applies the current decision; a no-op change unless something blocked the fan
  alarm_service(now);

  if ((int32_t)(now - next_state_ms) >= 0) {
    send_state(now);
    next_state_ms += CELL_STATE_PERIOD_MS;
    if ((int32_t)(now - next_state_ms) >= 0) {
      next_state_ms = now + CELL_STATE_PERIOD_MS;  // fell behind: resync, don't burst
    }
  }

  update_line0();
  lcd_service();
}
