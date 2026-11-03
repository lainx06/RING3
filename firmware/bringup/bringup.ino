// RING3 work-cell bring-up checks (T-06). Hand-written for RING3; uses the standard Servo and LiquidCrystal
// libraries. Human-readable output on USB serial, 115200 baud:
//   arduino-cli monitor -p /dev/ttyACM0 -c baudrate=115200
// One key per check, in the wiring order of hardware/wiring.md:
//   e E-stop   s presence sensor   v servo   f fan   l LCD   b buzzer   r RGB   a all (asks what is wired)
// Every pin stays an input or LOW until its check runs. The E-stop (INT0) cuts the fan during the fan check.
// This is a test sketch, not the work-cell firmware: it may block (delay); firmware/ring3cell must not.

#include <LiquidCrystal.h>
#include <Servo.h>

#include "pins.h"

enum Part { P_ESTOP, P_SENSOR, P_SERVO, P_FAN, P_LCD, P_BUZZER, P_RGB, P_COUNT };
enum Result : uint8_t { R_NOT_RUN, R_OK, R_PROBLEM, R_SKIPPED };

static Result results[P_COUNT];
static uint16_t backstop_mm = 0;
static int fan_min_duty = -1;  // -1 = not measured, 0 = never spun

static Servo servo;
// Note: the LiquidCrystal constructor already calls begin(16, 1), so the LCD pins are driven from boot.
static LiquidCrystal lcd(PIN_LCD_RS, PIN_LCD_E, PIN_LCD_D4, PIN_LCD_D5, PIN_LCD_D6, PIN_LCD_D7);

// Boot counter in RAM that survives a reset but not a power cycle: shows a reset during a check (brownout).
static uint16_t boot_magic __attribute__((section(".noinit")));
static uint16_t boot_count __attribute__((section(".noinit")));

// ---- E-stop (INT0) ----
static volatile uint8_t estop_hit = 0;
static volatile uint16_t estop_edges = 0;

static void estop_isr() {
  digitalWrite(PIN_FAN_EN, LOW);  // digitalWrite also disconnects the Timer0 PWM output
  estop_hit = 1;
  estop_edges++;
}

// ---- Echo timing (INT1, CHANGE) ----
enum { ECHO_IDLE, ECHO_ARMED, ECHO_HIGH, ECHO_DONE };
static volatile uint8_t echo_state = ECHO_IDLE;
static volatile uint32_t echo_rise_us = 0;
static volatile uint32_t echo_width_us = 0;

static void echo_isr() {
  uint32_t now = micros();
  bool high = digitalRead(PIN_ECHO) == HIGH;
  if (high && echo_state == ECHO_ARMED) {
    echo_rise_us = now;
    echo_state = ECHO_HIGH;
  } else if (!high && echo_state == ECHO_HIGH) {
    echo_width_us = now - echo_rise_us;
    echo_state = ECHO_DONE;
  }
}

// One reading in mm, or 0xFFFF for no echo (no pulse within 30 ms, or ECHO already high at trigger time).
static uint16_t measure_mm() {
  if (digitalRead(PIN_ECHO) == HIGH) {
    return 0xFFFF;
  }
  noInterrupts();
  echo_state = ECHO_ARMED;
  interrupts();
  digitalWrite(PIN_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);
  uint32_t t0 = millis();
  while (millis() - t0 < 30) {
    if (echo_state == ECHO_DONE) {
      break;
    }
  }
  noInterrupts();
  uint8_t st = echo_state;
  uint32_t w = echo_width_us;
  echo_state = ECHO_IDLE;
  interrupts();
  if (st != ECHO_DONE) {
    return 0xFFFF;
  }
  uint32_t mm = (w * 343UL) / 2000UL;  // sound ~343 m/s = 0.343 mm/us, there and back
  return mm >= 0xFFFF ? 0xFFFE : (uint16_t)mm;
}

// ---- Serial input helpers (arduino-cli monitor may send CR, LF or both) ----
static int read_key() {
  for (;;) {
    if (Serial.available()) {
      int c = Serial.read();
      if (c != '\r' && c != '\n') {
        return c;
      }
    }
  }
}

static void flush_input() {
  delay(20);
  while (Serial.available()) {
    Serial.read();
  }
}

static bool ask_yes(const __FlashStringHelper *q) {
  flush_input();
  Serial.print(q);
  Serial.print(F(" [y/n] "));
  for (;;) {
    int c = read_key();
    if (c == 'y' || c == 'Y') {
      Serial.println(F("y"));
      return true;
    }
    if (c == 'n' || c == 'N') {
      Serial.println(F("n"));
      return false;
    }
  }
}

static int read_number() {
  flush_input();
  int v = 0;
  bool any = false;
  for (;;) {
    if (!Serial.available()) {
      continue;
    }
    int c = Serial.read();
    if (c >= '0' && c <= '9') {
      v = v * 10 + (c - '0');
      if (v > 999) {
        v = 999;
      }
      any = true;
      Serial.write((char)c);
    } else if ((c == '\r' || c == '\n') && any) {
      Serial.println();
      return v;
    }
  }
}

static void wait_key(const __FlashStringHelper *msg) {
  flush_input();
  Serial.print(msg);
  Serial.println(F(" (any key)"));
  read_key();
}

static void say_result(Part p, bool ok) {
  results[p] = ok ? R_OK : R_PROBLEM;
  Serial.println(ok ? F("  -> OK") : F("  -> PROBLEM (see hardware/wiring.md, this part's check line)"));
}

// ---- Checks ----
static void check_estop() {
  Serial.println(F("[1/7] E-STOP  (button on D2 to GND, INT0, pressed = LOW)"));
  pinMode(PIN_ESTOP, INPUT_PULLUP);
  delay(5);
  bool released = digitalRead(PIN_ESTOP) == HIGH;
  Serial.print(F("  untouched: D2="));
  Serial.println(released ? F("HIGH (released)") : F("LOW (reads PRESSED)"));
  if (!released) {
    Serial.println(F("  D2 is LOW without a press: the two wires are on legs that are always connected."));
    Serial.println(F("  Move one wire to the diagonally opposite leg."));
    say_result(P_ESTOP, false);
    return;
  }
  Serial.println(F("  Press and release the button 3 times (15 s)."));
  noInterrupts();
  estop_edges = 0;
  interrupts();
  uint8_t presses = 0;
  bool stable = true;  // debounced state, true = released
  bool last_raw = true;
  uint32_t changed_at = millis();
  uint32_t t0 = millis();
  while (millis() - t0 < 15000UL && presses < 3) {
    bool raw = digitalRead(PIN_ESTOP) == HIGH;
    if (raw != last_raw) {
      last_raw = raw;
      changed_at = millis();
    }
    if (raw != stable && millis() - changed_at >= 20) {
      stable = raw;
      Serial.println(stable ? F("  released (D2=HIGH)") : F("  PRESSED  (D2=LOW)"));
      if (!stable) {
        presses++;
      }
    }
  }
  delay(300);
  noInterrupts();
  uint16_t edges = estop_edges;
  interrupts();
  Serial.print(F("  presses seen: "));
  Serial.print(presses);
  Serial.print(F(", INT0 falling edges: "));
  Serial.println(edges);
  say_result(P_ESTOP, presses >= 3 && edges >= 3 && digitalRead(PIN_ESTOP) == HIGH);
}

static void check_sensor() {
  Serial.println(F("[2/7] PRESENCE SENSOR  (HC-SR04: TRIG D4, ECHO D3 = INT1)"));
  pinMode(PIN_TRIG, OUTPUT);
  digitalWrite(PIN_TRIG, LOW);
  pinMode(PIN_ECHO, INPUT_PULLUP);  // unplugged ECHO reads HIGH = no echo (fail closed)
  wait_key(F("  Point it at the backstop 20-40 cm away, keep hands clear, then press a key."));
  uint16_t lo = 0xFFFF, hi = 0;
  uint8_t none = 0;
  for (uint8_t i = 0; i < 15; i++) {
    uint16_t mm = measure_mm();
    Serial.print(F("  "));
    if (mm == 0xFFFF) {
      Serial.println(F("NO ECHO"));
      none++;
    } else {
      Serial.print(mm);
      Serial.println(F(" mm"));
      lo = mm < lo ? mm : lo;
      hi = mm > hi ? mm : hi;
    }
    delay(200);
  }
  bool backstop_ok = none == 0 && lo >= 200 && hi <= 400 && hi - lo <= 30;
  if (none == 0) {
    backstop_mm = (uint16_t)((lo + hi) / 2);
  }
  Serial.print(F("  backstop: "));
  if (none == 0) {
    Serial.print(lo);
    Serial.print(F("-"));
    Serial.print(hi);
    Serial.println(F(" mm (want 200-400, spread <= 30)"));
  } else {
    Serial.print(none);
    Serial.println(F(" of 15 readings had no echo"));
  }
  wait_key(F("  Now hold a hand about 5 cm in front of the sensor and keep it there, then press a key."));
  uint8_t close = 0;
  for (uint8_t i = 0; i < 10; i++) {
    uint16_t mm = measure_mm();
    Serial.print(F("  "));
    if (mm == 0xFFFF) {
      Serial.println(F("NO ECHO"));
    } else {
      Serial.print(mm);
      Serial.println(F(" mm"));
      if (mm < 100) {
        close++;
      }
    }
    delay(200);
  }
  Serial.print(F("  hand readings < 100 mm: "));
  Serial.print(close);
  Serial.println(F(" of 10 (want >= 8)"));
  say_result(P_SENSOR, backstop_ok && close >= 8);
}

static void check_servo() {
  Serial.println(F("[3/7] SERVO  (SG90 signal D9, power from the MOTOR rail)"));
  Serial.println(F("  If this banner or 'RING3 BRINGUP' reprints during the sweep, the Uno reset: power problem."));
  servo.write(90);
  servo.attach(PIN_SERVO);
  delay(600);
  Serial.println(F("  -> 60"));
  servo.write(60);
  delay(800);
  Serial.println(F("  -> 120"));
  servo.write(120);
  delay(1000);
  Serial.println(F("  -> 90"));
  servo.write(90);
  delay(800);
  servo.detach();
  digitalWrite(PIN_SERVO, LOW);
  say_result(P_SERVO, ask_yes(F("  Did the horn turn to 60, then 120, then back to the middle (90)?")));
}

static void fan_set(uint8_t duty) {
  uint8_t v = (uint8_t)(((uint16_t)duty * 255U) / 100U);
  noInterrupts();
  if (!estop_hit && digitalRead(PIN_ESTOP) == HIGH) {
    analogWrite(PIN_FAN_EN, v);
  } else {
    digitalWrite(PIN_FAN_EN, LOW);
  }
  interrupts();
}

static void check_fan() {
  Serial.println(F("[4/7] FAN  (NPN switch: D5 -> 220 ohm -> PN2222 base, motor on the MOTOR rail)"));
  pinMode(PIN_ESTOP, INPUT_PULLUP);
  if (digitalRead(PIN_ESTOP) == LOW) {
    Serial.println(F("  E-stop reads PRESSED: release it first."));
    say_result(P_FAN, false);
    return;
  }
  noInterrupts();
  estop_hit = 0;
  interrupts();
  Serial.println(F("  Ramp 30..100 % in 10 % steps, 1 s each. Press the E-stop to cut it at any time."));
  Serial.println(F("  Watch for the step where the blade first starts turning."));
  wait_key(F("  Keep fingers away from the blade, then press a key to start."));
  bool cut = false;
  uint8_t cut_at = 0;
  for (uint8_t d = 30; d <= 100 && !cut; d += 10) {
    Serial.print(F("  duty "));
    Serial.print(d);
    Serial.println(F(" %"));
    fan_set(d);
    uint32_t t0 = millis();
    while (millis() - t0 < 1000) {
      if (estop_hit) {
        cut = true;
        cut_at = d;
        break;
      }
    }
  }
  digitalWrite(PIN_FAN_EN, LOW);
  if (cut) {
    Serial.print(F("  E-STOP: fan cut by INT0 at duty "));
    Serial.print(cut_at);
    Serial.println(F(" %"));
  }
  Serial.println(F("  fan off"));
  Serial.print(F("  At which duty did it first spin? Type 30-100, or 0 if it never spun, then Enter: "));
  fan_min_duty = read_number();
  bool spun = fan_min_duty >= 30 && fan_min_duty <= 100;
  Serial.print(F("  minimum spinning duty: "));
  if (spun) {
    Serial.print(fan_min_duty);
    Serial.println(F(" %"));
  } else {
    Serial.println(F("never spun"));
  }
  bool stopped = ask_yes(F("  Is the fan stopped now?"));
  say_result(P_FAN, spun && stopped);
}

static void check_lcd() {
  Serial.println(F("[5/7] LCD1602  (RS D8, E D10, D4-D7 on A0-A3, RW to GND)"));
  lcd.begin(16, 2);
  lcd.clear();
  lcd.print(F("RING3 BRINGUP"));
  lcd.setCursor(0, 1);
  lcd.print(F("line 2 012345678"));
  Serial.println(F("  Turn the contrast knob slowly until the text is sharp (blocks = too much, blank = too little)."));
  say_result(P_LCD, ask_yes(F("  Does line 1 read 'RING3 BRINGUP' and line 2 'line 2 012345678'?")));
}

static void check_buzzer() {
  Serial.println(F("[6/7] BUZZER  (active buzzer + on D7, - to GND)"));
  pinMode(PIN_BUZZER, OUTPUT);
  for (uint8_t i = 0; i < 2; i++) {
    digitalWrite(PIN_BUZZER, HIGH);
    delay(250);
    digitalWrite(PIN_BUZZER, LOW);
    delay(250);
  }
  say_result(P_BUZZER, ask_yes(F("  Did you hear two short beeps (not just clicks)?")));
}

static void check_rgb() {
  Serial.println(F("[7/7] RGB LED  (R D11, G D12, B A4, each via 220 ohm; long leg to GND)"));
  const uint8_t pins[3] = {PIN_RGB_R, PIN_RGB_G, PIN_RGB_B};
  for (uint8_t i = 0; i < 3; i++) {
    pinMode(pins[i], OUTPUT);
    digitalWrite(pins[i], LOW);
  }
  for (uint8_t i = 0; i < 3; i++) {
    Serial.println(i == 0 ? F("  red") : (i == 1 ? F("  green") : F("  blue")));
    digitalWrite(pins[i], HIGH);
    delay(800);
    digitalWrite(pins[i], LOW);
  }
  say_result(P_RGB, ask_yes(F("  Did it show red, then green, then blue, then go dark?")));
}

typedef void (*CheckFn)();
static const CheckFn checks[P_COUNT] = {check_estop, check_sensor, check_servo, check_fan,
                                        check_lcd,   check_buzzer, check_rgb};

static const __FlashStringHelper *part_name(uint8_t p) {
  switch (p) {
    case P_ESTOP: return F("E-stop ");
    case P_SENSOR: return F("Sensor ");
    case P_SERVO: return F("Servo  ");
    case P_FAN: return F("Fan    ");
    case P_LCD: return F("LCD    ");
    case P_BUZZER: return F("Buzzer ");
    default: return F("RGB    ");
  }
}

static void summary() {
  Serial.print(F("SUMMARY (boot #"));
  Serial.print(boot_count);
  Serial.println(F(")"));
  for (uint8_t p = 0; p < P_COUNT; p++) {
    Serial.print(F("  "));
    Serial.print(part_name(p));
    switch (results[p]) {
      case R_OK: Serial.print(F("OK")); break;
      case R_PROBLEM: Serial.print(F("PROBLEM")); break;
      case R_SKIPPED: Serial.print(F("SKIPPED (not wired yet)")); break;
      default: Serial.print(F("not run")); break;
    }
    if (p == P_SENSOR && backstop_mm) {
      Serial.print(F("  backstop "));
      Serial.print(backstop_mm);
      Serial.print(F(" mm"));
    }
    if (p == P_FAN && fan_min_duty >= 0) {
      Serial.print(F("  min spinning duty "));
      if (fan_min_duty) {
        Serial.print(fan_min_duty);
        Serial.print(F(" %"));
      } else {
        Serial.print(F("none"));
      }
    }
    Serial.println();
  }
}

static void run_all() {
  for (uint8_t p = 0; p < P_COUNT; p++) {
    Serial.print(F("Is the "));
    Serial.print(part_name(p));
    if (ask_yes(F("wired?"))) {
      checks[p]();
    } else {
      results[p] = R_SKIPPED;
      Serial.print(F("  SKIPPED: "));
      Serial.print(part_name(p));
      Serial.println(F("not wired yet"));
    }
  }
  summary();
}

static void menu() {
  Serial.println(F("Keys: e E-stop | s sensor | v servo | f fan | l LCD | b buzzer | r RGB | a all | ? summary"));
}

void setup() {
  // Everything idle first: fan enable LOW, other outputs LOW, sensor and button as inputs.
  pinMode(PIN_FAN_EN, OUTPUT);
  digitalWrite(PIN_FAN_EN, LOW);
  pinMode(PIN_BUZZER, OUTPUT);
  digitalWrite(PIN_BUZZER, LOW);
  pinMode(PIN_TRIG, OUTPUT);
  digitalWrite(PIN_TRIG, LOW);
  pinMode(PIN_ECHO, INPUT_PULLUP);
  pinMode(PIN_ESTOP, INPUT_PULLUP);
  pinMode(PIN_RGB_R, OUTPUT);
  pinMode(PIN_RGB_G, OUTPUT);
  pinMode(PIN_RGB_B, OUTPUT);
  digitalWrite(PIN_RGB_R, LOW);
  digitalWrite(PIN_RGB_G, LOW);
  digitalWrite(PIN_RGB_B, LOW);
  pinMode(PIN_SERVO, OUTPUT);
  digitalWrite(PIN_SERVO, LOW);

  if (boot_magic != 0xB007) {
    boot_magic = 0xB007;
    boot_count = 0;
  }
  boot_count++;

  attachInterrupt(digitalPinToInterrupt(PIN_ESTOP), estop_isr, FALLING);
  attachInterrupt(digitalPinToInterrupt(PIN_ECHO), echo_isr, CHANGE);

  Serial.begin(115200);
  Serial.println();
  Serial.print(F("RING3 BRINGUP  boot #"));
  Serial.print(boot_count);
  Serial.println(F(" since power-on (a jump during a check = the Uno reset)"));
  menu();
}

void loop() {
  if (!Serial.available()) {
    return;
  }
  int c = Serial.read();
  switch (c) {
    case 'e': check_estop(); break;
    case 's': check_sensor(); break;
    case 'v': check_servo(); break;
    case 'f': check_fan(); break;
    case 'l': check_lcd(); break;
    case 'b': check_buzzer(); break;
    case 'r': check_rgb(); break;
    case 'a': run_all(); break;
    case '?': summary(); break;
    case '\r':
    case '\n': return;
    default: break;
  }
  menu();
}
