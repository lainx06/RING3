/* Multiboot v1 entry (D-02). QEMU -kernel loads the ELF and jumps here in 32-bit protected mode with
 * EAX = 0x2BADB002 and EBX = the physical address of the multiboot info structure. Paging off, IF = 0. */

#define MB_MAGIC     0x1BADB002
#define MB_FLAGS     0x00000003          /* page-align modules | provide memory map */
#define MB_CHECKSUM  -(MB_MAGIC + MB_FLAGS)

    .section .multiboot, "a"
    .align 4
    .long MB_MAGIC
    .long MB_FLAGS
    .long MB_CHECKSUM

    .section .bss
    .align 16
    .globl boot_stack_bottom
    .globl boot_stack_top
boot_stack_bottom:
    .skip 16384                          /* 16 KiB boot stack */
boot_stack_top:

    .section .text
    .globl _start
    .type _start, @function
_start:
    cli
    movl $boot_stack_top, %esp
    xorl %ebp, %ebp
    pushl %ebx                           /* mbi */
    pushl %eax                           /* magic */
    call kmain
1:  cli
    hlt
    jmp 1b
    .size _start, . - _start
