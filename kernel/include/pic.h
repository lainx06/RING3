#ifndef RING3_PIC_H
#define RING3_PIC_H
#include <stdint.h>
void pic_init(void);                 /* remap to 0x20/0x28, mask everything */
void pic_unmask(uint8_t irq);
void pic_mask(uint8_t irq);
void pic_eoi(uint8_t irq);
#endif
