/* RING3 kernel: ELF32 i386, Multiboot v1, linked at 1 MiB (D-02). */
ENTRY(_start)
OUTPUT_FORMAT(elf32-i386)

SECTIONS
{
    . = 1M;
    _kernel_start = .;

    .multiboot ALIGN(4) : { KEEP(*(.multiboot)) }
    .text      ALIGN(4K) : { *(.text .text.*) }
    .rodata    ALIGN(4K) : { *(.rodata .rodata.*) }
    .data      ALIGN(4K) : { *(.data .data.*) }
    .bss       ALIGN(4K) : {
        _bss_start = .;
        *(COMMON)
        *(.bss .bss.*)
        _bss_end = .;
    }

    . = ALIGN(4K);
    _kernel_end = .;

    /DISCARD/ : { *(.eh_frame) *(.comment) *(.note*) }
}
