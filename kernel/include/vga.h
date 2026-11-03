#ifndef RING3_VGA_H
#define RING3_VGA_H
#include "policy.h"
#include <stdint.h>
/* VGA text status screen (T-08): off unless the kernel was booted with the `screen` option. Display only:
 * every hook reads or copies existing state and never feeds, changes or delays a decision. The layout is
 * the pure renderer kernel/ui/screen.c; this driver fills its snapshot and writes the changed cells to
 * 0xB8000, at most once per tick, from the kernel's wait loops (audit_flush) or from a tick that
 * interrupted ring 3 (the kernel's own context is parked then). COM1 output is not changed. */
void vga_init(void);                 /* after interrupts are on: blink off, cursor off, TSC calibration */
int vga_on(void);
/* The audit path (interrupts off): an audited decision; `before` is the policy state it was decided on. */
void vga_note_decision(const struct pol_event *ev, const struct pol_decision *d, const struct pol_state *before);
void vga_putc(char c);               /* COM1 mirror into the console window */
void vga_backspace(void);            /* the console line editor removed a character */
void vga_service(void);              /* render + write changed cells; at most once per tick */
void vga_print_stats(void);          /* console `screen`: render time per tick, measured with the TSC */
#endif
