#ifndef RING3_OBJSTORE_H
#define RING3_OBJSTORE_H
#include <stdint.h>
/* Flat object store (D-26): OBJ_SLOTS named slots of at most OBJ_DATA_MAX bytes, rules in kernel/policy/. */
void objstore_init(void);
uint32_t objstore_len(uint32_t id);
/* Copies at most `cap` bytes of object `id` into dst; returns the number copied. */
uint32_t objstore_read(uint32_t id, void *dst, uint32_t cap);
/* Replaces the contents of object `id` (len <= OBJ_DATA_MAX). */
void objstore_write(uint32_t id, const void *src, uint32_t len);
/* Text listing "id name trust rw\n" per object; returns the length or -EINVAL if `cap` is too small. */
int objstore_list(char *dst, uint32_t cap);
/* Appends a line to audit_log, dropping the oldest lines when full (kernel-written, bounded). */
void objstore_audit_append(const char *line);
#endif
