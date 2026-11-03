# Work cell layout v2 (two boards, pre-cut wires), lead, 2026-11-02 (D-63)

Top view, user at the bottom. Main board in front; board 2 ~1 cm behind it. Uno left of the main board. Power module
in the LEFT end of the main board, turned so its power jack points left (in the right end its + pins sat in the blue-painted rows; turned and moved, its + lands on the red rows of both strips, so the paint is true). Fan at the front-right desk edge. Keyboard (sensor backstop) 25-35 cm behind the
sensor. FLAT = pre-cut wire (both ends in a breadboard, lying flat over empty holes). FLEX = flexible jumper.

## Main board (row a NEAREST the user, row j far; near rails = MOTOR, far rails = LOGIC; red = +, blue = - once the module is in the left end)
| # | From | To | Wire |
|---|---|---|---|
| R1 | power module in the LEFT end, jack pointing left | both rail pairs; header on the near side: cap on 5V; far-side header: no cap. Check: module + pins in the red-line rows | - |
| R2 | near red and near blue each bridged col 26 -> col 36 inside their own row (added while chasing the sticky motor; harmless, keep) | | FLAT |
| R3 | Uno 5V | far + (col 7) | FLEX (power-order wire) |
| R4 | Uno GND | far - (col 7) | FLEX |
| R5 | near - (col 55) | far - (col 55) | FLAT long |
| R6 | far + / far - (col 46) | board 2 near + / near - (col 46) | FLAT |
| E1 | E-stop button straddling the gap, legs e12, e14, f12, f14 | | part |
| E2 | j12 (D2 node) | Uno D2 | FLEX |
| E3 | 100 nF i12 - i9 | | part |
| E4 | j9 | far - | FLAT |
| E5 | c14 (ground leg) | c17 | FLAT |
| E6 | a17 | far - | FLAT long |
| S1 | 1000 uF in the near rails at col 33, stripe to near - | | part |
| S2 | servo brown / red | near - / near + (col 35) | FLEX |
| S3 | servo orange | Uno D9 | FLEX |
| F1 | PN2222 (flat face toward the user): E c40, B c41, C c42 (D-64; replaces the L293D) | | part |
| F2 | a37 | Uno D5 | FLEX |
| F3 | 220 ohm e37 - e41 (base resistor; was 330, D-65) | | part |
| F4 | 10 k a41 -> near - (base pull-down, D-48 intent) | | part |
| F5 | a40 (emitter) | near - | FLAT |
| F6 | motor lead 2 -> a42 (collector); motor lead 1 -> b45 | | motor leads, twisted |
| F7 | 1N4007 d42 (anode) - d45 (cathode, stripe) | | part |
| F8 | 100 nF e42 - e45 | | part |
| F9 | a45 | near + | FLAT |

## Board 2 (rows as PRINTED: row A at the FAR edge, row J nearest; columns 0-60 left to right; all rails LOGIC)
Far rails: + is the outer row, - the inner row next to row A. Near rails: + is the inner row next to row J, - the outer.
| # | From | To | Wire |
|---|---|---|---|
| B1 | bridge far + and far - across the middle break (~col 30) | | FLAT |
| B2 | near + (col 38) | far + (col 38) | FLAT long |
| B3 | near - (col 39) | far - (col 39) | FLAT long |
| K1 | knob (triangle legs): end legs B-1 and B1 (leftmost column, left of 0), wiper E0 | | part |
| K2 | A-1 | far + | FLAT |
| K3 | A1 | far - | FLAT |
| K4 | A0 (wiper) | D9 | FLEX |
| L1 | LCD pins E7-E22 (pin 1 VSS at E7), screen face up over rows F-J | | part |
| L2 | A7 | far - | FLAT |
| L3 | A8 | far + | FLAT |
| L4 | B10 | Uno D8 | FLEX |
| L5 | A11 | far - | FLAT |
| L6 | B12 | Uno D10 | FLEX |
| L7 | B17, B18, B19, B20 | Uno A0, A1, A2, A3 | FLEX |
| L8 | 220 ohm A21 | far + | part |
| L9 | A22 | far - | FLAT |
| Z1 | active buzzer: + (long leg) H45, - H48 (near half, right of the LCD; at C27/C30 the LCD body is in the way) | | part |
| Z2 | J45 | Uno D7 | FLEX |
| Z3 | J48 | board-2 near - (blue) | FLAT |
| G1 | RGB LED legs C40 (R), C41 (GND, longest), C42 (G), C43 (B) | | part |
| G2 | A41 | far - | FLAT |
| G3 | 220 ohm E40-F40, E42-F42, E43-F43 (across the gap) | | part |
| G4 | I40, I42, I43 | Uno D11, D12, A4 | FLEX |
| P1 | HC-SR04 pins H58-H61 (board 2, right end), transducers facing the keyboard; all wires plug in rows I-J, on the user's side of the sensor, out of the beam. Seen from the user, usually GND H58, Echo H59, Trig H60, VCC H61: check the labels | | part |
| P2 | J58 (GND) | near - (blue, outer row) | FLAT short |
| P3 | J61 (VCC) | near + (red, inner row) | FLAT short |
| P4 | I59 (Echo), I60 (Trig) | Uno D3, D4 | FLEX, single wires, no joins |

Order: rails (R, B rows), LED-test each rail; flash bringup; then E-stop (e), sensor (s), servo (v), fan (f), LCD (l),
buzzer (b), RGB (r); then a, reflash ring3cell, tools/cellctl.py noise.
