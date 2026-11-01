/* Image header read by kernel/core/exec.c, then the entry point. Stack and zeroed bss come from the kernel. */
    .section .hdr, "a"
    .long 0x33474E52                 /* "RNG3" */
    .long _ro_end - 0x40000000
    .long _mem_end - 0x40000000
    .long _start - 0x40000000

    .section .text
    .globl _start
    .type _start, @function
_start:
    xorl %ebp, %ebp
    call main
    movl %eax, %ebx                  /* exit(main()) */
    movl $0, %eax
    int $0x80
1:  jmp 1b
    .size _start, . - _start
