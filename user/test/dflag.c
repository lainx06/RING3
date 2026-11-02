/* Skeptic #3: enter the kernel with DF = 1. The kernel must copy forwards regardless (cld on trap entry). */
#include "libu.h"
int main(void)
{
    static const char msg[] = "dflag: text copied with DF set\n";
    __asm__ volatile("std");
    int r = u_write(msg, sizeof msg - 1);
    __asm__ volatile("cld");
    return r == (int)sizeof msg - 1 ? 0 : 1;
}
