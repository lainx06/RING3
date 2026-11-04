#ifndef RING3_VGA_H
#define RING3_VGA_H
#include "multiboot.h"
#include "policy.h"
#include <stdint.h>
/* VGA text status screen (T-08): off unless the kernel was booted with the `screen` option. Display only:
 * every hook reads or copies existing state and never feeds, changes or delays a decision. The layout is
 * the pure renderer kernel/ui/screen.c; this driver fills its snapshot and writes the changed cells to
 * 0xB8000, at most once per tick, from the kernel's wait loops (audit_flush) or from a tick that
 * interrupted ring 3 (the kernel's own context is parked then). COM1 output is not changed. */
void vga_init(const struct multiboot_info *mbi); /* after interrupts are on: blink off, cursor off, TSC calibration;
                                                  T-18: with gfx=1 the pixel screen (kernel/drivers/gfxdev.c),
                                                  the text screen when the device or the mode is not there */
int vga_gfx_on(void);                /* 1 = the pixel screen is on */
void vga_print_font(void);           /* console `gfxfont` (test images): the plane-2 glyphs as hex, 256 lines */
int vga_on(void);
/* The audit path (interrupts off): an audited decision; `before` is the policy state it was decided on. */
void vga_note_decision(const struct pol_event *ev, const struct pol_decision *d, const struct pol_state *before);
void vga_putc(char c);               /* COM1 mirror into the console window */
void vga_backspace(void);            /* the console line editor removed a character */
void vga_service(void);              /* render + write changed cells; at most once per tick */
void vga_print_stats(void);          /* console `screen`: render time per tick, measured with the TSC */
#endif
