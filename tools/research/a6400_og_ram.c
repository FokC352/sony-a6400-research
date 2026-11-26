/* Fail-closed RAM tester for ILCE-6400 squeezed 3:2 Open Gate (E0695/E0696).
 *
 * Maps individual 4 KiB /dev/mem pages around every access with a SIGBUS
 * guard, verifies LIRO_HDR + all 146 entries before any write, issues a DSB,
 * and reads back every written byte. No automatic retries or unguarded sweeps.
 */
#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <fcntl.h>
#include <setjmp.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#include "a6400_og_ram_table.h"

#define PAGE_SIZE_BYTES 4096u
#define PAGE_MASK_BYTES (PAGE_SIZE_BYTES - 1u)

static sigjmp_buf g_bus_env;
static volatile sig_atomic_t g_bus_armed = 0;

static void bus_handler(int sig) {
    (void)sig;
    if (g_bus_armed) {
        siglongjmp(g_bus_env, 1);
    }
    _exit(128 + SIGBUS);
}

static int install_sigbus_handler(void) {
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = bus_handler;
    sigemptyset(&sa.sa_mask);
    if (sigaction(SIGBUS, &sa, NULL) != 0) {
        perror("sigaction");
        return -1;
    }
    return 0;
}

static int read_phys_bytes(int fd, uint32_t pa, void *dst, size_t len) {
    uint32_t page_pa = pa & ~PAGE_MASK_BYTES;
    uint32_t off = pa & PAGE_MASK_BYTES;
    if (off + len > PAGE_SIZE_BYTES) {
        fprintf(stderr, "read crosses page boundary at 0x%08x\n", pa);
        return -EINVAL;
    }
    void *map = mmap(NULL, PAGE_SIZE_BYTES, PROT_READ, MAP_SHARED, fd, (off_t)page_pa);
    if (map == MAP_FAILED) {
        fprintf(stderr, "mmap read 0x%08x failed: %s\n", pa, strerror(errno));
        return -errno;
    }
    volatile uint8_t *src = (volatile uint8_t *)map + off;
    uint8_t *out = (uint8_t *)dst;
    g_bus_armed = 1;
    if (sigsetjmp(g_bus_env, 1) != 0) {
        g_bus_armed = 0;
        munmap(map, PAGE_SIZE_BYTES);
        fprintf(stderr, "SIGBUS reading 0x%08x\n", pa);
        return -EFAULT;
    }
    for (size_t i = 0; i < len; ++i) {
        out[i] = src[i];
    }
    g_bus_armed = 0;
    munmap(map, PAGE_SIZE_BYTES);
    return 0;
}

static int write_phys_bytes(int fd, uint32_t pa, const void *src_bytes, size_t len) {
    uint32_t page_pa = pa & ~PAGE_MASK_BYTES;
    uint32_t off = pa & PAGE_MASK_BYTES;
    if (off + len > PAGE_SIZE_BYTES) {
        fprintf(stderr, "write crosses page boundary at 0x%08x\n", pa);
        return -EINVAL;
    }
    void *map = mmap(NULL, PAGE_SIZE_BYTES, PROT_READ | PROT_WRITE, MAP_SHARED, fd, (off_t)page_pa);
    if (map == MAP_FAILED) {
        fprintf(stderr, "mmap write 0x%08x failed: %s\n", pa, strerror(errno));
        return -errno;
    }
    volatile uint8_t *dst = (volatile uint8_t *)map + off;
    const uint8_t *in = (const uint8_t *)src_bytes;
    g_bus_armed = 1;
    if (sigsetjmp(g_bus_env, 1) != 0) {
        g_bus_armed = 0;
        munmap(map, PAGE_SIZE_BYTES);
        fprintf(stderr, "SIGBUS writing 0x%08x\n", pa);
        return -EFAULT;
    }
    for (size_t i = 0; i < len; ++i) {
        dst[i] = in[i];
    }
    __asm__ volatile("dsb" ::: "memory");
    uint8_t rb[8];
    for (size_t i = 0; i < len; ++i) {
        rb[i] = dst[i];
    }
    g_bus_armed = 0;
    munmap(map, PAGE_SIZE_BYTES);
    if (memcmp(rb, in, len) != 0) {
        fprintf(stderr, "readback mismatch at 0x%08x\n", pa);
        return -EIO;
    }
    return 0;
}

static int check_liro_header(int fd) {
    uint8_t hdr[16];
    int rc = read_phys_bytes(fd, AVCAM_PHYS_BASE, hdr, sizeof(hdr));
    if (rc != 0) {
        return rc;
    }
    if (memcmp(hdr, LIRO_HDR, sizeof(hdr)) != 0) {
        printf("LIRO_HDR: MISMATCH got=");
        for (size_t i = 0; i < sizeof(hdr); ++i) {
            printf("%02x", hdr[i]);
        }
        printf(" exp=");
        for (size_t i = 0; i < sizeof(hdr); ++i) {
            printf("%02x", LIRO_HDR[i]);
        }
        printf("\n");
        return -1;
    }
    printf("LIRO_HDR: MATCH @ 0x%08x\n", AVCAM_PHYS_BASE);
    return 0;
}

static uint32_t read_le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint16_t read_le16(const uint8_t *p) {
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static void print_hex(const uint8_t *buf, uint8_t len) {
    for (uint8_t i = 0; i < len; ++i) {
        printf("%02x", buf[i]);
    }
}

static void print_rt_diagnostics(int fd) {
    uint8_t buf[16];
    uint32_t h1 = 0, v1 = 0, h2 = 0, v2 = 0;
    if (read_phys_bytes(fd, RT_T14_M16_PA + 0x08u, buf, 8) == 0) {
        h1 = read_le32(buf);
        v1 = read_le32(buf + 4);
    }
    if (read_phys_bytes(fd, RT_T14_M16_PA + 0x68u, buf, 8) == 0) {
        h2 = read_le32(buf);
        v2 = read_le32(buf + 4);
    }
    printf("rt_t14[16] (24p): hv1=(%u,%u) hv2=(%u,%u)\n", h1, v1, h2, v2);

    h1 = v1 = h2 = v2 = 0;
    if (read_phys_bytes(fd, RT_T14_M17_PA + 0x08u, buf, 8) == 0) {
        h1 = read_le32(buf);
        v1 = read_le32(buf + 4);
    }
    if (read_phys_bytes(fd, RT_T14_M17_PA + 0x68u, buf, 8) == 0) {
        h2 = read_le32(buf);
        v2 = read_le32(buf + 4);
    }
    printf("rt_t14[17] (25p): hv1=(%u,%u) hv2=(%u,%u)\n", h1, v1, h2, v2);

    uint32_t vact16 = 0, vact17 = 0;
    if (read_phys_bytes(fd, RT_T14_M16_PA + 0x50u, buf, 4) == 0) vact16 = read_le32(buf);
    if (read_phys_bytes(fd, RT_T14_M17_PA + 0x50u, buf, 4) == 0) vact17 = read_le32(buf);

    uint8_t u12[2] = {0, 0}, u24 = 0;
    if (read_phys_bytes(fd, RT_T10_M16_PA + 0x12u, u12, 2) == 0 &&
        read_phys_bytes(fd, RT_T10_M16_PA + 0x24u, &u24, 1) == 0) {
        printf("rt_t10[16] (24p): +0x12=%u +0x24=%u (0x%02x) V_ACTIVE=%u\n", read_le16(u12), u24, u24, vact16);
    }
    if (read_phys_bytes(fd, RT_T10_M17_PA + 0x12u, u12, 2) == 0 &&
        read_phys_bytes(fd, RT_T10_M17_PA + 0x24u, &u24, 1) == 0) {
        printf("rt_t10[17] (25p): +0x12=%u +0x24=%u (0x%02x) V_ACTIVE=%u\n", read_le16(u12), u24, u24, vact17);
    }
}

static int cmd_dump(int fd) {
    if (check_liro_header(fd) != 0) {
        return 1;
    }
    uint32_t m_st = 0, m_t1 = 0, m_t4 = 0, m_t3 = 0, m_t2 = 0, m_ts = 0, m_p24 = 0, m_p8 = 0, m_other = 0;
    uint32_t printed_other = 0;
    for (uint32_t i = 0; i < N_OG_PATCHES; ++i) {
        const struct og_patch *p = &OG_PATCHES[i];
        uint8_t cur[8] = {0};
        if (read_phys_bytes(fd, p->pa, cur, p->len) != 0) {
            return 1;
        }
        int is_st = memcmp(cur, p->stock, p->len) == 0;
        int is_t1 = memcmp(cur, p->t1, p->len) == 0;
        int is_t4 = memcmp(cur, p->t4, p->len) == 0;
        int is_t3 = memcmp(cur, p->t3, p->len) == 0;
        int is_t2 = memcmp(cur, p->t2, p->len) == 0;
        int is_ts = memcmp(cur, p->ts, p->len) == 0;
        int is_p24 = memcmp(cur, p->tp24, p->len) == 0;
        int is_p8 = memcmp(cur, p->tp8, p->len) == 0;
        if (is_st) ++m_st;
        if (is_t1) ++m_t1;
        if (is_t4) ++m_t4;
        if (is_t3) ++m_t3;
        if (is_t2) ++m_t2;
        if (is_ts) ++m_ts;
        if (is_p24) ++m_p24;
        if (is_p8) ++m_p8;
        if (!is_st && !is_t1 && !is_t4 && !is_t3 && !is_t2 && !is_ts && !is_p24 && !is_p8) {
            ++m_other;
            if (printed_other < 8) {
                printf("OTHER[%u] pa=0x%08x len=%u got=", i, p->pa, p->len);
                print_hex(cur, p->len);
                printf(" stock=");
                print_hex(p->stock, p->len);
                printf("\n");
                ++printed_other;
            }
        }
    }
    print_rt_diagnostics(fd);
    printf("COUNTS: total=%u stock=%u t1=%u t4=%u t3=%u t2=%u shrink=%u plus24=%u plus8=%u other=%u\n",
           N_OG_PATCHES, m_st, m_t1, m_t4, m_t3, m_t2, m_ts, m_p24, m_p8, m_other);
    const char *verdict = "MIXED";
    if (m_st == N_OG_PATCHES) verdict = "STOCK";
    else if (m_t1 == N_OG_PATCHES) verdict = "TEST1";
    else if (m_t4 == N_OG_PATCHES) verdict = "TEST4";
    else if (m_t3 == N_OG_PATCHES) verdict = "TEST3";
    else if (m_t2 == N_OG_PATCHES) verdict = "TEST2";
    else if (m_ts == N_OG_PATCHES) verdict = "SHRINK";
    else if (m_p24 == N_OG_PATCHES) verdict = "PLUS24";
    else if (m_p8 == N_OG_PATCHES) verdict = "PLUS8";
    printf("VERDICT: %s\n", verdict);
    return (m_other == 0) ? 0 : 2;
}

static const uint8_t *pick_step_bytes(uint32_t idx, const struct og_patch *p, const char *step) {
    if (strcmp(step, "test1") == 0) return p->t1;
    if (strcmp(step, "test4") == 0) return p->t4;
    if (strcmp(step, "test3") == 0) return p->t3;
    if (strcmp(step, "test2") == 0) return p->t2;
    if (strcmp(step, "shrink") == 0) return p->ts;
    if (strcmp(step, "plus24") == 0) return p->tp24;
    if (strcmp(step, "plus8") == 0) return p->tp8;
    if (strcmp(step, "plus24_norcv") == 0) return (idx >= 146u && idx < 152u) ? p->stock : p->tp24;
    if (strcmp(step, "plus24_rcvonly") == 0) return (idx < 4u || (idx >= 146u && idx < 152u)) ? p->tp24 : p->stock;
    if (strcmp(step, "plus24_szonly") == 0) return (idx < 4u || (idx >= 144u && idx < 146u) || (idx >= 156u && idx < 158u)) ? p->tp24 : p->stock;
    int is_timing = ((idx >= 136u && idx < 144u) || (idx >= 152u && idx < 156u));
    if (strcmp(step, "timing4") == 0) return is_timing ? p->t4 : p->stock;
    if (strcmp(step, "fe4") == 0) return (!is_timing) ? p->t4 : p->stock;
    return NULL;
}

static int apply_selected_targets(int fd, const uint8_t *const targets[N_OG_PATCHES], const char *label) {
    if (check_liro_header(fd) != 0) {
        return 1;
    }
    uint8_t cur_vals[N_OG_PATCHES][8];
    for (uint32_t i = 0; i < N_OG_PATCHES; ++i) {
        const struct og_patch *p = &OG_PATCHES[i];
        if (read_phys_bytes(fd, p->pa, cur_vals[i], p->len) != 0) {
            return 1;
        }
        if (memcmp(cur_vals[i], p->stock, p->len) != 0 &&
            memcmp(cur_vals[i], targets[i], p->len) != 0) {
            printf("ABORT_APPLY[%u] pa=0x%08x not stock/target got=", i, p->pa);
            print_hex(cur_vals[i], p->len);
            printf("\n");
            return 3;
        }
    }
    uint32_t written = 0, unchanged = 0;
    for (uint32_t i = 0; i < N_OG_PATCHES; ++i) {
        const struct og_patch *p = &OG_PATCHES[i];
        if (memcmp(cur_vals[i], targets[i], p->len) == 0) {
            ++unchanged;
            continue;
        }
        if (write_phys_bytes(fd, p->pa, targets[i], p->len) != 0) {
            return 4;
        }
        ++written;
    }
    printf("APPLY %s: ok written=%u unchanged=%u\n", label, written, unchanged);
    return 0;
}

static int cmd_apply(int fd, const char *step) {
    const uint8_t *targets[N_OG_PATCHES];
    for (uint32_t i = 0; i < N_OG_PATCHES; ++i) {
        targets[i] = pick_step_bytes(i, &OG_PATCHES[i], step);
        if (targets[i] == NULL) {
            fprintf(stderr, "Unknown apply step '%s' (use test1|test4|test3|test2|shrink|plus24|plus8|plus24_norcv|plus24_rcvonly|plus24_szonly|timing4|fe4)\n", step);
            return 2;
        }
    }
    return apply_selected_targets(fd, targets, step);
}

static int cmd_apply_fe_range(int fd, uint32_t lo, uint32_t hi) {
    if (lo >= hi || hi > 66u) {
        fprintf(stderr, "Invalid FE range [%u..%u) (require 0 <= lo < hi <= 66)\n", lo, hi);
        return 2;
    }
    const uint8_t *targets[N_OG_PATCHES];
    for (uint32_t i = 0; i < N_OG_PATCHES; ++i) {
        const struct og_patch *p = &OG_PATCHES[i];
        int in_range = (i < 4u) ||
                       (i >= 4u + lo && i < 4u + hi) ||
                       (i >= 70u + lo && i < 70u + hi);
        targets[i] = in_range ? p->tp24 : p->stock;
    }
    char label[48];
    snprintf(label, sizeof(label), "plus24_fe [%u..%u)", lo, hi);
    return apply_selected_targets(fd, targets, label);
}

#define FE_REC_0A_194_PA    0x03E15028u
#define FE_REC_19_194_PA    0x03E15FE8u

#define A1_VDF_1A_PA        0x03CAEB1Au
#define A1_VDF_2A_PA        0x03CAEB2Au
static const uint32_t A1_ZIMA_W_PAS[6] = {
    0x03E3B56Cu, 0x03E3B594u, 0x03E3B5BCu,
    0x03E3B5E4u, 0x03E3B60Cu, 0x03E3B634u,
};

static int cmd_apply_a1(int fd) {
    if (check_liro_header(fd) != 0) {
        return 1;
    }
    uint8_t b2[2] = {0, 0};
    if (read_phys_bytes(fd, A1_VDF_1A_PA, b2, 2u) != 0 ||
        (read_le16(b2) != 0x2004u && read_le16(b2) != 0x2006u)) {
        printf("ABORT_A1: unexpected 0x%08x=0x%04x\n", A1_VDF_1A_PA, read_le16(b2));
        return 3;
    }
    if (read_phys_bytes(fd, A1_VDF_2A_PA, b2, 2u) != 0 ||
        (read_le16(b2) != 0x2004u && read_le16(b2) != 0x2006u)) {
        printf("ABORT_A1: unexpected 0x%08x=0x%04x\n", A1_VDF_2A_PA, read_le16(b2));
        return 3;
    }
    for (int i = 0; i < 6; ++i) {
        if (read_phys_bytes(fd, A1_ZIMA_W_PAS[i], b2, 2u) != 0 ||
            (read_le16(b2) != 3840u && read_le16(b2) != 3240u)) {
            printf("ABORT_A1: unexpected zima[%d]=%u\n", i, read_le16(b2));
            return 3;
        }
    }
    const uint8_t vdf_p[2] = {0x06u, 0x20u};
    const uint8_t zima_p[2] = {0xA8u, 0x0Cu}; /* 3240 */
    if (write_phys_bytes(fd, A1_VDF_1A_PA, vdf_p, 2u) != 0 ||
        write_phys_bytes(fd, A1_VDF_2A_PA, vdf_p, 2u) != 0) {
        return 4;
    }
    for (int i = 0; i < 6; ++i) {
        if (write_phys_bytes(fd, A1_ZIMA_W_PAS[i], zima_p, 2u) != 0) {
            return 4;
        }
    }
    printf("APPLY a1: ok vdf=(0x2006,0x2006) zima_w=3240 (6 entries)\n");
    return 0;
}

static int cmd_apply_plus24_r4(int fd) {
    uint8_t cur0[2] = {0, 0}, cur1[2] = {0, 0};
    if (read_phys_bytes(fd, FE_REC_0A_194_PA, cur0, 2u) != 0 ||
        read_phys_bytes(fd, FE_REC_19_194_PA, cur1, 2u) != 0) {
        return 1;
    }
    uint16_t v0 = read_le16(cur0), v1 = read_le16(cur1);
    if ((v0 != 0u && v0 != 24u) || (v1 != 0u && v1 != 24u)) {
        printf("ABORT_R4: unexpected fe_194=(%u,%u)\n", v0, v1);
        return 3;
    }
    int rc = cmd_apply(fd, "plus24");
    if (rc != 0) {
        return rc;
    }
    const uint8_t target[2] = {24u, 0u};
    if (write_phys_bytes(fd, FE_REC_0A_194_PA, target, 2u) != 0 ||
        write_phys_bytes(fd, FE_REC_19_194_PA, target, 2u) != 0) {
        return 4;
    }
    printf("APPLY plus24_r4: ok fe_194=(24,24)\n");
    return 0;
}

static int cmd_putback(int fd) {
    if (check_liro_header(fd) != 0) {
        return 1;
    }
    uint8_t r4_0[2] = {0, 0}, r4_1[2] = {0, 0};
    if (read_phys_bytes(fd, FE_REC_0A_194_PA, r4_0, 2u) != 0 ||
        read_phys_bytes(fd, FE_REC_19_194_PA, r4_1, 2u) != 0) {
        return 1;
    }
    uint16_t rv0 = read_le16(r4_0), rv1 = read_le16(r4_1);
    if ((rv0 != 0u && rv0 != 24u) || (rv1 != 0u && rv1 != 24u)) {
        printf("ABORT_PUTBACK_R4: unexpected fe_194=(%u,%u)\n", rv0, rv1);
        return 3;
    }
    uint8_t cur_vals[N_OG_PATCHES][8];
    for (uint32_t i = 0; i < N_OG_PATCHES; ++i) {
        const struct og_patch *p = &OG_PATCHES[i];
        if (read_phys_bytes(fd, p->pa, cur_vals[i], p->len) != 0) {
            return 1;
        }
        int known = (memcmp(cur_vals[i], p->stock, p->len) == 0) ||
                    (memcmp(cur_vals[i], p->t1, p->len) == 0) ||
                    (memcmp(cur_vals[i], p->t4, p->len) == 0) ||
                    (memcmp(cur_vals[i], p->t3, p->len) == 0) ||
                    (memcmp(cur_vals[i], p->t2, p->len) == 0) ||
                    (memcmp(cur_vals[i], p->ts, p->len) == 0) ||
                    (memcmp(cur_vals[i], p->tp24, p->len) == 0) ||
                    (memcmp(cur_vals[i], p->tp8, p->len) == 0);
        if (!known) {
            printf("ABORT_PUTBACK[%u] pa=0x%08x unknown value=", i, p->pa);
            print_hex(cur_vals[i], p->len);
            printf("\n");
            return 3;
        }
    }
    uint32_t restored = 0, unchanged = 0;
    for (uint32_t i = 0; i < N_OG_PATCHES; ++i) {
        const struct og_patch *p = &OG_PATCHES[i];
        if (memcmp(cur_vals[i], p->stock, p->len) == 0) {
            ++unchanged;
            continue;
        }
        if (write_phys_bytes(fd, p->pa, p->stock, p->len) != 0) {
            return 4;
        }
        ++restored;
    }
    if (rv0 != 0u || rv1 != 0u) {
        const uint8_t zero2[2] = {0u, 0u};
        if (write_phys_bytes(fd, FE_REC_0A_194_PA, zero2, 2u) != 0 ||
            write_phys_bytes(fd, FE_REC_19_194_PA, zero2, 2u) != 0) {
            return 4;
        }
        printf("PUTBACK_R4: restored fe_194=(0,0)\n");
    }
    uint8_t a1_b[2] = {0, 0};
    uint32_t a1_restored = 0;
    if (read_phys_bytes(fd, A1_VDF_1A_PA, a1_b, 2u) == 0 && read_le16(a1_b) == 0x2006u) {
        const uint8_t st[2] = {0x04u, 0x20u};
        if (write_phys_bytes(fd, A1_VDF_1A_PA, st, 2u) != 0) return 4;
        ++a1_restored;
    }
    if (read_phys_bytes(fd, A1_VDF_2A_PA, a1_b, 2u) == 0 && read_le16(a1_b) == 0x2006u) {
        const uint8_t st[2] = {0x04u, 0x20u};
        if (write_phys_bytes(fd, A1_VDF_2A_PA, st, 2u) != 0) return 4;
        ++a1_restored;
    }
    for (int i = 0; i < 6; ++i) {
        if (read_phys_bytes(fd, A1_ZIMA_W_PAS[i], a1_b, 2u) == 0 && read_le16(a1_b) == 3240u) {
            const uint8_t st[2] = {0x00u, 0x0Fu};
            if (write_phys_bytes(fd, A1_ZIMA_W_PAS[i], st, 2u) != 0) return 4;
            ++a1_restored;
        }
    }
    if (a1_restored > 0u) {
        printf("PUTBACK_A1: restored=%u\n", a1_restored);
    }
    printf("PUTBACK: ok restored=%u unchanged=%u\n", restored, unchanged);
    return 0;
}

static int line_matches(const char *s) {
    static const char *const keys[] = {
        "Refresh", "CAMOUT", "overflow", "S-Ele", "sgc", "SGC",
        "Imager", "IMX", "FE", "force", "NG", "Err", "ERR",
    };
    for (size_t i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i) {
        if (strstr(s, keys[i]) != NULL) return 1;
    }
    return 0;
}

static int cmd_alog(int fd) {
    const uint32_t base_pa = 0x03524FC0u;
    const uint32_t total = 0x60040u;
    uint8_t *buf = (uint8_t *)malloc(total);
    if (!buf) {
        perror("malloc alog");
        return 1;
    }
    uint32_t done = 0;
    while (done < total) {
        uint32_t cur_pa = base_pa + done;
        uint32_t chunk = PAGE_SIZE_BYTES - (cur_pa & PAGE_MASK_BYTES);
        if (chunk > total - done) chunk = total - done;
        if (read_phys_bytes(fd, cur_pa, buf + done, chunk) != 0) {
            free(buf);
            return 1;
        }
        done += chunk;
    }
    printf("ALOG_HDR: ");
    print_hex(buf, 16);
    printf("\n");

    char **lines = (char **)calloc(4096, sizeof(char *));
    if (!lines) {
        free(buf);
        return 1;
    }
    size_t nlines = 0;
    uint32_t i = 0;
    while (i < total && nlines < 4096) {
        while (i < total && (buf[i] < 0x20 || buf[i] > 0x7e)) ++i;
        uint32_t start = i;
        while (i < total && buf[i] >= 0x20 && buf[i] <= 0x7e) ++i;
        uint32_t len = i - start;
        if (len >= 6u) {
            if (len > 140u) len = 140u;
            char *s = (char *)malloc(len + 1u);
            if (s) {
                memcpy(s, buf + start, len);
                s[len] = '\0';
                lines[nlines++] = s;
            }
        }
    }
    printf("ALOG_LINES: %zu\n", nlines);
    size_t matched = 0;
    for (size_t k = 0; k < nlines; ++k) {
        if (line_matches(lines[k])) {
            if (matched < 30u) {
                printf("M[%zu]: %s\n", k, lines[k]);
            }
            ++matched;
        }
    }
    printf("ALOG_MATCHED: %zu\n", matched);
    size_t tail_start = (nlines > 25u) ? (nlines - 25u) : 0u;
    for (size_t k = tail_start; k < nlines; ++k) {
        printf("T[%zu]: %s\n", k, lines[k]);
    }
    for (size_t k = 0; k < nlines; ++k) free(lines[k]);
    free(lines);
    free(buf);
    return (nlines > 0u) ? 0 : 2;
}

#define SELE_FLAG_PA        0x0434FD7Eu
#define SELE_REFRESH_CNT_PA 0x04367AF0u
#define SELE_SIDE_CNT_PA    0x043679ACu
#define SELE_LATCH_PA       0x044B02B9u
#define SELE_STICKY_PA      0x044B02BBu
#define SELE_RCV_OVF_PA     0x044B02CAu
#define SELE_SDVGA_OVF_PA   0x044B02C9u
#define SELE_STATE_BYTE_PA  0x0434D9ABu
#define SELE_CAMOUT_VFC_PA  0x043679DCu
#define SELE_CAMOUT_FRC_PA  0x0436799Cu
#define SGC1_H_PA           0x04339E30u
#define SGC1_V_PA           0x04339E34u
#define SGC1_WIN_PA         0x04339E38u
#define SGC1_SRC_H_PA       0x04342BE4u
#define SGC1_SRC_V_PA       0x04342BE8u

static int read_u32_pa(int fd, uint32_t pa, uint32_t *out) {
    uint8_t b[4] = {0, 0, 0, 0};
    if (read_phys_bytes(fd, pa, b, 4) != 0) return -1;
    *out = read_le32(b);
    return 0;
}

static int cmd_diag(int fd) {
    uint32_t ref_cnt = 0, side_cnt = 0, vfc = 0, frc = 0;
    uint32_t sgc_h = 0, sgc_v = 0, src_h = 0, src_v = 0;
    uint8_t latch = 0, sticky = 0, rcv_ovf = 0, sdvga_ovf = 0, dbg_flag = 0, state_b = 0;
    uint8_t sgc1_win[3] = {0, 0, 0};
    uint16_t rcv_vals[6] = {0};
    uint8_t r4_0[2] = {0, 0}, r4_1[2] = {0, 0};

    if (read_u32_pa(fd, SELE_REFRESH_CNT_PA, &ref_cnt) != 0 ||
        read_u32_pa(fd, SELE_SIDE_CNT_PA, &side_cnt) != 0 ||
        read_phys_bytes(fd, SELE_LATCH_PA, &latch, 1) != 0 ||
        read_phys_bytes(fd, SELE_STICKY_PA, &sticky, 1) != 0 ||
        read_phys_bytes(fd, SELE_RCV_OVF_PA, &rcv_ovf, 1) != 0 ||
        read_phys_bytes(fd, SELE_SDVGA_OVF_PA, &sdvga_ovf, 1) != 0 ||
        read_phys_bytes(fd, SELE_FLAG_PA, &dbg_flag, 1) != 0 ||
        read_phys_bytes(fd, SELE_STATE_BYTE_PA, &state_b, 1) != 0 ||
        read_u32_pa(fd, SELE_CAMOUT_VFC_PA, &vfc) != 0 ||
        read_u32_pa(fd, SELE_CAMOUT_FRC_PA, &frc) != 0 ||
        read_u32_pa(fd, SGC1_H_PA, &sgc_h) != 0 ||
        read_u32_pa(fd, SGC1_V_PA, &sgc_v) != 0 ||
        read_phys_bytes(fd, SGC1_WIN_PA, sgc1_win, sizeof(sgc1_win)) != 0 ||
        read_u32_pa(fd, SGC1_SRC_H_PA, &src_h) != 0 ||
        read_u32_pa(fd, SGC1_SRC_V_PA, &src_v) != 0 ||
        read_phys_bytes(fd, FE_REC_0A_194_PA, r4_0, 2u) != 0 ||
        read_phys_bytes(fd, FE_REC_19_194_PA, r4_1, 2u) != 0) {
        return 1;
    }
    for (uint32_t i = 0; i < 6u; ++i) {
        uint8_t rb[2] = {0, 0};
        if (read_phys_bytes(fd, OG_PATCHES[146u + i].pa, rb, 2u) != 0) {
            return 1;
        }
        rcv_vals[i] = read_le16(rb);
    }
    printf("DIAG: refresh_cnt=%u side_cnt=%u latch=0x%02x sticky=0x%02x rcv_ovf=0x%02x sdvga_ovf=0x%02x\n",
           ref_cnt, side_cnt, latch, sticky, rcv_ovf, sdvga_ovf);
    printf("DIAG: debug_flag=0x%02x state_byte=0x%02x camout_vfc=%u camout_frc=%u\n",
           dbg_flag, state_b, vfc, frc);
    printf("DIAG: sgc1_HV=(%u,%u) [0x%x,0x%x] sgc1_src_HV=(%u,%u) [0x%x,0x%x]\n",
           sgc_h, sgc_v, sgc_h, sgc_v, src_h, src_v, src_h, src_v);
    printf("DIAG: sgc1_win=(+0x40=0x%02x,+0x41=0x%02x,+0x42=0x%02x)\n",
           sgc1_win[0], sgc1_win[1], sgc1_win[2]);
    printf("DIAG: fe_194=(%u,%u)\n", read_le16(r4_0), read_le16(r4_1));
    printf("DIAG: rcv_desc=[0x%08x:%u 0x%08x:%u 0x%08x:%u 0x%08x:%u 0x%08x:%u 0x%08x:%u]\n",
           OG_PATCHES[146].pa, rcv_vals[0], OG_PATCHES[147].pa, rcv_vals[1],
           OG_PATCHES[148].pa, rcv_vals[2], OG_PATCHES[149].pa, rcv_vals[3],
           OG_PATCHES[150].pa, rcv_vals[4], OG_PATCHES[151].pa, rcv_vals[5]);
    print_rt_diagnostics(fd);
    return 0;
}

static int cmd_watch(int fd, int seconds) {
    if (seconds < 1) seconds = 1;
    if (seconds > 60) seconds = 60;
    int steps = seconds * 20;
    struct timespec ts = {0, 50000000L};

    uint32_t prev_ref = 0xffffffffu, prev_h = 0xffffffffu, prev_v = 0xffffffffu;
    uint32_t prev_sh = 0xffffffffu, prev_sv = 0xffffffffu;
    uint8_t prev_l = 0xffu, prev_ro = 0xffu, prev_so = 0xffu, prev_sb = 0xffu;
    uint8_t prev_win[3] = {0xffu, 0xffu, 0xffu};

    for (int i = 0; i < steps; ++i) {
        uint32_t ref_cnt = 0, vfc = 0, sgc_h = 0, sgc_v = 0, src_h = 0, src_v = 0;
        uint8_t latch = 0, rcv_ovf = 0, sdvga_ovf = 0, state_b = 0;
        uint8_t win[3] = {0, 0, 0};
        if (read_u32_pa(fd, SELE_REFRESH_CNT_PA, &ref_cnt) != 0 ||
            read_phys_bytes(fd, SELE_LATCH_PA, &latch, 1) != 0 ||
            read_phys_bytes(fd, SELE_RCV_OVF_PA, &rcv_ovf, 1) != 0 ||
            read_phys_bytes(fd, SELE_SDVGA_OVF_PA, &sdvga_ovf, 1) != 0 ||
            read_phys_bytes(fd, SELE_STATE_BYTE_PA, &state_b, 1) != 0 ||
            read_u32_pa(fd, SELE_CAMOUT_VFC_PA, &vfc) != 0 ||
            read_u32_pa(fd, SGC1_H_PA, &sgc_h) != 0 ||
            read_u32_pa(fd, SGC1_V_PA, &sgc_v) != 0 ||
            read_phys_bytes(fd, SGC1_WIN_PA, win, sizeof(win)) != 0 ||
            read_u32_pa(fd, SGC1_SRC_H_PA, &src_h) != 0 ||
            read_u32_pa(fd, SGC1_SRC_V_PA, &src_v) != 0) {
            return 1;
        }
        int changed = (ref_cnt != prev_ref || latch != prev_l || rcv_ovf != prev_ro ||
                       sdvga_ovf != prev_so || state_b != prev_sb ||
                       sgc_h != prev_h || sgc_v != prev_v ||
                       win[0] != prev_win[0] || win[1] != prev_win[1] || win[2] != prev_win[2] ||
                       src_h != prev_sh || src_v != prev_sv);
        if (changed || (i % 20) == 0) {
            printf("t=%5dms ref=%u l=%u rcv=%u sdvga=%u st=0x%02x vfc=%u sgc=(%u,%u) win=(%02x,%02x,%02x) src=(%u,%u)\n",
                   i * 50, ref_cnt, latch, rcv_ovf, sdvga_ovf, state_b, vfc,
                   sgc_h, sgc_v, win[0], win[1], win[2], src_h, src_v);
            fflush(stdout);
            prev_ref = ref_cnt;
            prev_l = latch;
            prev_ro = rcv_ovf;
            prev_so = sdvga_ovf;
            prev_sb = state_b;
            prev_h = sgc_h;
            prev_v = sgc_v;
            prev_win[0] = win[0];
            prev_win[1] = win[1];
            prev_win[2] = win[2];
            prev_sh = src_h;
            prev_sv = src_v;
        }
        nanosleep(&ts, NULL);
    }
    return 0;
}

static int read_mmio_u32(int fd, uint32_t pa, uint32_t *out) {
    if ((pa & 3u) != 0u) {
        fprintf(stderr, "unaligned mmio read 0x%08x\n", pa);
        return -EINVAL;
    }
    uint32_t page_pa = pa & ~PAGE_MASK_BYTES;
    uint32_t off = pa & PAGE_MASK_BYTES;
    void *map = mmap(NULL, PAGE_SIZE_BYTES, PROT_READ, MAP_SHARED, fd, (off_t)page_pa);
    if (map == MAP_FAILED) {
        fprintf(stderr, "mmap mmio 0x%08x failed: %s\n", pa, strerror(errno));
        return -errno;
    }
    volatile uint32_t *reg = (volatile uint32_t *)((volatile uint8_t *)map + off);
    g_bus_armed = 1;
    if (sigsetjmp(g_bus_env, 1) != 0) {
        g_bus_armed = 0;
        munmap(map, PAGE_SIZE_BYTES);
        fprintf(stderr, "SIGBUS reading mmio 0x%08x\n", pa);
        return -EFAULT;
    }
    *out = *reg;
    g_bus_armed = 0;
    munmap(map, PAGE_SIZE_BYTES);
    return 0;
}

static int cmd_mmio_e0702(int fd, int secs) {
    static const uint32_t addrs[8] = {
        0xF38513A8u, 0xF38513B8u, 0xF3851418u, 0xF385141Cu,
        0xF39513A8u, 0xF39513B8u, 0xF3951418u, 0xF395141Cu,
    };
    if (secs < 0) secs = 0;
    if (secs > 90) secs = 90;
    int steps = (secs == 0) ? 1 : (secs * 4);
    struct timespec ts = {0, 250000000L};
    uint32_t prev[8] = {0};
    uint32_t prev_ref = 0xffffffffu;
    int have_prev = 0;

    for (int i = 0; i < steps; ++i) {
        uint32_t cur[8] = {0};
        uint32_t ref_cnt = 0, vfc = 0;
        for (int k = 0; k < 8; ++k) {
            if (read_mmio_u32(fd, addrs[k], &cur[k]) != 0) {
                return 1;
            }
        }
        if (read_u32_pa(fd, SELE_REFRESH_CNT_PA, &ref_cnt) != 0 ||
            read_u32_pa(fd, SELE_CAMOUT_VFC_PA, &vfc) != 0) {
            return 1;
        }
        int changed = (!have_prev || ref_cnt != prev_ref || memcmp(cur, prev, sizeof(cur)) != 0);
        if (changed || (i % 8) == 0) {
            printf("t=%5dms ref=%u vfc=%u 85=[3A8:%08x 3B8:%08x 418:%08x 41C:%08x] 95=[3A8:%08x 3B8:%08x 418:%08x 41C:%08x]\n",
                   i * 250, ref_cnt, vfc,
                   cur[0], cur[1], cur[2], cur[3],
                   cur[4], cur[5], cur[6], cur[7]);
            fflush(stdout);
            memcpy(prev, cur, sizeof(cur));
            prev_ref = ref_cnt;
            have_prev = 1;
        }
        if (secs > 0) nanosleep(&ts, NULL);
    }
    return 0;
}

static int cmd_noreboot(int fd, int enable) {
    uint8_t cur_flag = 0, new_flag = 0;
    uint8_t cnt_buf[4] = {0, 0, 0, 0};
    if (read_phys_bytes(fd, SELE_FLAG_PA, &cur_flag, 1) != 0) {
        return 1;
    }
    if (read_phys_bytes(fd, SELE_REFRESH_CNT_PA, cnt_buf, 4) != 0) {
        return 1;
    }
    new_flag = enable ? (uint8_t)(cur_flag | 0x40u) : (uint8_t)(cur_flag & ~0x40u);
    if (new_flag != cur_flag) {
        if (write_phys_bytes(fd, SELE_FLAG_PA, &new_flag, 1) != 0) {
            return 1;
        }
    }
    printf("NOREBOOT %s: flag 0x%02x -> 0x%02x refresh_cnt=%u\n",
           enable ? "on" : "off", cur_flag, new_flag, read_le32(cnt_buf));
    return 0;
}

static int cmd_resetcnt(int fd) {
    uint8_t prev[4] = {0, 0, 0, 0};
    uint8_t zero[4] = {0, 0, 0, 0};
    if (read_phys_bytes(fd, SELE_REFRESH_CNT_PA, prev, 4) != 0) {
        return 1;
    }
    if (write_phys_bytes(fd, SELE_REFRESH_CNT_PA, zero, 4) != 0) {
        return 1;
    }
    printf("RESETCNT: %u -> 0\n", read_le32(prev));
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <dump | diag | alog | noreboot on|off | resetcnt | apply ... | putback>\n", argv[0]);
        return 2;
    }
    if (install_sigbus_handler() != 0) {
        return 1;
    }
    int is_write = (strcmp(argv[1], "apply") == 0 ||
                    strcmp(argv[1], "putback") == 0 ||
                    strcmp(argv[1], "noreboot") == 0 ||
                    strcmp(argv[1], "resetcnt") == 0);
    int fd = open("/dev/mem", is_write ? (O_RDWR | O_SYNC) : (O_RDONLY | O_SYNC));
    if (fd < 0) {
        perror("open /dev/mem");
        return 1;
    }
    int rc = 2;
    if (strcmp(argv[1], "dump") == 0) {
        rc = cmd_dump(fd);
    } else if (strcmp(argv[1], "diag") == 0) {
        rc = cmd_diag(fd);
    } else if (strcmp(argv[1], "watch") == 0) {
        int secs = (argc >= 3) ? atoi(argv[2]) : 25;
        rc = cmd_watch(fd, secs);
    } else if (strcmp(argv[1], "mmio_e0702") == 0) {
        int secs = (argc >= 3) ? atoi(argv[2]) : 0;
        rc = cmd_mmio_e0702(fd, secs);
    } else if (strcmp(argv[1], "alog") == 0) {
        rc = cmd_alog(fd);
    } else if (strcmp(argv[1], "noreboot") == 0 && argc >= 3) {
        if (strcmp(argv[2], "on") == 0) rc = cmd_noreboot(fd, 1);
        else if (strcmp(argv[2], "off") == 0) rc = cmd_noreboot(fd, 0);
        else fprintf(stderr, "Use noreboot on|off\n");
    } else if (strcmp(argv[1], "resetcnt") == 0) {
        rc = cmd_resetcnt(fd);
    } else if (strcmp(argv[1], "apply") == 0 && argc >= 5 && strcmp(argv[2], "plus24_fe") == 0) {
        int lo = atoi(argv[3]);
        int hi = atoi(argv[4]);
        if (lo < 0 || hi <= lo || hi > 66) {
            fprintf(stderr, "Invalid FE range [%d..%d) (require 0 <= lo < hi <= 66)\n", lo, hi);
            rc = 2;
        } else {
            rc = cmd_apply_fe_range(fd, (uint32_t)lo, (uint32_t)hi);
        }
    } else if (strcmp(argv[1], "apply") == 0 && argc >= 3 && strcmp(argv[2], "plus24_r4") == 0) {
        rc = cmd_apply_plus24_r4(fd);
    } else if (strcmp(argv[1], "apply") == 0 && argc >= 3 && strcmp(argv[2], "a1") == 0) {
        rc = cmd_apply_a1(fd);
    } else if (strcmp(argv[1], "apply") == 0 && argc >= 3) {
        rc = cmd_apply(fd, argv[2]);
    } else if (strcmp(argv[1], "putback") == 0) {
        rc = cmd_putback(fd);
    } else {
        fprintf(stderr, "Invalid arguments\n");
    }
    close(fd);
    return rc;
}
