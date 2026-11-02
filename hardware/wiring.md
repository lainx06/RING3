# RING3 work cell: wiring guide (T-06)

Parts are from the ELEGOO Super Starter Kit, plus the breadboard power module fed by a 9 V DC wall adapter into its barrel jack (D-47; D-34's USB charger also works) and
the 1000 µF capacitor (D-19). Everything sits on one breadboard and the desk: no cutting, no soldering (D-24).

Wire in this order (D-41f): **power rails → E-stop → presence sensor → servo → fan** (target 90 min), then the
**bring-up checkpoint**, then **LCD → buzzer → RGB**. After each part, run its check in the `bringup` sketch.

## Pin table (single source; `firmware/ring3cell/pins.h` mirrors it)

| Uno pin | Use | Direction | PWM / timer | Notes |
|---|---|---|---|---|
| D0, D1 | USB serial to the PC | — | — | **Leave free.** This is the link to Ubuntu/QEMU. |
| **D2** | E-stop button | in, `INPUT_PULLUP` | — | **INT0**, falling edge. Pressed = LOW. |
| **D3** | HC-SR04 ECHO | in, `INPUT_PULLUP` | Timer2 pin, PWM unused | **INT1**, `CHANGE`. Pull-up: an unplugged wire reads "no echo". |
| D4 | HC-SR04 TRIG | out | — | 10 µs pulse. |
| **D5** | Fan: L293D pin 1 (EN1,2) | out, PWM | **Timer0** (OC0B, ~980 Hz) | D-43: fan PWM on D5 or D6 only. 10 kΩ pull-down on L293D pin 1 (D-48). |
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
| 5V | LOGIC rail + | power out | — | Feeds L293D pin 16, sensor, LCD, buzzer, RGB. **Never to the MOTOR rail.** |
| GND | LOGIC rail − | ground | — | |

**15 of 18 usable pins used** (spare: D6, D13, A5). No conflicts: one function per pin; INT0 and INT1 are the
only two external interrupts and both are used; Timer0 drives the fan PWM (and `millis()`); Timer1 belongs to the
Servo library; Timer2 is unused, because `tone()` is not used.

**Hard-wired, no Uno pin:** L293D IN1 (pin 2) → LOGIC 5 V and IN2 (pin 7) → GND, so the fan turns one way only;
L293D EN3,4 (pin 9) → GND, so the second channel is off; LCD RW (pin 5) → GND, so the LCD is write-only.

## Before you start (read once)

- **Power order rule (D-48): the module stays ON whenever the LOGIC rail is powered.** With the module off and
  the Uno powered, the L293D leaks from its logic side (pin 16 VCC1, IN1 tied to LOGIC +) into the unpowered MOTOR
  rail; the module's LED then glows dimly. So:
  - **Powering down to wire:** first pull the **Uno 5V → LOGIC +** jumper, **then** switch the module off.
  - **Powering up:** first switch the module **on**, **then** put the Uno 5V jumper back.
  - The USB cable can stay plugged in (the board stays attached to Ubuntu); with the Uno 5V jumper out, the LOGIC
    rail is dead and nothing leaks.
- **"A → B"** means one jumper wire from A to B. "Row 10" means the 5-hole column numbered 10 on the breadboard.
- **Split rails:** on some breadboards the red/blue power lines stop halfway along, which means the rail is cut in two
  there. If yours has a gap, bridge each rail across the gap with a short jumper (+ to +, − to −).
- **The kit's E-stop is only a push button.** A real E-stop is *normally closed*: a broken wire stops the machine.
  This one is *normally open*. A broken or loose E-stop wire does **not** stop anything; it only makes the button
  do nothing. The bring-up check below catches a wire that is loose at test time; it can't catch one that comes
  loose later (D-42f).
- Flash `bringup` once at the start (the commands are in the T-06 session). Each part's check is one key in
  the monitor: `e s v f l b r`. `a` runs all of them and asks which parts are wired.

## 1. Power rails (D-31, D-43, D-47, D-48)

Two separate 5 V supplies that share **ground only**:

| Rail | Fed by | Feeds |
|---|---|---|
| **MOTOR** (the rail pair on the row-**a** side) | Breadboard power module, its jumper on **5V** | L293D pin 8 (VCC2, motor supply) and the servo |
| **LOGIC** (the rail pair on the row-**j** side) | The Uno's 5V pin; the module's jumper on this side **OFF** (or pulled out) | L293D pin 16 (VCC1), HC-SR04, LCD, buzzer, RGB |

Parts: breadboard power module, 9 V DC wall adapter (barrel plug), 3 jumper wires. Optional: a tape label "M" and "L".

1. Plug the power module into one end of the breadboard so its pins go into **both** rail pairs. Line its `+`/`−`
   markings up with the breadboard's red (+) and blue (−) lines on both sides.
2. Find the two voltage jumpers on the module. `[UNVERIFIED]`: on most of these modules each side has positions
   marked `5V`, `OFF` and `3.3V`; check the silkscreen printed on **your** module.
   - Row-a side (**MOTOR**): jumper on **5V**.
   - Row-j side (**LOGIC**): jumper on **OFF**, or pull it out and keep it where you can find it. Pulling it out is the
     surest way to keep the module from driving this rail.
3. Wires:
   - Uno **5V** → LOGIC **+** (row-j side red line). *This is the jumper you pull out while wiring.*
   - Uno **GND** → LOGIC **−** (row-j side blue line).
   - MOTOR **−** (row-a side blue) → LOGIC **−** (row-j side blue). This ties the grounds even if the module is unplugged.
4. **Never** connect MOTOR + to LOGIC +, and never put the Uno 5V wire on the MOTOR rail. The two 5 V supplies must
   not meet.
5. **Order (D-48):** module on before the Uno 5V jumper goes in; jumper out before the module goes off. Never leave
   the module off while the Uno feeds the LOGIC rail (see "Power order rule" above).
6. Plug the 9 V adapter into the module's barrel jack. Leave the module switched off. `[UNVERIFIED]`: from 9 V the
   module's 5 V regulator turns about 4 V × the motor + servo current into heat; after a long fan run, touch-check
   that the regulator isn't too hot to hold. If the fan or servo stutters, a regulator in thermal limit is the first
   suspect (the Uno, on its own supply, keeps running).

**Check:** module switched on → its power LED lights. The Uno's `ON` LED stays lit, and `arduino-cli board list`
still shows the Uno. Switching the module on must not reset the Uno: the `bringup` banner must not reprint. If you
ever see the module's LED glowing **dimly** while it is switched off, that is the L293D leak (D-48): switch the
module on or pull the Uno 5V jumper, and keep to the power order.

## 2. E-stop button (D2, INT0)

Parts: 1 push button (4 legs), 2 jumper wires. No resistor: the Uno's internal pull-up is used.

1. Push the button in so that it **straddles the centre gap** of the breadboard (two legs above the gap, two below).
2. Use two legs that are **diagonally opposite** (for example top-left and bottom-right). Diagonal legs are always
   on opposite sides of the switch, whichever way the button is turned.
   - top-left leg's row → Uno **D2**
   - bottom-right leg's row → LOGIC **−** (GND)
3. Put it where your hand can reach it without crossing the fan.

**Check (`e`):** untouched shows `D2=HIGH (released)`; three presses show `PRESSED (D2=LOW)` then `released` each
time, with `INT0 falling edges: 3` or more; ends `-> OK`. If it says D2 is LOW without a press, move one wire to the
diagonal leg.

## 3. Presence sensor (HC-SR04: TRIG D4, ECHO D3 = INT1)

Parts: HC-SR04, 4 jumper wires, and a **backstop**: a book or box standing on the desk **20–40 cm** in front of
the sensor, always there. "Clear" then means "I can see the backstop". A missing echo counts as **not clear**
(D-43f).

1. Push the HC-SR04 into the breadboard near an edge with the two "eyes" facing out over the desk, level, pointing at
   the backstop. Nothing else (wires, fan, servo) may be in the beam.
2. Wires (pin names are printed on the sensor):
   - **VCC** → LOGIC **+**
   - **Trig** → Uno **D4**
   - **Echo** → Uno **D3**
   - **Gnd** → LOGIC **−**
3. Stand the backstop 20–40 cm away, flat face towards the sensor.

**Check (`s`):** 15 readings at the backstop, all between 200 and 400 mm with a spread ≤ 30 mm and no `NO ECHO`;
then, with a hand ~5 cm in front of it, at least 8 of 10 readings below 100 mm; ends `-> OK`. Note the backstop
distance; it goes in the evidence.

## 4. Servo (SG90 on D9, MOTOR rail) + 1000 µF

Parts: SG90 servo with a horn, the 1000 µF electrolytic capacitor, 3 jumper wires (the servo plug has 3 holes:
use male-to-male jumpers into it).

1. **Capacitor first.** An electrolytic capacitor has a polarity: the **stripe** marks the **−** leg (the shorter
   one). Put it across the **MOTOR** rail: stripe leg → MOTOR **−**, other leg → MOTOR **+**. Place it near where the
   servo wires land. Backwards can damage it, so check twice.
2. Servo wires:
   - **brown** → MOTOR **−**
   - **red** → MOTOR **+**
   - **orange** (signal) → Uno **D9**
3. Lay the servo flat on the desk with the horn free to turn. (The printed gauge comes in T-07.)

**Check (`v`):** the horn turns to 60°, then 120°, then back to the middle (90°); you answer `y`; ends `-> OK`. If
the `RING3 BRINGUP` banner reprints during the sweep, the Uno reset. That is a power problem: the servo is
probably on the LOGIC rail, or the grounds aren't joined.

## 5. Fan (DC motor + blade through the L293D, EN on D5)

Parts: L293D chip (16 pins), DC motor with the fan blade, one 10 kΩ resistor (pull-down, D-48), about 10 jumper wires. The L293D has diodes built in for the
motor, so no extra diode is needed.

1. Find the **notch** (a half-moon) at one end of the chip. Push the chip in so it **straddles the centre gap**,
   notch pointing **left**. Pins are numbered from the notch, anticlockwise when you look down on the chip:
   ```
          16 15 14 13 12 11 10  9      <- top row (above the gap)
         +-----------------------+
   notch )        L293D          |
         +-----------------------+
           1  2  3  4  5  6  7  8      <- bottom row (below the gap)
   ```
   | Pin | Name | Wire to |
   |---|---|---|
   | 1 | EN1,2 (speed) | Uno **D5**, and a **10 kΩ resistor from pin 1 to LOGIC −** (pull-down, D-48) |
   | 2 | IN1 | LOGIC **+** (hard-wired HIGH) |
   | 3 | OUT1 | motor wire A |
   | 4 | GND | LOGIC **−** |
   | 5 | GND | LOGIC **−** |
   | 6 | OUT2 | motor wire B |
   | 7 | IN2 | LOGIC **−** (hard-wired LOW) |
   | 8 | VCC2 (motor power) | **MOTOR +** |
   | 9 | EN3,4 | LOGIC **−** (second channel off) |
   | 10, 11, 14, 15 | channel 2 | nothing |
   | 12, 13 | GND | LOGIC **−** (optional; they're the same ground as 4/5 and help with heat) |
   | 16 | VCC1 (logic power) | LOGIC **+** |
2. Motor: its two wires go to pin 3 and pin 6 (either way round). Press the fan blade onto the shaft. Put the motor
   where the blade can spin freely, away from the sensor beam and from your hand on the E-stop.
3. Double-check pins 8 and 16: **8 = MOTOR +**, **16 = LOGIC +**. They are on opposite corners of the chip.
4. **Pull-down on pin 1 (D-48).** While the Uno resets (after an upload, a port open or the reset button) every pin
   is an input for about a second, so EN floats and the fan can blip on its own. A 10 kΩ resistor from L293D pin 1
   to LOGIC − holds EN low until the firmware drives it. (Reference build: pin 1 at b40, resistor b40 → GND.)

**Check (pull-down):** press the Uno's reset button (or upload a sketch): the fan must not move at all.

**Check (`f`):** the fan ramps from 30 % to 100 % in 10 % steps, 1 s each. Note the step where the blade **first
starts turning** and type it in. That is the **minimum spinning duty** (the L293D loses about 2 V, D-43); it goes in
the evidence. Then the fan stops and you answer `y`; ends `-> OK`. Run `f` once more and press the E-stop
mid-ramp: it must print `E-STOP: fan cut by INT0`, and the blade must stop. If the Uno resets during the ramp (banner
reprints), stop and report: it is a power problem. If air blows the wrong way, swap the motor wires on pins 3
and 6.

## Bring-up checkpoint (after the first four parts)

Press `a`, answer `y` for E-stop, sensor, servo and fan and `n` for LCD, buzzer and RGB. Save the whole monitor
output: it is `docs/evidence/T-06/bringup.txt` part 1. All four must show `OK` before you wire the rest.

## 6. LCD1602 (RS D8, E D10, D4–D7 on A0–A3)

Parts: LCD1602 (16 pins along the top edge; `[UNVERIFIED]`: in this kit it should come with the pin header
already soldered), 10 kΩ potentiometer (contrast), 220 Ω resistor (backlight), 13 jumper wires.

1. Push the LCD's header pins into the breadboard (each pin in its own row). Pin 1 is marked `VSS` or `1`.
2. Push the potentiometer in (3 legs in 3 rows): outer legs → LOGIC **+** and LOGIC **−** (either way round),
   middle leg → LCD pin 3.
3. Wires:
   | LCD pin | Name | Wire to |
   |---|---|---|
   | 1 | VSS | LOGIC **−** |
   | 2 | VDD | LOGIC **+** |
   | 3 | V0 (contrast) | potentiometer middle leg |
   | 4 | RS | Uno **D8** |
   | 5 | RW | LOGIC **−** (hard-wired: write only) |
   | 6 | E | Uno **D10** |
   | 7–10 | D0–D3 | nothing (4-bit mode) |
   | 11 | D4 | Uno **A0** |
   | 12 | D5 | Uno **A1** |
   | 13 | D6 | Uno **A2** |
   | 14 | D7 | Uno **A3** |
   | 15 | A (backlight +) | 220 Ω resistor → LOGIC **+** |
   | 16 | K (backlight −) | LOGIC **−** |

**Check (`l`):** line 1 reads `RING3 BRINGUP`, line 2 `line 2 0123456789`. If you see a row of solid blocks or
nothing at all, turn the potentiometer slowly until the text is sharp. You answer `y`; ends `-> OK`.

## 7. Buzzer (active, D7)

Parts: the **active** buzzer, 2 jumper wires. The kit has two buzzers. `[UNVERIFIED]` for your kit: the active one
usually has a sealed black bottom and a sticker on top; the passive one shows a green circuit board underneath. The
longer leg is **+**.

- buzzer **+** (long leg) → Uno **D7**
- buzzer **−** → LOGIC **−**

**Check (`b`):** two short beeps; you answer `y`; ends `-> OK`. If you hear only faint clicks, it is the passive
buzzer: swap it.

## 8. RGB LED (R D11, G D12, B A4)

Parts: RGB LED (4 legs), three 220 Ω resistors, 4 jumper wires. `[UNVERIFIED]` for your kit: the kit's RGB LED
should be **common cathode**, where the **longest leg** goes to GND. With the longest leg second from the flat side, the
order is usually R, GND, G, B.

1. Push the LED in so that each leg is in its own row.
2. Longest leg → LOGIC **−**.
3. Each colour leg → a 220 Ω resistor → its Uno pin:
   - red leg → 220 Ω → Uno **D11**
   - green leg → 220 Ω → Uno **D12**
   - blue leg → 220 Ω → Uno **A4**

**Check (`r`):** red, then green, then blue, then dark; you answer `y`; ends `-> OK`. If the colours come out in a
different order, swap the wires to match. If the LED is lit when it should be dark and goes dark on its colour,
it is common **anode**: stop and report (the firmware would need the logic inverted).

## Final bring-up

Press `a` and answer `y` for every part. Save the output as `docs/evidence/T-06/bringup.txt` part 2.
