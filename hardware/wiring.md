# RING3 work cell: wiring guide (T-06, v2 build)

This guide matches the **v2 build** (D-63, D-64, D-64b, D-65): two breadboards with pre-cut wires, the power module in the
**left** end of the main board, and the fan switched by an **NPN transistor** instead of the L293D. The hole-by-hole
layout is `hardware/WIRING_V2_LAYOUT.md` (the lead's table; the wire IDs below, such as `E2` or `F3`, refer to it).

Parts are from the ELEGOO Super Starter Kit, plus the breadboard power module fed by a 9 V DC wall adapter into its
barrel jack (D-47), the 1000 µF capacitor (D-19), and from the kit for the fan switch: a PN2222 transistor, a 220 Ω
and a 10 kΩ resistor and a 1N4007 diode (D-64). Everything sits on the two breadboards and the desk: no cutting, no
soldering (D-24).

Wire in this order (D-41f): **power rails → E-stop → presence sensor → servo → fan**, then the **bring-up
checkpoint**, then **LCD → buzzer → RGB**. After each part, run its check in the `bringup` sketch.

## Pin table (single source; `firmware/ring3cell/pins.h` mirrors it)

| Uno pin | Use | Direction | PWM / timer | Notes |
|---|---|---|---|---|
| D0, D1 | USB serial to the PC | — | — | **Leave free.** This is the link to Ubuntu/QEMU. |
| **D2** | E-stop button | in, `INPUT_PULLUP` | — | **INT0**, falling edge. Pressed = LOW. |
| **D3** | HC-SR04 ECHO | in, `INPUT_PULLUP` | Timer2 pin, PWM unused | **INT1**, `CHANGE`. Pull-up: an unplugged wire reads "no echo". |
| D4 | HC-SR04 TRIG | out | — | 10 µs pulse. |
| **D5** | Fan: PN2222 base through 220 Ω | out, PWM | **Timer0** (OC0B, ~980 Hz) | D-43: fan PWM on D5 or D6 only. HIGH = fan on (D-64). 10 kΩ base pull-down to MOTOR − (D-48 intent). |
| D6 | spare | — | Timer0 (OC0A) | |
| D7 | Active buzzer + | out | — | `digitalWrite` on/off; no `tone()` (Timer2). |
| D8 | LCD RS (LCD pin 4) | out | — | |
| **D9** | Servo signal | out | Timer1 (Servo library) | The Servo library takes Timer1, so D9 and D10 lose PWM. |
| D10 | LCD E (LCD pin 6) | out | Timer1 PWM lost | Plain digital is all the LCD needs. |
| D11 | RGB red | out | Timer2 pin, PWM unused | Plain digital. |
| D12 | RGB green | out | — | Plain digital. |
| D13 | spare | — | — | On-board LED; blinks while the bootloader runs. |
| A0 | LCD D4 (LCD pin 11) | out | — | Analog pins used as digital pins. |
| A1 | LCD D5 (LCD pin 12) | out | — | |
| A2 | LCD D6 (LCD pin 13) | out | — | |
| A3 | LCD D7 (LCD pin 14) | out | — | |
| A4 | RGB blue | out | — | Plain digital. |
| A5 | spare | — | — | |
| 5V | LOGIC rail + | power out | — | Feeds the sensor, LCD, buzzer, RGB (all on board 2) and the E-stop pull-up side. **Never to the MOTOR rail.** |
| GND | LOGIC rail − | ground | — | |

**15 of 18 usable pins used** (spare: D6, D13, A5). No conflicts: one function per pin; INT0 and INT1 are the
only two external interrupts and both are used; Timer0 drives the fan PWM (and `millis()`); Timer1 belongs to the
Servo library; Timer2 is unused, because `tone()` is not used.

**Hard-wired, no Uno pin:** LCD RW (pin 5) → LOGIC −, so the LCD is write-only. The fan turns one way only, so a
single low-side switch is enough and needs no direction pins (D-64; the L293D and its IN1/IN2/EN3,4 wiring are gone).

## Before you start (read once)

- **Two boards, two ways of naming holes.** The **main board** (in front, nearest you) uses lower-case rows:
  row **a** is nearest you, row **j** the far side. **Board 2** (about 1 cm behind it) uses the rows **as printed**,
  upper case: row **A** is at its **far** edge, row **J** nearest you. A hole is row + column: `c40` is main-board
  row c, column 40; `H58` is board-2 row H, column 58. Board 2's columns run 0–60 left to right (its knob uses one
  column left of 0, written `-1`).
- **Which rails are which.** Main board: the near rail pair (row-a side) is **MOTOR**, the far pair (row-j side) is
  **LOGIC**. Board 2: **all** its rails are LOGIC. With the power module in the left end, **red = +** and
  **blue = −** on every rail.
- **Power order rule (D-48): the module stays ON whenever the LOGIC rail is powered.** It came from the L293D, which
  leaked from its logic side into an unpowered MOTOR rail; the L293D is gone (D-64), but the rule stays (the servo's
  signal from D9 is still a path into an unpowered MOTOR rail, `[UNVERIFIED]` how much). So:
  - **Powering down to wire:** first pull the **Uno 5V → far +** wire (`R3`), **then** switch the module off.
  - **Powering up:** first switch the module **on**, **then** put the Uno 5V wire back.
  - The USB cable can stay plugged in (the board stays attached to Ubuntu); with `R3` out, the LOGIC rails are
    dead.
- **FLAT** wires are pre-cut and lie flat on the board; **FLEX** wires are the flexible jumpers to the Uno. Keep the
  FLEX wires to the E-stop away from the motor leads (D-49).
- **The kit's E-stop is only a push button.** A real E-stop is *normally closed*: a broken wire stops the machine.
  This one is *normally open*. A broken or loose E-stop wire does **not** stop anything; it only makes the button
  do nothing. The bring-up check below catches a wire that is loose at test time; it can't catch one that comes
  loose later (D-42f).
- Each part's check is one key in the `bringup` monitor: `e s v f l b r`. `a` runs all of them and asks which parts
  are wired.

## 1. Power rails (D-31, D-43, D-47, D-48, D-63)

Two separate 5 V supplies that share **ground only**:

| Rail | Fed by | Feeds |
|---|---|---|
| **MOTOR** (main board, near pair, row-a side) | Breadboard power module, near-side jumper on **5V** | the servo and the fan motor (through the NPN switch) |
| **LOGIC** (main board far pair, row-j side, and **every** rail on board 2) | The Uno's 5V pin; the module's far-side jumper **removed** | E-stop circuit, HC-SR04, LCD, buzzer, RGB |

1. **`R1`:** push the power module into the **left** end of the main board, its barrel jack pointing **left**, so
   its pins go into both rail pairs. **Check:** the module's + pins sit in the **red**-line rows of both pairs (in
   the right end they landed in the blue rows; that is why it moved, D-63).
2. Jumpers on the module (`[UNVERIFIED]` for your module: check the silkscreen; most have `5V`, `OFF`, `3.3V`):
   - near side (**MOTOR**): jumper cap on **5V**.
   - far side (**LOGIC**): **no cap**. Keep it where you can find it.
3. Main board wires:
   - `R3` Uno **5V** → far **+** (col 7). *This is the wire you pull out while wiring.* (FLEX)
   - `R4` Uno **GND** → far **−** (col 7). (FLEX)
   - `R5` near **−** (col 55) → far **−** (col 55). This ties the grounds even if the module is unplugged. (FLAT)
   - `R2` (already in): near red and near blue are each bridged col 26 → col 36 inside their own row. Harmless; keep.
4. Board 2 wires:
   - `R6` main far **+** / far **−** (col 46) → board 2 near **+** / near **−** (col 46). (FLAT)
   - `B1` bridge board 2's far **+** and far **−** across the break in the middle (~col 30), + to +, − to −.
   - `B2` board 2 near **+** (col 38) → far **+** (col 38); `B3` near **−** (col 39) → far **−** (col 39).
   - Board 2's rails: far side, **+ is the outer row** and − the inner row next to row A; near side, **+ is the inner
     row** next to row J and − the outer row.
5. **Never** connect MOTOR + to LOGIC +, and never put the Uno 5V wire on the MOTOR rail. The two 5 V supplies must
   not meet.
6. Plug the 9 V adapter into the module's barrel jack. Leave the module switched off. `[UNVERIFIED]`: from 9 V the
   module's 5 V regulator turns about 4 V × the motor + servo current into heat; after a long fan run, touch-check
   that the regulator isn't too hot to hold.

**Check:** LED-test each rail (lead's order). Module switched on → its power LED lights; the Uno's `ON` LED stays
lit and `arduino-cli board list` still shows the Uno. Switching the module on must not reset the Uno: the `bringup`
banner must not reprint.

## 2. E-stop button (D2, INT0) (D-49)

Parts: 1 push button (4 legs), one 100 nF ceramic capacitor (marked `104`), pre-cut wires. No pull-up resistor:
the Uno's internal pull-up is used.

**Ground rule (D-49): the E-stop's ground goes to LOGIC −, never to MOTOR −.** Motor current flows in the MOTOR −
rail; a button grounded there sees that current bounce its ground, and the firmware latched the E-stop by itself
while the fan ran (5 of 5 runs, 2026-11-01). Keep the button's wires away from the motor wires.

| # | Wire |
|---|---|
| `E1` | button straddling the centre gap, legs in **e12, e14, f12, f14** |
| `E2` | **j12** (the D2 node) → Uno **D2** (FLEX) |
| `E3` | 100 nF capacitor **i12 – i9** (noise filter on the D2 node; a press discharges it at once) |
| `E4` | **j9** → far **−** (LOGIC) |
| `E5` | **c14** (the ground leg) → **c17** |
| `E6` | **a17** → far **−** (LOGIC; long FLAT, across the board) |

Leg f12 (column 12, the D2 node) and leg e14 (column 14, the ground node) are diagonally opposite, so they are on
opposite sides of the switch and a press joins D2 to LOGIC −. Put the button where your hand
reaches it without crossing the fan.

**Check (`e`):** untouched shows `D2=HIGH (released)`; three presses show `PRESSED (D2=LOW)` then `released` each
time, with `INT0 falling edges: 3` or more; ends `-> OK`. If it says D2 is LOW without a press, the button is turned
90°: turn it. After the fan is wired, `tools/cellctl.py noise /dev/ttyACM0` (with `ring3cell`) must end
`NOISE SUMMARY: latch set without a press in: none`.

## 3. Presence sensor (HC-SR04 on board 2: TRIG D4, ECHO D3 = INT1)

Parts: HC-SR04, pre-cut wires, 2 FLEX wires, and a **backstop**: the keyboard, 25–35 cm behind the sensor, always
there (D-63). "Clear" then means "I can see the backstop". A missing echo counts as **not clear** (D-43f).

| # | Wire |
|---|---|
| `P1` | HC-SR04 pins in **H58–H61** (board 2, right end), the two "eyes" facing the keyboard. Seen from you it is usually **GND H58, Echo H59, Trig H60, VCC H61**: **check the labels printed on your sensor** and move the wires below if yours differ |
| `P2` | **J58** (GND) → board 2 near **−** (blue, outer row) (short FLAT) |
| `P3` | **J61** (VCC) → board 2 near **+** (red, inner row) (short FLAT) |
| `P4` | **I59** (Echo) → Uno **D3**; **I60** (Trig) → Uno **D4** (FLEX, single wires, no joins) |

All the sensor's wires plug into rows I–J, on your side of the sensor, so nothing is in the beam. Nothing else
(wires, fan, servo, board 2's parts) may be in the beam either.

**Check (`s`):** 15 readings at the backstop, all between 200 and 400 mm with a spread ≤ 30 mm and no `NO ECHO`;
then, with a hand ~5 cm in front of it, at least 8 of 10 readings below 100 mm; ends `-> OK`. Note the backstop
distance; it goes in the evidence.

## 4. Servo (SG90 on D9, MOTOR rail) + 1000 µF

Parts: SG90 servo with a horn, the 1000 µF electrolytic capacitor, 3 FLEX wires (male-to-male into the servo plug).

| # | Wire |
|---|---|
| `S1` | 1000 µF in the **near (MOTOR)** rails at col 33, **stripe (−, shorter leg) → near −**. Backwards can damage it: check twice |
| `S2` | servo **brown** → near **−**, **red** → near **+** (col 35) |
| `S3` | servo **orange** (signal) → Uno **D9** |

Lay the servo flat on the desk with the horn free to turn. (The printed gauge comes in T-07.)

**Check (`v`):** the horn turns to 60°, then 120°, then back to the middle (90°); you answer `y`; ends `-> OK`. If
the `RING3 BRINGUP` banner reprints during the sweep, the Uno reset. That is a power problem: the servo is
probably on the LOGIC rail, or the grounds aren't joined.

## 5. Fan (DC motor + blade, NPN low-side switch, D5) (D-64, D-64b, D-65)

Parts: PN2222 transistor (3 legs, one flat face), 220 Ω resistor (base), 10 kΩ resistor (base pull-down), 1N4007
diode (flyback, the stripe is the cathode), 100 nF ceramic capacitor (`104`, motor noise, D-49), the DC motor with
the fan blade, pre-cut wires and 1 FLEX wire.

How it works: D5 HIGH pushes current through the 220 Ω into the transistor's base, the transistor connects the
motor's lower lead to MOTOR −, and the motor runs from MOTOR +. PWM on D5 sets the speed. The 10 kΩ holds the base
low while the Uno resets (every pin is an input for about a second then), so the fan can't blip on its own (D-48
intent). The diode catches the motor's voltage spike when the switch opens. Motor current returns on **MOTOR −**,
never on the LOGIC ground (D-49).

| # | Wire |
|---|---|
| `F1` | PN2222, **flat face toward you**: **E c40, B c41, C c42** (`[UNVERIFIED]` for your part: check the print; E-B-C left to right is the usual PN2222 order) |
| `F2` | **a37** → Uno **D5** (FLEX) |
| `F3` | **220 Ω e37 – e41** (base resistor; D-65, was 330 Ω) |
| `F4` | **10 kΩ a41 → near −** (base pull-down) |
| `F5` | **a40** (emitter) → near **−** (FLAT) |
| `F6` | motor lead 2 → **a42** (collector); motor lead 1 → **b45**. **Twist the two leads together** and keep them away from the E-stop wires |
| `F7` | 1N4007 **d42** (anode) – **d45** (cathode, **stripe**). Stripe toward d45, the + side; backwards it shorts the motor supply when the switch closes |
| `F8` | 100 nF **e42 – e45** (across the motor) |
| `F9` | **a45** → near **+** (FLAT) |

**Placing the motor:** on its side at the front-right desk edge, blade over the edge, **out of the sensor beam**
(D-49: a motor in the beam read as a 15 cm object) and away from your hand on the E-stop. Hold it with putty only on
the side of the can (D-64b): never on either end or near the shaft. Putty pressing on the rear end (the small metal
bearing cap where the shaft ends) was the real cause of the slow fan. Flick the blade: it must coast freely.

**Check (pull-down):** press the Uno's reset button (or upload a sketch): the fan must not move at all.

**Check (`f`):** the fan ramps from 30 % to 100 % in 10 % steps, 1 s each. Note the step where the blade **first
starts turning** and type it in. That is the **minimum spinning duty**; it was 50 % on the L293D build (which lost
about 2 V), and is re-measured on the NPN switch (about 0.3 V loss, D-64) and goes in the evidence. Then the fan
stops and you answer `y`; ends `-> OK`. Run `f` once more and press the E-stop mid-ramp: it must print
`E-STOP: fan cut by INT0`, and the blade must stop. If the Uno resets during the ramp (banner reprints), stop and
report: it is a power problem. If air blows the wrong way, swap the motor leads (a42 ↔ b45; the diode and the
capacitor stay where they are).

## Bring-up checkpoint (after the first four parts)

Press `a`, answer `y` for E-stop, sensor, servo and fan and `n` for LCD, buzzer and RGB. All four must show `OK`
before you wire the rest.

## 6. LCD1602 on board 2 (RS D8, E D10, D4–D7 on A0–A3)

Parts: LCD1602 (16-pin header), 10 kΩ potentiometer (the contrast knob), 220 Ω resistor (backlight), pre-cut wires,
7 FLEX wires.

The LCD's pins go in row E, **pin 1 (VSS) at E7**, so LCD pin *n* is in column *6 + n* (pin 16 at E22). The screen
lies face up over rows F–J. Rows A and B in the same column are the pin's wiring points.

| # | Wire | LCD pin |
|---|---|---|
| `K1` | knob: its two end legs in **B-1** and **B1** (column -1 is left of column 0), the middle leg (wiper) in **E0** | |
| `K2` | **A-1** → board 2 far **+** | |
| `K3` | **A1** → far **−** | |
| `K4` | **A0** (wiper) → board-2 hole **D9** (that is LCD pin 3's column on the breadboard, **not** Uno D9) | 3 V0 (contrast) |
| `L1` | LCD header in **E7–E22** | |
| `L2` | **A7** → far **−** | 1 VSS |
| `L3` | **A8** → far **+** | 2 VDD |
| `L4` | **B10** → Uno **D8** (FLEX) | 4 RS |
| `L5` | **A11** → far **−** (hard-wired: write only) | 5 RW |
| `L6` | **B12** → Uno **D10** (FLEX) | 6 E |
| | columns 13–16: nothing (4-bit mode) | 7–10 D0–D3 |
| `L7` | **B17, B18, B19, B20** → Uno **A0, A1, A2, A3** (FLEX) | 11–14 D4–D7 |
| `L8` | **220 Ω A21 → far +** | 15 A (backlight +) |
| `L9` | **A22** → far **−** | 16 K (backlight −) |

**Check (`l`):** line 1 reads `RING3 BRINGUP`, line 2 `line 2 012345678` (16 characters, the full width). If you
see a row of solid blocks or nothing at all, turn the knob slowly until the text is sharp. You answer `y`; ends
`-> OK`. After `ring3cell` is flashed, the boot screen is `HALTED` / `no kernel link` (D-46).

## 7. Buzzer on board 2 (active, D7)

Parts: the **active** buzzer (sealed bottom; the passive one shows a circuit board underneath and only clicks), one
FLEX and one FLAT wire. The longer leg is **+**.

| # | Wire |
|---|---|
| `Z1` | buzzer **+** (long leg) **H45**, **−** **H48** (near half, right of the LCD) |
| `Z2` | **J45** → Uno **D7** (FLEX) |
| `Z3` | **J48** → board 2 near **−** (blue) (FLAT) |

**Check (`b`):** two short beeps; you answer `y`; ends `-> OK`. If you hear only faint clicks, it is the passive
buzzer: swap it.

## 8. RGB LED on board 2 (R D11, G D12, B A4)

Parts: RGB LED (4 legs, **common cathode**: the longest leg goes to GND), three 220 Ω resistors, 3 FLEX wires and
one FLAT wire.

| # | Wire |
|---|---|
| `G1` | LED legs **C40 (R), C41 (GND, longest), C42 (G), C43 (B)** |
| `G2` | **A41** → board 2 far **−** |
| `G3` | three 220 Ω across the gap: **E40 – F40**, **E42 – F42**, **E43 – F43** |
| `G4` | **I40** → Uno **D11**, **I42** → Uno **D12**, **I43** → Uno **A4** (FLEX) |

**Check (`r`):** red, then green, then blue, then dark; you answer `y`; ends `-> OK`. If the colours come out in a
different order, swap the FLEX wires to match. If the LED is lit when it should be dark and goes dark on its colour,
it is common **anode**: stop and report (the firmware would need the logic inverted).

## Final bring-up

Press `a` and answer `y` for every part. Then reflash `ring3cell` and run `tools/cellctl.py noise /dev/ttyACM0`
(must end `none`).
