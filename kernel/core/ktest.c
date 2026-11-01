/* In-kernel unit tests (A3). Built only with KTEST=1; run with the `ktest` boot option. */
#include "ktest.h"
#ifdef KTEST
#include "cobs.h"
#include "crc16.h"
#include "exec.h"
#include "kctx.h"
#include "kmalloc.h"
#include "kstring.h"
#include "multiboot.h"
#include "paging.h"
#include "panic.h"
#include "pmm.h"
#include "printk.h"
#include "qemu.h"
#include "syscall.h"
#include "uaccess.h"

static int pass_count, fail_count, cur_failed;
static const char *cur_name;

#define CHECK(cond)                                                                                  \
    do {                                                                                             \
        if (!(cond)) {                                                                               \
            printk("  check failed: %s (%s:%d)\n", #cond, __FILE__, __LINE__);                      \
            cur_failed = 1;                                                                          \
        }                                                                                            \
    } while (0)

static void begin(const char *name)
{
    cur_name = name;
    cur_failed = 0;
}

static void end(void)
{
    printk("KTEST %s %s\n", cur_name, cur_failed ? "FAIL" : "PASS");
    if (cur_failed)
        fail_count++;
    else
        pass_count++;
}

/* ---- crc16 ---- */
static void t_crc16(void)
{
    begin("crc16_check_value");
    CHECK(crc16_ccitt_false((const uint8_t *)"123456789", 9) == 0x29B1);
    CHECK(crc16_ccitt_false((const uint8_t *)"", 0) == 0xFFFF);
    uint8_t a[] = { 0x00, 0x00 };
    CHECK(crc16_ccitt_false(a, 2) == 0x1D0F);                   /* known value for two zero bytes */
    uint16_t inc = crc16_update(crc16_update(0xFFFF, (const uint8_t *)"1234", 4), (const uint8_t *)"56789", 5);
    CHECK(inc == 0x29B1);
    end();
}

/* ---- cobs ---- */
static uint8_t enc[COBS_ENC_MAX(COBS_MAX_PAYLOAD) + 8];
static uint8_t dec[COBS_MAX_PAYLOAD + 8];
static uint8_t src[COBS_MAX_PAYLOAD + 8];

static int roundtrip(const uint8_t *in, size_t n)
{
    size_t e = cobs_encode(in, n, enc, sizeof enc);
    if (e == 0 || e > COBS_ENC_MAX(n))
        return 0;
    for (size_t i = 0; i < e; i++)
        if (enc[i] == 0)
            return 0;
    size_t d = cobs_decode(enc, e, dec, sizeof dec);
    return d == n && memcmp(dec, in, n) == 0;
}

static void t_cobs(void)
{
    begin("cobs_zero_bytes");
    {
        const uint8_t z1[] = { 0 };
        const uint8_t z2[] = { 0, 0 };
        const uint8_t z3[] = { 1, 0, 2 };
        const uint8_t z4[] = { 0, 1, 2, 0 };
        const uint8_t z5[] = { 0x11, 0x22, 0x00, 0x33 };
        CHECK(roundtrip(z1, 1));
        CHECK(roundtrip(z2, 2));
        CHECK(roundtrip(z3, 3));
        CHECK(roundtrip(z4, 4));
        CHECK(roundtrip(z5, 4));
        CHECK(roundtrip(z5, 0));
        /* known encoding: 11 22 00 33 -> 03 11 22 02 33 */
        size_t e = cobs_encode(z5, 4, enc, sizeof enc);
        CHECK(e == 5 && enc[0] == 3 && enc[1] == 0x11 && enc[2] == 0x22 && enc[3] == 2 && enc[4] == 0x33);
    }
    end();

    begin("cobs_runs_253_254_255");
    for (size_t i = 0; i < sizeof src; i++)
        src[i] = (uint8_t)(1 + (i % 255));                       /* never zero */
    CHECK(roundtrip(src, 253));
    CHECK(roundtrip(src, 254));
    CHECK(roundtrip(src, 255));
    CHECK(cobs_encode(src, 254, enc, sizeof enc) == 255);         /* 254 non-zero bytes: one code byte */
    CHECK(cobs_encode(src, 255, enc, sizeof enc) == 257);         /* 255: two groups */
    /* zeros around the 254 boundary */
    src[254] = 0;
    CHECK(roundtrip(src, 255));
    CHECK(roundtrip(src, 256));
    src[253] = 0;
    CHECK(roundtrip(src, 255));
    end();

    begin("cobs_max_frame");
    for (size_t i = 0; i < COBS_MAX_PAYLOAD; i++)
        src[i] = (uint8_t)(i * 7 + 3);                             /* includes zeros */
    CHECK(roundtrip(src, COBS_MAX_PAYLOAD));
    for (size_t i = 0; i < COBS_MAX_PAYLOAD; i++)
        src[i] = 0xAB;                                             /* no zeros: max overhead path */
    CHECK(roundtrip(src, COBS_MAX_PAYLOAD));
    CHECK(cobs_encode(src, COBS_MAX_PAYLOAD, enc, COBS_MAX_PAYLOAD) == 0);   /* too small output */
    end();

    begin("cobs_rejects_malformed");
    {
        const uint8_t bad_zero[] = { 3, 0x11, 0x00 };
        const uint8_t bad_len[] = { 5, 0x11, 0x22 };               /* code claims 4 bytes, 2 present */
        const uint8_t zero_code[] = { 0, 1 };
        CHECK(cobs_decode(bad_zero, 3, dec, sizeof dec) == (size_t)-1);
        CHECK(cobs_decode(bad_len, 3, dec, sizeof dec) == (size_t)-1);
        CHECK(cobs_decode(zero_code, 2, dec, sizeof dec) == (size_t)-1);
        CHECK(cobs_decode(enc, 0, dec, sizeof dec) == 0);
        size_t e = cobs_encode(src, 64, enc, sizeof enc);
        CHECK(cobs_decode(enc, e, dec, 10) == (size_t)-1);         /* output too small */
    }
    end();
}

/* ---- frame allocator ---- */
static void t_pmm(void)
{
    begin("pmm_alloc_free");
    uint32_t before = pmm_free_frames();
    uint32_t f[32];
    int distinct = 1, aligned = 1, used = 1;
    for (int i = 0; i < 32; i++) {
        f[i] = pmm_alloc();
        if (!f[i] || (f[i] & 0xFFF))
            aligned = 0;
        if (!pmm_is_used(f[i]))
            used = 0;
        for (int j = 0; j < i; j++)
            if (f[j] == f[i])
                distinct = 0;
    }
    CHECK(aligned);
    CHECK(used);
    CHECK(distinct);                                               /* no double allocation */
    CHECK(pmm_free_frames() == before - 32);
    for (int i = 0; i < 32; i++)
        pmm_free(f[i]);
    CHECK(pmm_free_frames() == before);
    CHECK(!pmm_is_used(f[0]));
    CHECK(pmm_alloc() == f[0]);                                    /* lowest free frame first */
    pmm_free(f[0]);
    CHECK(pmm_is_used(0x00100000));                                /* kernel image reserved */
    CHECK(pmm_is_used(0x00000000));                                /* low memory reserved */
    end();

    begin("pmm_double_free_detected");
    {
        struct kctx trap;
        uint32_t x = pmm_alloc();
        pmm_free(x);
        int caught = 0;
        if (kctx_save(&trap) == 0) {
            panic_arm_test_trap(&trap);
            pmm_free(x);                                           /* must panic */
            panic_disarm_test_trap();
        } else {
            caught = 1;
        }
        CHECK(caught);
        CHECK(pmm_free_frames() == before);
    }
    end();
}

/* ---- memory map parsing ---- */
static void t_mmap(void)
{
    begin("mmap_parsing");
    struct multiboot_mmap_entry map[4] __attribute__((aligned(4)));
    const uint64_t MiB = 1024 * 1024;
    map[0] = (struct multiboot_mmap_entry){ 20, 0, 640 * 1024, MULTIBOOT_MEMORY_AVAILABLE };
    map[1] = (struct multiboot_mmap_entry){ 20, 1 * MiB, 14 * MiB, MULTIBOOT_MEMORY_AVAILABLE };
    map[2] = (struct multiboot_mmap_entry){ 20, 15 * MiB, 1 * MiB, 2 };                 /* reserved */
    map[3] = (struct multiboot_mmap_entry){ 20, 16 * MiB, 48 * MiB, MULTIBOOT_MEMORY_AVAILABLE };
    struct multiboot_info mbi;
    memset(&mbi, 0, sizeof mbi);
    mbi.flags = MBI_FLAG_MMAP;
    mbi.mmap_addr = (uint32_t)(uintptr_t)map;
    mbi.mmap_length = sizeof map;
    CHECK(pmm_mmap_usable_kib(&mbi) == 640 + 14 * 1024 + 48 * 1024);
    CHECK(pmm_mmap_window_frames(&mbi) == 14 * 256);               /* only [1 MiB, 15 MiB) is in the window */
    mbi.flags = 0;
    CHECK(pmm_mmap_usable_kib(&mbi) == 0);
    /* unaligned entry: 1 MiB + 100 .. : partial pages are dropped */
    map[1].addr = 1 * MiB + 100;
    map[1].len = 2 * MiB;
    mbi.flags = MBI_FLAG_MMAP;
    mbi.mmap_length = 2 * sizeof map[0];
    CHECK(pmm_mmap_window_frames(&mbi) == 2 * 256 - 1);
    end();
}

/* ---- kmalloc ---- */
static void t_kmalloc(void)
{
    begin("kmalloc_reuse_after_free");
    size_t free0 = kmalloc_free_bytes();
    void *a = kmalloc(100);
    void *b = kmalloc(200);
    CHECK(a && b && a != b);
    CHECK(((uintptr_t)a & 15) == 0 && ((uintptr_t)b & 15) == 0);
    memset(a, 0x5A, 100);
    memset(b, 0xA5, 200);
    CHECK(((uint8_t *)a)[99] == 0x5A && ((uint8_t *)b)[0] == 0xA5);
    kfree(a);
    void *c = kmalloc(100);
    CHECK(c == a);                                                  /* freed block reused */
    kfree(c);
    kfree(b);
    CHECK(kmalloc_free_bytes() == free0);                           /* coalesced back */
    CHECK(kmalloc_largest_free() == free0);
    CHECK(kmalloc(0) == 0);
    CHECK(kmalloc(1u << 30) == 0);
    void *d = kmalloc(1);
    CHECK(d != 0);
    kfree(d);
    CHECK(kmalloc_free_bytes() == free0);
    end();

    begin("kmalloc_double_free_detected");
    {
        struct kctx trap;
        void *x = kmalloc(64);
        kfree(x);
        int caught = 0;
        if (kctx_save(&trap) == 0) {
            panic_arm_test_trap(&trap);
            kfree(x);                                               /* must panic */
            panic_disarm_test_trap();
        } else {
            caught = 1;
        }
        CHECK(caught);
    }
    end();
}

/* ---- uaccess (no program running: nothing user-mapped except what this test maps) ---- */
static void t_uaccess(void)
{
    begin("uaccess_bounds");
    CHECK(uaccess_check(0x00100000, 16, 0) == -EFAULT);             /* kernel address */
    CHECK(uaccess_check(0xC0000000, 16, 0) == -EFAULT);             /* above USER_TOP */
    CHECK(uaccess_check(USER_TOP, 1, 0) == -EFAULT);                /* at the top: nothing mapped there */
    CHECK(uaccess_check(USER_TOP - 4, 8, 0) == -EFAULT);            /* crosses USER_TOP */
    CHECK(uaccess_check(USER_TOP + 4, 0xFFFFFFF8u, 0) == -EFAULT);  /* would wrap if subtracted first */
    CHECK(uaccess_check(USER_BASE, MAX_SYSCALL_BUF + 1, 0) == -EFAULT);
    CHECK(uaccess_check(USER_BASE, 16, 0) == -EFAULT);              /* unmapped */
    uint32_t f = pmm_alloc();
    paging_map_user(USER_BASE, f, 0);
    paging_flush_tlb();
    CHECK(uaccess_check(USER_BASE, 16, 0) == 0);
    CHECK(uaccess_check(USER_BASE, PAGE_SIZE, 0) == 0);
    CHECK(uaccess_check(USER_BASE, PAGE_SIZE + 1, 0) == -EFAULT);   /* runs into the next, unmapped page */
    CHECK(uaccess_check(USER_BASE + PAGE_SIZE - 1, 2, 0) == -EFAULT);
    CHECK(uaccess_check(USER_BASE, 16, 1) == -EFAULT);              /* read-only page, write wanted */
    CHECK(paging_unmap_user(USER_BASE) == f);
    paging_flush_tlb();
    CHECK(uaccess_check(USER_BASE, 16, 0) == -EFAULT);
    pmm_free(f);
    end();
}

/* ---- lib ---- */
static void t_lib(void)
{
    begin("string_helpers");
    char buf[16];
    memcpy(buf, "abcdefghijklmno", 16);
    memmove(buf + 2, buf, 8);
    CHECK(memcmp(buf, "ababcdefghklmno", 16) == 0);
    memmove(buf, buf + 2, 8);
    CHECK(memcmp(buf, "abcdefghghklmno", 16) == 0);
    CHECK(strlen("") == 0 && strlen("ring3") == 5);
    CHECK(strncmp("abc", "abd", 2) == 0 && strncmp("abc", "abd", 3) < 0);
    strncpy(buf, "hi", 5);
    CHECK(buf[2] == 0 && buf[4] == 0 && buf[5] == 'f');
    CHECK(str_has_word("ktest testexit", "testexit") && !str_has_word("ktest testexit", "test"));
    CHECK(!str_has_word("", "x") && str_has_word("  a  b ", "a"));
    end();

    begin("printk_formats");
    char out[64];
    int n = ksnprintf(out, sizeof out, "%s|%c|%d|%u|%x|%08x|%p|%%|%5d", "s", 'c', -42, 3000000000u, 0xBEEF,
                      0x1A, (void *)0x1000, 7);
    CHECK(strncmp(out, "s|c|-42|3000000000|beef|0000001a|0x00001000|%|    7", sizeof out) == 0);
    CHECK(n == (int)strlen(out));
    n = ksnprintf(out, 4, "%d", 123456);
    CHECK(n == 6 && strncmp(out, "123", 4) == 0);
    end();
}

void ktest_run_all(void)
{
    printk("KTEST begin\n");
    t_crc16();
    t_cobs();
    t_pmm();
    t_mmap();
    t_kmalloc();
    t_uaccess();
    t_lib();
    printk("KTEST SUMMARY pass=%d fail=%d\n", pass_count, fail_count);
    qemu_exit(fail_count ? QEMU_EXIT_FAIL : QEMU_EXIT_PASS);
    for (;;)
        __asm__ volatile("hlt");
}
#else
typedef int ktest_not_built_in_this_configuration;
#endif
