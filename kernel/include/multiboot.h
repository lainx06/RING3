#ifndef RING3_MULTIBOOT_H
#define RING3_MULTIBOOT_H
#include <stdint.h>

#define MULTIBOOT_BOOTLOADER_MAGIC 0x2BADB002
#define MBI_FLAG_MEM     (1u << 0)
#define MBI_FLAG_CMDLINE (1u << 2)
#define MBI_FLAG_MMAP    (1u << 6)

struct multiboot_info {
    uint32_t flags;
    uint32_t mem_lower, mem_upper;     /* KiB */
    uint32_t boot_device;
    uint32_t cmdline;
    uint32_t mods_count, mods_addr;
    uint32_t syms[4];
    uint32_t mmap_length, mmap_addr;
} __attribute__((packed));

struct multiboot_mmap_entry {
    uint32_t size;                     /* size of this entry excluding this field */
    uint64_t addr;
    uint64_t len;
    uint32_t type;                     /* 1 = available RAM */
} __attribute__((packed));

#define MULTIBOOT_MEMORY_AVAILABLE 1
#endif
