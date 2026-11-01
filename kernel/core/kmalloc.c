#include "kmalloc.h"
#include "kstring.h"
#include "panic.h"

#define ARENA_SIZE (256u * 1024)        /* (prov.) */
#define ALIGN 16u
#define MAGIC_USED 0x55534544u          /* "USED" */
#define MAGIC_FREE 0x46524545u          /* "FREE" */

struct hdr {
    uint32_t magic;
    uint32_t size;                      /* payload bytes, multiple of ALIGN */
    struct hdr *next;                   /* address order */
    uint32_t pad;
};

static uint8_t arena[ARENA_SIZE] __attribute__((aligned(16)));
static struct hdr *head;

static inline uint8_t *payload(struct hdr *h) { return (uint8_t *)(h + 1); }
static inline struct hdr *hdr_of(void *p) { return (struct hdr *)p - 1; }

void kmalloc_init(void)
{
    head = (struct hdr *)arena;
    head->magic = MAGIC_FREE;
    head->size = ARENA_SIZE - sizeof(struct hdr);
    head->next = 0;
    head->pad = 0;
}

void *kmalloc(size_t n)
{
    if (n == 0 || n > ARENA_SIZE)
        return 0;
    uint32_t want = (uint32_t)((n + ALIGN - 1) & ~(ALIGN - 1));
    for (struct hdr *h = head; h; h = h->next) {
        if (h->magic != MAGIC_FREE && h->magic != MAGIC_USED)
            panic("kmalloc: corrupt header at %p", (void *)h);
        if (h->magic != MAGIC_FREE || h->size < want)
            continue;
        if (h->size >= want + sizeof(struct hdr) + ALIGN) {       /* split */
            struct hdr *rest = (struct hdr *)(payload(h) + want);
            rest->magic = MAGIC_FREE;
            rest->size = h->size - want - sizeof(struct hdr);
            rest->next = h->next;
            rest->pad = 0;
            h->next = rest;
            h->size = want;
        }
        h->magic = MAGIC_USED;
        return payload(h);
    }
    return 0;
}

void kfree(void *p)
{
    if (!p)
        return;
    if ((uint8_t *)p < arena + sizeof(struct hdr) || (uint8_t *)p >= arena + ARENA_SIZE)
        panic("kfree: %p is not from the kernel heap", p);
    struct hdr *h = hdr_of(p);
    if (h->magic == MAGIC_FREE)
        panic("kfree: double free of %p", p);
    if (h->magic != MAGIC_USED)
        panic("kfree: corrupt header for %p", p);
    h->magic = MAGIC_FREE;
    /* coalesce with the following block, then with the preceding one */
    struct hdr *prev = 0;
    for (struct hdr *c = head; c && c != h; c = c->next)
        prev = c;
    if (h->next && h->next->magic == MAGIC_FREE) {
        h->size += sizeof(struct hdr) + h->next->size;
        h->next = h->next->next;
    }
    if (prev && prev->magic == MAGIC_FREE) {
        prev->size += sizeof(struct hdr) + h->size;
        prev->next = h->next;
        h->magic = 0;
    }
}

size_t kmalloc_free_bytes(void)
{
    size_t t = 0;
    for (struct hdr *h = head; h; h = h->next)
        if (h->magic == MAGIC_FREE)
            t += h->size;
    return t;
}

size_t kmalloc_largest_free(void)
{
    size_t m = 0;
    for (struct hdr *h = head; h; h = h->next)
        if (h->magic == MAGIC_FREE && h->size > m)
            m = h->size;
    return m;
}
