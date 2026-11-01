#ifndef RING3_PIT_H
#define RING3_PIT_H
#include <stdint.h>
#define PIT_HZ 100   /* (prov.) */
void pit_init(void);
uint32_t ticks_now(void);
void pit_on_tick(void);              /* called by the IRQ 0 path */
#endif
