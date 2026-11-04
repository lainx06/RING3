#ifndef RING3_GFXDEV_H
#define RING3_GFXDEV_H
#include "gfx.h"
#include "multiboot.h"
#include <stdint.h>
/* T-18: QEMU's standard VGA in its 1024x768x32 linear-framebuffer mode (the Bochs DISPI interface), for the
 * pixel status screen. Everything here is display plumbing: the PCI scan for the device and its framebuffer
 * address (BAR0), the 8x16 glyphs read from VGA plane 2 before the mode switch, the kernel-only mappings of
 * the framebuffer and of the back buffer (physical memory above the allocator window), and the mode set.
 * Any check that fails leaves the machine in text mode with a line on COM1 saying why: never a panic, never a
 * blank screen. */
struct gfxdev {
    uint32_t *lfb;          /* the framebuffer window (kernel-only, identity mapped) */
    uint32_t *back;         /* the back buffer window (kernel-only, identity mapped) */
    uint32_t lfb_phys;      /* BAR0 */
    uint32_t back_phys;
};

/* 1 = graphics mode is on and `dev`/`font` are filled; 0 = text mode (reason printed). */
int gfxdev_init(const struct multiboot_info *mbi, struct gfx_font *font, struct gfxdev *dev);
/* Copies rectangle (x, y, w, h) of the back buffer to the framebuffer; returns the pixels copied. */
uint32_t gfxdev_copy(const struct gfxdev *dev, uint32_t x, uint32_t y, uint32_t w, uint32_t h);
#endif
