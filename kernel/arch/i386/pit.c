/* 8253/8254 PIT channel 0, rate generator at PIT_HZ (100 Hz prov.). */
#include "pit.h"
#include "io.h"
#include "pic.h"

#define PIT_CH0  0x40
#define PIT_CMD  0x43
#define PIT_BASE 1193182u

static volatile uint32_t ticks;

void pit_init(void)
{
    uint16_t div = (uint16_t)(PIT_BASE / PIT_HZ);
    outb(PIT_CMD, 0x34);                     /* channel 0, lo/hi byte, mode 2 (rate generator), binary */
    outb(PIT_CH0, div & 0xFF);
    outb(PIT_CH0, (uint8_t)(div >> 8));
    pic_unmask(0);
}

uint32_t ticks_now(void)
{
    return ticks;
}

void pit_on_tick(void)
{
    ticks++;
}
