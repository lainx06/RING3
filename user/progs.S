/* Embedded user programs: flat binaries in the D-41 layout, built from the sources in user/test/. */
    .section .rodata
    .align 4

.macro UPROG name
    .globl uprog_\name\()_start
    .globl uprog_\name\()_end
uprog_\name\()_start:
    .incbin "build/user/\name\().bin"
uprog_\name\()_end:
    .align 4
.endm

UPROG hello
UPROG kwrite
UPROG ticks
UPROG priv
UPROG badptr
UPROG spin
