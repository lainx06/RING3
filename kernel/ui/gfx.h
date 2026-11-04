/* The pixel status screen (T-18, D-101): the same snapshot as the text screen (screen.h), drawn at 1024x768
 * with shapes. `gfx_render()` is a pure renderer like screen_render(): no I/O, no globals, no clock; it draws
 * into a caller-owned back buffer and reports which rectangles it touched, so the driver copies only those
 * to the framebuffer. Built for the kernel and, unchanged, for the host golden-image tests
 * (tests/host/gfx_test.c). The glyphs come from the caller: the kernel reads them from VGA plane 2 at boot
 * (hardware data), the host test loads the same bytes from a fixture.
 *
 * Same data, same rules as the text screen: nothing moves without a real value, every state has a word,
 * the words come from words.c. Nothing here decides anything. */
#ifndef RING3_UI_GFX_H
#define RING3_UI_GFX_H
#include "screen.h"
#include <stdint.h>

#define GFX_W 1024
#define GFX_H 768
#define GFX_BPP 32
#define GFX_BYTES (GFX_W * GFX_H * 4u)
#define GFX_MAX_RECTS 24
#define GFX_FAN_PERIOD_DEG 120u   /* three identical blades: the picture repeats every 120 degrees */

/* 8x16 glyphs; rows[ch][y] bit 7 is the leftmost pixel (the VGA plane-2 layout, 16 of the 32 bytes). */
struct gfx_font {
    uint8_t rows[256][16];
};

struct gfx_rect {
    uint16_t x, y, w, h;
};

/* Pixel 0x00RRGGBB in a little-endian uint32 (the 32 bpp framebuffer layout). Draws the regions whose inputs
 * differ between `m` and `prev` into `fb` (GFX_W x GFX_H pixels) and writes their rectangles to `out`
 * (at most GFX_MAX_RECTS); returns the count. `prev` == 0 draws everything and reports one full rectangle.
 * `pending`: the caller's region bits between calls (regions still to draw); `budget`: the most pixel units
 * (area drawn + area copied) one call may spend, 0 = unlimited. Regions that do not fit stay pending and are
 * drawn by the next call, in priority order (status, banner, console, ticker, panel words, pictures, clock,
 * rotor), so a tick never does more than the budget while every change still reaches the screen. */
uint32_t gfx_render(const struct screen_model *m, const struct screen_model *prev, const struct gfx_font *font,
                    uint32_t *fb, struct gfx_rect *out, uint32_t *pending, uint32_t budget);
/* The kernel's budget per tick: about 1.3 ms of drawing under QEMU's TCG (measured, docs/evidence/T-18). */
#define GFX_TICK_BUDGET 66000u

/* The rotor: degrees of rotation per tick for a board-reported duty (0 = still), and the rotation angle
 * (0..GFX_FAN_PERIOD_DEG-1) for a snapshot: 0 while frozen (no telemetry, duty 0, E-stop latched, link STALE
 * or LOST, the text screen's rule), otherwise tick x rate, so it turns only while the board reports a duty. */
uint32_t gfx_fan_deg_per_tick(uint8_t duty);
uint32_t gfx_fan_angle(const struct screen_model *m);
#endif
