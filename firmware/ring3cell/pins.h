// RING3 work-cell pin map (T-06). Single table: hardware/wiring.md. This file is copied byte-for-byte into
// firmware/bringup/pins.h; `make -C firmware test` checks the two copies are identical.
// Uno usable pins: D2-D13, A0-A5 (18). D0/D1 are the USB serial link and stay free. 15 used, 3 spare.
#pragma once

#define PIN_ESTOP 2    // E-stop push button to GND, INPUT_PULLUP, pressed = LOW. INT0.
#define PIN_ECHO 3     // HC-SR04 ECHO. INT1 (CHANGE). Timer2 PWM pin, PWM unused.
#define PIN_TRIG 4     // HC-SR04 TRIG.
#define PIN_FAN_EN 5   // Fan NPN switch (PN2222) base via 220 ohm, D-64. PWM on Timer0 (OC0B). HIGH = on. 10k base pull-down (D-48).
#define PIN_BUZZER 7   // Active buzzer +, switched with digitalWrite (the tone function is not used).
#define PIN_LCD_RS 8   // LCD1602 pin 4.
#define PIN_SERVO 9    // SG90 signal. Servo library takes Timer1 (D9/D10 lose PWM).
#define PIN_LCD_E 10   // LCD1602 pin 6. Plain digital (Timer1 PWM lost to Servo; not needed).
#define PIN_RGB_R 11   // RGB red via 220 ohm. Plain digital.
#define PIN_RGB_G 12   // RGB green via 220 ohm. Plain digital.
#define PIN_LCD_D4 A0  // LCD1602 pin 11.
#define PIN_LCD_D5 A1  // LCD1602 pin 12.
#define PIN_LCD_D6 A2  // LCD1602 pin 13.
#define PIN_LCD_D7 A3  // LCD1602 pin 14.
#define PIN_RGB_B A4   // RGB blue via 220 ohm. Plain digital.
// Spare: D6 (PWM, Timer0), D13 (on-board LED, blinks during the bootloader), A5.
