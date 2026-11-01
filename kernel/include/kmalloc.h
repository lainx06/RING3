#ifndef RING3_KMALLOC_H
#define RING3_KMALLOC_H
#include <stddef.h>
#include <stdint.h>
/* Kernel heap over a fixed arena: 16-byte aligned blocks with headers, first-fit, coalescing on free,
 * double-free and corruption detection (panic = kernel bug, INV-12). */
void kmalloc_init(void);
void *kmalloc(size_t n);
void kfree(void *p);
size_t kmalloc_free_bytes(void);
size_t kmalloc_largest_free(void);
#endif
