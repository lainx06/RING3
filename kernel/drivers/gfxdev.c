/* QEMU standard VGA (Bochs DISPI) framebuffer plumbing: see gfxdev.h.
 *
 * Facts used (recorded in docs/evidence/T-18/README.md with their sources):
 * - PCI configuration mechanism 1: address port 0xCF8 (enable bit 31, bus 23:16, device 15:11, function 10:8,
 *   register 7:2), data port 0xCFC (PCI Local Bus Specification).
 * - QEMU standard VGA: PCI vendor 0x1234 device 0x1111; BAR0 is the framebuffer (16 MiB by default); the
 *   Bochs VBE interface is at I/O ports 0x01CE (index) and 0x01CF (data) (QEMU docs/specs/standard-vga).
 * - DISPI registers: 0 ID (0xB0C0..0xB0C5), 1 XRES, 2 YRES, 3 BPP, 4 ENABLE (0x01 enabled, 0x40 linear
 *   framebuffer, 0x80 keep memory), 6 VIRT_WIDTH (QEMU include/hw/display/bochs-vbe.h). 32 bpp pixels are
 *   little-endian 0x00RRGGBB (QEMU's endianness register defaults to little endian).
 * - VGA: sequencer index/data 0x3C4/0x3C5 (2 map mask, 4 memory mode, 0 reset), graphics controller
 *   0x3CE/0x3CF (4 read map select, 5 mode, 6 miscellaneous); the text-mode font lives in plane 2, 32 bytes
 *   per character, readable at 0xA0000 with plane 2 selected and odd/even addressing off (IBM VGA technical
 *   reference). */
#include "gfxdev.h"
#include "exec.h"
#include "io.h"
#include "kstring.h"
#include "paging.h"
#include "pmm.h"
#include "printk.h"

#define PCI_ADDR 0xCF8
#define PCI_DATA 0xCFC
#define QEMU_VGA_ID 0x11111234u          /* device 0x1111 << 16 | vendor 0x1234 */
#define PCI_BAR0 0x10
#define DISPI_INDEX 0x01CE
#define DISPI_DATA 0x01CF
#define DISPI_ID 0
#define DISPI_XRES 1
#define DISPI_YRES 2
#define DISPI_BPP 3
#define DISPI_ENABLE 4
#define DISPI_VIRT_WIDTH 6
#define DISPI_ID_MIN 0xB0C0
#define DISPI_ID_MAX 0xB0C5
#define DISPI_ENABLED 0x01
#define DISPI_LFB 0x40
#define BACK_PHYS PMM_LIMIT              /* the first 3 MiB above the allocator window */
#define WINDOW_PAGES (GFX_BYTES / PAGE_SIZE)

static uint32_t pci_read(uint32_t dev, uint32_t reg)
{
    outl(PCI_ADDR, 0x80000000u | dev << 11 | (reg & 0xFCu));
    return inl(PCI_DATA);
}

static void dispi_write(uint16_t index, uint16_t v)
{
    outw(DISPI_INDEX, index);
    outw(DISPI_DATA, v);
}

static uint16_t dispi_read(uint16_t index)
{
    outw(DISPI_INDEX, index);
    return inw(DISPI_DATA);
}

static uint8_t seq_read(uint8_t i)
{
    outb(0x3C4, i);
    return inb(0x3C5);
}

static void seq_write(uint8_t i, uint8_t v)
{
    outb(0x3C4, i);
    outb(0x3C5, v);
}

static uint8_t gc_read(uint8_t i)
{
    outb(0x3CE, i);
    return inb(0x3CF);
}

static void gc_write(uint8_t i, uint8_t v)
{
    outb(0x3CE, i);
    outb(0x3CF, v);
}

/* The 8x16 glyphs the VGA BIOS loaded into plane 2, read through the 0xA0000 window with plane 2 selected;
 * the registers are put back as they were. Sanity: the space is blank, the full block (0xDB) is solid and
 * 'A' has ink. */
static int font_read(struct gfx_font *f)
{
    uint8_t s2 = seq_read(2), s4 = seq_read(4), g4 = gc_read(4), g5 = gc_read(5), g6 = gc_read(6);
    seq_write(0, 0x01);                       /* synchronous reset while the memory mode changes */
    seq_write(2, 0x04);                       /* map mask: plane 2 */
    seq_write(4, 0x06);                       /* extended memory, odd/even addressing off */
    seq_write(0, 0x03);
    gc_write(4, 0x02);                        /* read map select: plane 2 */
    gc_write(5, 0x00);                        /* read mode 0, odd/even off */
    gc_write(6, 0x04);                        /* graphics map at 0xA0000, 64 KiB */
    const volatile uint8_t *plane = (const volatile uint8_t *)0xA0000;
    for (uint32_t c = 0; c < 256; c++)
        for (uint32_t r = 0; r < 16; r++)
            f->rows[c][r] = plane[c * 32 + r];
    seq_write(0, 0x01);
    seq_write(2, s2);
    seq_write(4, s4);
    seq_write(0, 0x03);
    gc_write(4, g4);
    gc_write(5, g5);
    gc_write(6, g6);
    int blank = 1, solid = 1, ink = 0;
    for (uint32_t r = 0; r < 16; r++) {
        blank &= f->rows[' '][r] == 0;
        solid &= f->rows[0xDB][r] == 0xFF;
        ink |= f->rows['A'][r] != 0;
    }
    return blank && solid && ink;
}

static int ram_covers(const struct multiboot_info *mbi, uint64_t start, uint64_t end)
{
    if (!(mbi->flags & MBI_FLAG_MMAP))
        return 0;
    uint32_t p = mbi->mmap_addr, last = mbi->mmap_addr + mbi->mmap_length;
    while (p < last) {
        const struct multiboot_mmap_entry *e = (const struct multiboot_mmap_entry *)(uintptr_t)p;
        if (e->size < sizeof *e - 4)
            return 0;
        if (e->type == MULTIBOOT_MEMORY_AVAILABLE && e->addr <= start && e->addr + e->len >= end)
            return 1;
        p += e->size + 4;
    }
    return 0;
}

int gfxdev_init(const struct multiboot_info *mbi, struct gfx_font *font, struct gfxdev *dev)
{
    uint32_t bar = 0, found = 0;
    for (uint32_t d = 0; d < 32 && !found; d++)
        if (pci_read(d, 0) == QEMU_VGA_ID) {
            bar = pci_read(d, PCI_BAR0);
            found = 1;
        }
    if (!found) {
        printk("gfx: no QEMU standard VGA (PCI 1234:1111) on bus 0: text mode\n");
        return 0;
    }
    uint16_t id = dispi_read(DISPI_ID);
    if (id < DISPI_ID_MIN || id > DISPI_ID_MAX) {
        printk("gfx: DISPI id 0x%x not in 0xB0C0..0xB0C5: text mode\n", id);
        return 0;
    }
    if (bar & 1u) {
        printk("gfx: BAR0 0x%08x is an I/O BAR, not memory: text mode\n", bar);
        return 0;
    }
    uint32_t lfb = bar & ~0xFu;
    if (lfb < PMM_LIMIT || lfb + GFX_BYTES < lfb || (lfb < USER_TOP && lfb + GFX_BYTES > USER_BASE)) {
        printk("gfx: framebuffer 0x%08x cannot be a kernel-only window: text mode\n", lfb);
        return 0;
    }
    if (!ram_covers(mbi, BACK_PHYS, (uint64_t)BACK_PHYS + GFX_BYTES)) {
        printk("gfx: no RAM at 0x%08x for the back buffer (memory map): text mode\n", BACK_PHYS);
        return 0;
    }
    if (!font_read(font)) {
        printk("gfx: VGA plane 2 did not hold a usable 8x16 font: text mode\n");
        return 0;
    }
    paging_map_kernel(lfb, lfb, WINDOW_PAGES);
    paging_map_kernel(BACK_PHYS, BACK_PHYS, WINDOW_PAGES);
    dev->lfb = (uint32_t *)(uintptr_t)lfb;
    dev->back = (uint32_t *)(uintptr_t)BACK_PHYS;
    dev->lfb_phys = lfb;
    dev->back_phys = BACK_PHYS;
    memset(dev->back, 0, GFX_BYTES);

    dispi_write(DISPI_ENABLE, 0);
    dispi_write(DISPI_XRES, GFX_W);
    dispi_write(DISPI_YRES, GFX_H);
    dispi_write(DISPI_BPP, GFX_BPP);
    dispi_write(DISPI_VIRT_WIDTH, GFX_W);
    dispi_write(DISPI_ENABLE, DISPI_ENABLED | DISPI_LFB);
    if (dispi_read(DISPI_XRES) != GFX_W || dispi_read(DISPI_YRES) != GFX_H || dispi_read(DISPI_BPP) != GFX_BPP ||
        !(dispi_read(DISPI_ENABLE) & DISPI_ENABLED)) {
        dispi_write(DISPI_ENABLE, 0);         /* back to the VGA text mode the BIOS set */
        printk("gfx: mode %ux%ux%u not taken (read back %ux%ux%u): text mode\n", GFX_W, GFX_H, GFX_BPP,
               dispi_read(DISPI_XRES), dispi_read(DISPI_YRES), dispi_read(DISPI_BPP));
        return 0;
    }
    memset(dev->lfb, 0, GFX_BYTES);
    printk("gfx: %ux%ux%u linear framebuffer phys=0x%08x (PCI 1234:1111 BAR0, DISPI id 0x%x), back buffer "
           "phys=0x%08x, font from VGA plane 2, both windows kernel-only\n",
           GFX_W, GFX_H, GFX_BPP, lfb, id, BACK_PHYS);
    return 1;
}

uint32_t gfxdev_copy(const struct gfxdev *dev, uint32_t x, uint32_t y, uint32_t w, uint32_t h)
{
    if (x >= GFX_W || y >= GFX_H)
        return 0;
    if (x + w > GFX_W)
        w = GFX_W - x;
    if (y + h > GFX_H)
        h = GFX_H - y;
    for (uint32_t r = y; r < y + h; r++) {
        uint32_t *dst = dev->lfb + r * GFX_W + x;
        const uint32_t *src = dev->back + r * GFX_W + x;
        uint32_t n = w;
        __asm__ volatile("rep movsl" : "+D"(dst), "+S"(src), "+c"(n) : : "memory");
    }
    return w * h;
}
