/* Ring-3 entry and the kernel-loop context used to abandon a program (D-41b).
 *
 * void user_enter(uint32_t entry, uint32_t user_esp)      -- never returns
 * int  kctx_save(struct kctx *c)                           -- returns 0 now, `val` when resumed
 * void kctx_resume(struct kctx *c, int val)                -- never returns
 *
 * struct kctx { uint32_t esp, ebp, ebx, esi, edi, eip; } */

    .section .text

    .globl user_enter
    .type user_enter, @function
user_enter:
    movl 4(%esp), %ecx            /* entry */
    movl 8(%esp), %edx            /* user stack pointer */
    movw $0x23, %ax               /* user data selector, RPL 3 */
    movw %ax, %ds
    movw %ax, %es
    movw %ax, %fs
    movw %ax, %gs
    pushl $0x23                   /* ss */
    pushl %edx                    /* esp */
    pushl $0x00000202             /* eflags: IF = 1, IOPL = 0, reserved bit 1 */
    pushl $0x1B                   /* cs: user code selector, RPL 3 */
    pushl %ecx                    /* eip */
    xorl %eax, %eax               /* nothing from the kernel leaks through registers */
    xorl %ebx, %ebx
    xorl %ecx, %ecx
    xorl %edx, %edx
    xorl %esi, %esi
    xorl %edi, %edi
    xorl %ebp, %ebp
    iret
    .size user_enter, . - user_enter

    .globl kctx_save
    .type kctx_save, @function
kctx_save:
    movl 4(%esp), %eax
    leal 4(%esp), %ecx            /* esp as it will be after our ret */
    movl %ecx, 0(%eax)
    movl %ebp, 4(%eax)
    movl %ebx, 8(%eax)
    movl %esi, 12(%eax)
    movl %edi, 16(%eax)
    movl (%esp), %ecx             /* return address */
    movl %ecx, 20(%eax)
    xorl %eax, %eax
    ret
    .size kctx_save, . - kctx_save

    .globl kctx_resume
    .type kctx_resume, @function
kctx_resume:
    movl 4(%esp), %eax
    movl 8(%esp), %edx
    movl 0(%eax), %esp
    movl 4(%eax), %ebp
    movl 8(%eax), %ebx
    movl 12(%eax), %esi
    movl 16(%eax), %edi
    movl 20(%eax), %ecx
    movl %edx, %eax
    jmp *%ecx
    .size kctx_resume, . - kctx_resume
