/* Fixed flat layout (D-41d): header + code + rodata read-only at 0x40000000, then data + bss on the next
 * page-aligned pages (writable). The kernel maps [0, _ro_end) RO and [_ro_end, _mem_end) RW. */
ENTRY(_start)
OUTPUT_FORMAT(elf32-i386)
SECTIONS
{
    . = 0x40000000;
    .hdr    : { KEEP(*(.hdr)) }
    .text   : { *(.text .text.*) }
    .rodata : { *(.rodata .rodata.*) }
    . = ALIGN(4K);
    _ro_end = .;
    .data   : { *(.data .data.*) }
    .bss    : { *(COMMON) *(.bss .bss.*) }
    . = ALIGN(4K);
    _mem_end = .;
    /DISCARD/ : { *(.eh_frame) *(.comment) *(.note*) }
}
