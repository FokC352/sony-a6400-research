/* WiFiSync camera-side core implementation. Freestanding: no libc, no heap. */
#include "wifisync_core.h"

/* ------------------------------------------------------------------ util */

uint32_t ws_strlen(const char *s) {
    uint32_t n = 0;
    if (!s) return 0;
    while (s[n]) n++;
    return n;
}

int ws_streq(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return *a == *b;
}

int ws_strneq(const char *a, const char *b, uint32_t n) {
    uint32_t i;
    for (i = 0; i < n; i++) {
        if (a[i] != b[i]) return 0;
        if (!a[i]) return 1;
    }
    return 1;
}

void ws_memcpy(void *dst, const void *src, uint32_t n) {
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    while (n--) *d++ = *s++;
}

void ws_memset(void *dst, uint8_t v, uint32_t n) {
    uint8_t *d = (uint8_t *)dst;
    while (n--) *d++ = v;
}

int ws_memcmp(const void *a, const void *b, uint32_t n) {
    const uint8_t *pa = (const uint8_t *)a, *pb = (const uint8_t *)b;
    while (n--) {
        if (*pa != *pb) return (int)*pa - (int)*pb;
        pa++; pb++;
    }
    return 0;
}

int ws_parse_u32(const char *s, uint32_t *out) {
    uint32_t v = 0;
    int digits = 0;
    if (!s || !out) return -1;
    while (*s == ' ') s++;
    while (*s >= '0' && *s <= '9') {
        uint32_t d = (uint32_t)(*s - '0');
        if (v > 429496729u) return -1;
        v = v * 10u + d;
        digits++;
        s++;
    }
    if (!digits) return -1;
    *out = v;
    return 0;
}

static const char *ws_ext_suffix(uint8_t ext) {
    switch (ext) {
    case WS_EXT_JPG: return "JPG";
    case WS_EXT_ARW: return "ARW";
    default:         return "";
    }
}

const char *ws_ext_name(uint8_t ext) { return ws_ext_suffix(ext); }

static void put_4digits(char *out, uint32_t v) {
    out[0] = (char)('0' + ((v / 1000u) % 10u));
    out[1] = (char)('0' + ((v / 100u) % 10u));
    out[2] = (char)('0' + ((v / 10u) % 10u));
    out[3] = (char)('0' + (v % 10u));
}

static int build_name_with_prefix(const char *prefix, uint32_t index, uint8_t ext, char *out) {
    const char *suf = ws_ext_suffix(ext);
    const char *pfx = (prefix && prefix[0] && prefix[1] && prefix[2] && prefix[3]) ? prefix : "DSC0";
    if (!suf[0] || index > 9999u) return -1;
    out[0] = pfx[0]; out[1] = pfx[1]; out[2] = pfx[2]; out[3] = pfx[3];
    put_4digits(out + 4, index);
    out[8] = '.';
    out[9] = suf[0];
    out[10] = suf[1];
    out[11] = suf[2];
    out[12] = 0;
    return 0;
}

int ws_build_name(uint32_t index, uint8_t ext, char *out) {
    return build_name_with_prefix(0, index, ext, out);
}

int ws_build_path(const ws_config *cfg, uint32_t index, uint8_t ext, char *out) {
    uint32_t n = ws_strlen(cfg->dcim_dir);
    uint32_t i;
    char name[WS_NAME_MAX];
    if (build_name_with_prefix(cfg->prefix, index, ext, name) != 0) return -1;
    if (n + 1u + sizeof(name) > WS_PATH_MAX) return -1;
    for (i = 0; i < n; i++) out[i] = cfg->dcim_dir[i];
    out[n] = '/';
    ws_memcpy(out + n + 1u, name, sizeof(name));
    return 0;
}

/* -------------------------------------------------------------- CRC-32 */
/* Slice-by-4 over a runtime-built table: keeps 1 KiB out of .rodata, where the
 * SMOD relocation scanner would have to reason about it. */

/* Defined below; declared here because handle_ack() and the routes use it. */
static const char *find_key(const char *from, const char *end, const char *key, uint32_t key_len);

void ws_crc_init(ws_server *s) {
    uint32_t i, j, c;
    if (s->crc_ready) return;
    for (i = 0; i < 256u; i++) {
        c = i;
        for (j = 0; j < 8u; j++) c = (c & 1u) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
        s->crc_table[0][i] = c;
    }
    for (i = 0; i < 256u; i++) {
        uint32_t v = s->crc_table[0][i];
        s->crc_table[1][i] = (v >> 8) ^ s->crc_table[0][v & 0xFFu];
        v = s->crc_table[1][i];
        s->crc_table[2][i] = (v >> 8) ^ s->crc_table[0][v & 0xFFu];
        v = s->crc_table[2][i];
        s->crc_table[3][i] = (v >> 8) ^ s->crc_table[0][v & 0xFFu];
    }
    s->crc_ready = 1;
}

uint32_t ws_crc32_update(const ws_server *s, uint32_t crc, const void *buf, uint32_t len) {
    const uint8_t *p = (const uint8_t *)buf;
    const uint32_t *t0 = s->crc_table[0], *t1 = s->crc_table[1];
    const uint32_t *t2 = s->crc_table[2], *t3 = s->crc_table[3];
    while (len >= 4u) {
        uint32_t x = crc ^ ((uint32_t)p[0] | ((uint32_t)p[1] << 8) |
                            ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24));
        crc = t3[x & 0xFFu] ^ t2[(x >> 8) & 0xFFu] ^
              t1[(x >> 16) & 0xFFu] ^ t0[(x >> 24) & 0xFFu];
        p += 4;
        len -= 4u;
    }
    while (len--) crc = t0[(crc ^ *p++) & 0xFFu] ^ (crc >> 8);
    return crc;
}

uint32_t ws_crc32(const ws_server *s, const void *buf, uint32_t len) {
    return ws_crc32_update(s, 0xFFFFFFFFu, buf, len) ^ 0xFFFFFFFFu;
}

/* ---------------------------------------------------------------- queue */

static int ws_slot_valid(const ws_server *s, uint32_t seq) {
    return seq != 0u && seq <= s->q.head;
}

static uint32_t ws_slot_of(const ws_server *s, uint32_t seq) {
    (void)s;
    return (seq - 1u) % WS_QUEUE_CAP;
}

void ws_queue_reset(ws_server *s) {
    ws_memset(&s->q, 0, sizeof(s->q));
}

uint32_t ws_queue_pending(const ws_server *s) {
    return s->q.head - s->q.ack;
}

uint32_t ws_queue_next_pending(const ws_server *s) {
    return (s->q.ack < s->q.head) ? s->q.ack + 1u : 0u;
}

int32_t ws_queue_find(const ws_server *s, uint32_t seq) {
    if (!ws_slot_valid(s, seq) || seq <= s->q.ack) return -1;
    {
        uint32_t slot = ws_slot_of(s, seq);
        if (s->q.slot[slot].seq != seq) return -1;
        if (!s->q.slot[slot].ready) return -1;
        return (int32_t)slot;
    }
}

static uint32_t ws_min_size_for(const ws_server *s, uint8_t ext) {
    return (ext == WS_EXT_ARW) ? s->cfg.min_arw : s->cfg.min_jpg;
}

/* Is a file with the same index and extension already queued (unacked)? */
static ws_entry *ws_find_unacked(ws_server *s, uint32_t index, uint8_t ext) {
    uint32_t seq;
    for (seq = s->q.ack + 1u; seq <= s->q.head; seq++) {
        ws_entry *e = &s->q.slot[ws_slot_of(s, seq)];
        if (e->seq == seq && e->index == index && e->ext == ext) return e;
    }
    return 0;
}

uint32_t ws_queue_push(ws_server *s, uint32_t index, uint8_t ext,
                       const char *name, uint32_t size, uint32_t mtime) {
    uint32_t slot;
    ws_entry *e;
    uint32_t seq;
    ws_entry *old;
    if (ext != WS_EXT_JPG && ext != WS_EXT_ARW) return 0;
    if (!name || !name[0]) return 0;
    if (size < ws_min_size_for(s, ext)) return 0;
    /* Re-observation of a queued file: a growth means the writer had not
     * finished when it was first seen. Update the size and drop the cached
     * CRC so the next /file computes it over the final bytes. */
    old = ws_find_unacked(s, index, ext);
    if (old) {
        if (size > old->size) {
            old->size = size;
            old->mtime = mtime;
            old->crc_state = WS_CRC_PENDING;
            old->crc32 = 0;
            s->events++;
        }
        return 0;
    }
    if (ws_queue_pending(s) >= WS_QUEUE_CAP) return 0;
    /* Smart Single-Best is final: a late .ARW supersedes a queued .JPG of the
     * same stem so the phone never receives the duplicate. */
    if (ext == WS_EXT_ARW) {
        uint32_t q;
        for (q = s->q.ack + 1u; q <= s->q.head; q++) {
            ws_entry *old = &s->q.slot[ws_slot_of(s, q)];
            if (old->seq == q && old->index == index && old->ext == WS_EXT_JPG) {
                old->ready = 0;
                s->events++;
            }
        }
    }
    seq = s->q.head + 1u;
    slot = ws_slot_of(s, seq);
    e = &s->q.slot[slot];
    ws_memset(e, 0, sizeof(*e));
    e->seq = seq;
    e->index = index;
    e->size = size;
    e->mtime = mtime;
    e->ext = ext;
    e->ready = 1;
    e->crc_state = WS_CRC_PENDING;
    {
        uint32_t i = 0;
        while (name[i] && i + 1u < WS_NAME_MAX) { e->name[i] = name[i]; i++; }
        e->name[i] = 0;
    }
    s->q.head = seq;
    s->events++;
    return seq;
}

void ws_queue_ack(ws_server *s, uint32_t seq) {
    if (seq > s->q.head) return;
    if (seq > s->q.ack) {
        s->q.ack = seq;
        {
            uint32_t slot = ws_slot_of(s, seq);
            if (s->q.slot[slot].seq == seq) {
                if (s->q.slot[slot].index > s->acked_index)
                    s->acked_index = s->q.slot[slot].index;
            }
        }
        /* Skip superseded (dropped) records so the watermark can pass them. */
        while (s->q.ack < s->q.head) {
            uint32_t slot = ws_slot_of(s, s->q.ack + 1u);
            if (s->q.slot[slot].seq == s->q.ack + 1u && s->q.slot[slot].ready) break;
            s->q.ack++;
        }
        s->events++;
    }
}

/* ------------------------------------------------------- capture + scan */

void ws_note_capture(ws_server *s) {
    /* Runs inside the ConPutWriteStill interposition: no I/O, no allocation,
     * one word store for the flag and one for the clock. */
    s->capture_count++;
    s->last_capture_ms = s->plat.now_ms ? s->plat.now_ms(s->plat.ctx) : 0u;
    s->events++;
}

static int probe_file(ws_server *s, uint32_t index, uint8_t ext,
                      uint32_t *size, uint32_t *mtime) {
    char path[WS_PATH_MAX];
    uint32_t sz = 0, mt = 0;
    void *h = 0;
    if (ws_build_path(&s->cfg, index, ext, path) != 0) return -1;
    if (s->fs.stat(s->fs.ctx, path, &sz, &mt) != 0) return 0;
    if (sz < ws_min_size_for(s, ext)) return 0;
    if (s->fs.open(s->fs.ctx, path, &h) != 0) return 0;
    (void)s->fs.close(s->fs.ctx, h);
    *size = sz;
    *mtime = mt;
    return 1;
}

/* Smart Single-Best: for one stem, .ARW wins whenever both exist; a lone .JPG
 * is sent as .JPG; a lone .ARW is sent as .ARW. */
static int smart_pick(ws_server *s, uint32_t index, uint32_t now_ms) {
    uint32_t arw_size = 0, arw_mtime = 0, jpg_size = 0, jpg_mtime = 0;
    int has_arw, has_jpg;
    char name[WS_NAME_MAX];
    uint32_t size, mtime;
    uint8_t ext;
    /* Burst quiet window: while the camera is writing, the SD card keeps its
     * full bandwidth and a half-written file is never enqueued. */
    if (s->last_capture_ms && (uint32_t)(now_ms - s->last_capture_ms) < WS_BURST_QUIET_MS)
        return 0;
    has_arw = probe_file(s, index, WS_EXT_ARW, &arw_size, &arw_mtime);
    has_jpg = probe_file(s, index, WS_EXT_JPG, &jpg_size, &jpg_mtime);
    if (has_arw < 0 || has_jpg < 0) return -1;
    if (!has_arw && !has_jpg) return 0;
    if (has_arw) {
        ext = WS_EXT_ARW; size = arw_size; mtime = arw_mtime;
    } else {
        ext = WS_EXT_JPG; size = jpg_size; mtime = jpg_mtime;
    }
    if (s->acked_index && index <= s->acked_index) return 0;
    if (build_name_with_prefix(s->cfg.prefix, index, ext, name) != 0) return -1;
    if (ws_queue_push(s, index, ext, name, size, mtime) == 0u) return 0;
    if (s->plat.log) s->plat.log(s->plat.ctx, "queued", index);
    return 1;
}

int ws_scan(ws_server *s) {
    uint32_t now = s->plat.now_ms ? s->plat.now_ms(s->plat.ctx) : 0u;
    uint32_t budget, added = 0;
    uint32_t i, p, stop;

    if (s->next_index == 0u) {
        s->next_index = s->cfg.first_index ? s->cfg.first_index : 1u;
        s->scan_end = s->next_index + (s->cfg.discover_span ? s->cfg.discover_span : 64u);
        if (s->scan_end > 10000u) s->scan_end = 10000u;
        s->discovering = 1;
        s->window_added = 0;
        s->miss_run = 0;
        s->last_scan_ms = 0;
    }
    budget = s->cfg.scan_budget ? s->cfg.scan_budget : 128u;

    if (s->discovering) {
        /* Bounded first sweep over [first_index, first_index + discover_span).
         * A contiguous run is found in one or two calls; the miss limit stops
         * a nearly empty card early instead of walking all 9999 stems. */
        while (s->next_index < s->scan_end && budget--) {
            i = s->next_index;
            if (s->plat.should_stop && s->plat.should_stop(s->plat.ctx)) break;
            if (smart_pick(s, i, now) > 0) {
                added++;
                s->window_added = 1;
                s->miss_run = 0;
            } else {
                s->miss_run++;
            }
            s->next_index++;
            if (s->window_added && s->cfg.discover_miss_limit &&
                s->miss_run >= s->cfg.discover_miss_limit) break;
        }
        if (s->next_index >= s->scan_end) {
            s->discovering = 0;
            if (!s->window_added) {
                /* Nothing in the sweep: watch from the start so the first new
                 * photo is still found. */
                s->next_index = s->cfg.first_index ? s->cfg.first_index : 1u;
            }
            if (s->plat.log) s->plat.log(s->plat.ctx, "discovery_done", s->next_index);
        } else if (s->window_added && s->cfg.discover_miss_limit &&
                   s->miss_run >= s->cfg.discover_miss_limit) {
            /* Contiguous run ended: switch to the steady forward window. */
            s->discovering = 0;
            if (s->plat.log) s->plat.log(s->plat.ctx, "discovery_done", s->next_index);
        }
    } else {
        /* Steady state: probe one bounded window ahead of the last known stem.
         * Misses do not move the high-water mark, so the window does not run
         * away while the camera is idle. */
        uint32_t fwd = s->cfg.probe_forward ? s->cfg.probe_forward : 64u;
        p = s->next_index;
        stop = p + fwd;
        if (stop > 10000u) stop = 10000u;
        while (p < stop && budget--) {
            if (s->plat.should_stop && s->plat.should_stop(s->plat.ctx)) break;
            if (smart_pick(s, p, now) > 0) {
                added++;
                s->next_index = p + 1u;
                stop = s->next_index + fwd;
                if (stop > 10000u) stop = 10000u;
            }
            p++;
        }
        /* Resync behind the high-water mark around a fresh capture, so a
         * numbering reset or a re-inserted card is still caught. */
        if (added == 0u && s->cfg.probe_back && s->next_index > 1u &&
            s->last_capture_ms && (uint32_t)(now - s->last_capture_ms) < 10000u) {
            uint32_t back = s->next_index - 1u;
            uint32_t floor = (back > s->cfg.probe_back) ? back - s->cfg.probe_back : 1u;
            for (i = floor; i <= back; i++) {
                if (smart_pick(s, i, now) > 0) added++;
            }
        }
    }
    s->last_scan_ms = now;
    return (int)added;
}

/* --------------------------------------------------------------- CRC step */

static int compute_entry_crc(ws_server *s, ws_entry *e) {
    char path[WS_PATH_MAX];
    void *h = 0;
    uint32_t crc = 0xFFFFFFFFu;
    uint32_t left = e->size;
    int n;
    if (ws_build_path(&s->cfg, e->index, e->ext, path) != 0) return -1;
    if (s->fs.open(s->fs.ctx, path, &h) != 0) return -2;
    while (left) {
        uint32_t want = (left > s->io_cap) ? s->io_cap : left;
        n = s->fs.read(s->fs.ctx, h, s->io, want);
        if (n <= 0) { s->fs.close(s->fs.ctx, h); return -3; }
        crc = ws_crc32_update(s, crc, s->io, (uint32_t)n);
        left -= (uint32_t)n;
    }
    s->fs.close(s->fs.ctx, h);
    e->crc32 = crc ^ 0xFFFFFFFFu;
    e->crc_state = WS_CRC_KNOWN;
    return 0;
}

int ws_crc_step(ws_server *s) {
    uint32_t seq;
    for (seq = s->q.ack + 1u; seq <= s->q.head; seq++) {
        ws_entry *e = &s->q.slot[ws_slot_of(s, seq)];
        if (e->seq != seq || e->crc_state != WS_CRC_PENDING) continue;
        if (compute_entry_crc(s, e) != 0) {
            e->crc_state = WS_CRC_NONE;
            s->errors++;
            if (s->plat.log) s->plat.log(s->plat.ctx, "crc_read_failed", seq);
        } else if (s->plat.log) {
            s->plat.log(s->plat.ctx, "crc_ready", seq);
        }
        return 1;
    }
    return 0;
}

/* --------------------------------------------------------- power machine */

uint32_t ws_power_state(const ws_server *s) { return s->power; }

static void power_apply(ws_server *s, uint32_t state) {
    if (s->power == state) return;
    if (s->plat.set_power && s->plat.set_power(s->plat.ctx, state) != 0) {
        s->errors++;
        if (s->plat.log) s->plat.log(s->plat.ctx, "power_apply_failed", state);
        return;
    }
    s->power = state;
    s->last_activity_ms = s->plat.now_ms ? s->plat.now_ms(s->plat.ctx) : 0u;
    if (s->plat.log) s->plat.log(s->plat.ctx, "power_state", state);
}

void ws_power_on(ws_server *s) {
    power_apply(s, WS_POWER_ACTIVE);
    s->last_activity_ms = s->plat.now_ms ? s->plat.now_ms(s->plat.ctx) : 0u;
}

void ws_power_off(ws_server *s) {
    power_apply(s, WS_POWER_OFF);
}

/* Number of entries the phone still has to fetch (superseded records excluded). */
static uint32_t count_ready(const ws_server *s) {
    uint32_t seq, n = 0;
    for (seq = s->q.ack + 1u; seq <= s->q.head; seq++) {
        const ws_entry *e = &s->q.slot[ws_slot_of(s, seq)];
        if (e->seq == seq && e->ready) n++;
    }
    return n;
}

void ws_power_tick(ws_server *s, uint32_t now_ms) {
    uint32_t idle_ms = (uint32_t)(s->cfg.idle_sec ? s->cfg.idle_sec : 45u) * 1000u;
    if (s->power == WS_POWER_OFF) return;
    /* A capture or any queued file keeps the AP ACTIVE at full TX rate. */
    if ((s->last_capture_ms && (uint32_t)(now_ms - s->last_capture_ms) < 5000u) ||
        count_ready(s) > 0u) {
        power_apply(s, WS_POWER_ACTIVE);
        s->last_activity_ms = now_ms;
        return;
    }
    if (s->power == WS_POWER_ACTIVE &&
        (uint32_t)(now_ms - s->last_activity_ms) >= idle_ms) {
        power_apply(s, WS_POWER_STANDBY);
    }
}

/* ------------------------------------------------------------ HTTP layer */

typedef struct ws_buf {
    char    *p;
    uint32_t len;
    uint32_t cap;
    int      over;
} ws_buf;

static void buf_init(ws_buf *b, char *p, uint32_t cap) {
    b->p = p; b->len = 0; b->cap = cap; b->over = 0;
}

static void buf_putc(ws_buf *b, char c) {
    if (b->len + 1u >= b->cap) { b->over = 1; return; }
    b->p[b->len++] = c;
    b->p[b->len] = 0;
}

static void buf_puts(ws_buf *b, const char *s) {
    while (s && *s) buf_putc(b, *s++);
}

static void buf_putu(ws_buf *b, uint32_t v) {
    char tmp[10];
    int i = 0;
    if (!v) { buf_putc(b, '0'); return; }
    while (v && i < 10) { tmp[i++] = (char)('0' + (v % 10u)); v /= 10u; }
    while (i) buf_putc(b, tmp[--i]);
}

static void buf_puthex8(ws_buf *b, uint32_t v) {
    static const char hx[] = "0123456789abcdef";
    int i;
    for (i = 28; i >= 0; i -= 4) buf_putc(b, hx[(v >> i) & 0xFu]);
}

static int send_str(ws_conn *c, const char *s) {
    return c->send(c->ctx, s, ws_strlen(s));
}

static void log_http(ws_server *s, const char *msg, uint32_t v) {
    if (s->plat.log) s->plat.log(s->plat.ctx, msg, v);
}

/* Case-insensitive header lookup in the request head. Returns value start. */
static const char *header_value(const char *head, const char *name, uint32_t name_len) {
    const char *p = head;
    while (p && *p) {
        /* start of a line */
        uint32_t i;
        int match = 1;
        for (i = 0; i < name_len; i++) {
            char a = p[i], b = name[i];
            if (a >= 'A' && a <= 'Z') a = (char)(a - 'A' + 'a');
            if (b >= 'A' && b <= 'Z') b = (char)(b - 'A' + 'a');
            if (a != b) { match = 0; break; }
        }
        if (match && p[name_len] == ':') {
            const char *v = p + name_len + 1;
            while (*v == ' ' || *v == '\t') v++;
            return v;
        }
        while (*p && *p != '\n') p++;
        if (*p == '\n') p++;
    }
    return 0;
}

static const char *conn_hdr(int keep_open) {
    return keep_open ? "\r\nConnection: keep-alive\r\n\r\n" : "\r\nConnection: close\r\n\r\n";
}

static int ci_contains(const char *val, const char *needle) {
    uint32_t n = ws_strlen(needle);
    if (!val || !n) return 0;
    while (*val && *val != '\r' && *val != '\n') {
        uint32_t i;
        int match = 1;
        for (i = 0; i < n; i++) {
            char a = val[i], b = needle[i];
            if (!a || a == '\r' || a == '\n') { match = 0; break; }
            if (a >= 'A' && a <= 'Z') a = (char)(a - 'A' + 'a');
            if (b >= 'A' && b <= 'Z') b = (char)(b - 'A' + 'a');
            if (a != b) { match = 0; break; }
        }
        if (match) return 1;
        val++;
    }
    return 0;
}

static int send_status(ws_server *s, ws_conn *c, int keep_open) {
    char b[512];
    ws_buf o;
    ws_buf *w = &o;
    buf_init(w, b, sizeof(b));
    buf_puts(w, "{\"camera_id\":\"");
    buf_puthex8(w, s->cfg.camera_id);
    buf_puts(w, "\",\"fw\":\"");
    buf_puts(w, s->fw);
    buf_puts(w, "\",\"queue_len\":");
    buf_putu(w, ws_queue_pending(s));
    buf_puts(w, ",\"queue_head\":");
    buf_putu(w, s->q.head);
    buf_puts(w, ",\"ack\":");
    buf_putu(w, s->q.ack);
    buf_puts(w, ",\"power_state\":");
    buf_putu(w, s->power);
    buf_puts(w, ",\"ap_chanspec\":");
    buf_putu(w, s->cfg.channel);
    buf_puts(w, ",\"ap_width\":");
    buf_putu(w, s->cfg.width);
    buf_puts(w, ",\"captures\":");
    buf_putu(w, s->capture_count);
    buf_puts(w, ",\"next_index\":");
    buf_putu(w, s->next_index);
    buf_puts(w, ",\"discovering\":");
    buf_putu(w, s->discovering);
    buf_puts(w, ",\"files_served\":");
    buf_putu(w, s->files_served);
    buf_puts(w, ",\"errors\":");
    buf_putu(w, s->errors);
    buf_puts(w, ",\"uptime_ms\":");
    buf_putu(w, s->plat.now_ms ? (uint32_t)(s->plat.now_ms(s->plat.ctx) - s->start_ms) : 0u);
    buf_puts(w, "}");
    if (w->over) return -1;
    if (send_str(c, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ") != 0)
        return -1;
    {
        char lenbuf[12];
        ws_buf lb;
        buf_init(&lb, lenbuf, sizeof(lenbuf));
        buf_putu(&lb, w->len);
        if (lb.over || c->send(c->ctx, lenbuf, lb.len) != 0) return -1;
    }
    if (send_str(c, conn_hdr(keep_open)) != 0) return -1;
    return c->send(c->ctx, b, w->len);
}

static int send_manifest(ws_server *s, ws_conn *c, uint32_t since, int keep_open) {
    uint32_t seq, len = 0;
    char b[4096];
    ws_buf o;
    ws_buf *w = &o;
    buf_init(w, b, sizeof(b));
    buf_puts(w, "{\"since\":");
    buf_putu(w, since);
    buf_puts(w, ",\"head\":");
    buf_putu(w, s->q.head);
    buf_puts(w, ",\"files\":[");
    for (seq = (since > s->q.ack ? since : s->q.ack) + 1u; seq <= s->q.head; seq++) {
        ws_entry *e = &s->q.slot[ws_slot_of(s, seq)];
        if (e->seq != seq) continue;
        if (!e->ready) continue;
        if (len++) buf_putc(w, ',');
        buf_puts(w, "{\"seq_id\":");
        buf_putu(w, e->seq);
        buf_puts(w, ",\"name\":\"");
        buf_puts(w, e->name);
        buf_puts(w, "\",\"ext\":\"");
        buf_puts(w, ws_ext_suffix(e->ext));
        buf_puts(w, "\",\"size\":");
        buf_putu(w, e->size);
        buf_puts(w, ",\"mtime\":");
        buf_putu(w, e->mtime);
        buf_puts(w, ",\"crc32\":");
        if (e->crc_state == WS_CRC_KNOWN) {
            buf_puts(w, "\"");
            buf_puthex8(w, e->crc32);
            buf_puts(w, "\"");
        } else {
            buf_puts(w, "null");
        }
        buf_putc(w, '}');
        if (w->len > sizeof(b) - 256u) break;   /* bounded page */
    }
    buf_puts(w, "]}");
    if (w->over) return -1;
    if (send_str(c, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ") != 0)
        return -1;
    {
        char lenbuf[12];
        ws_buf lb;
        buf_init(&lb, lenbuf, sizeof(lenbuf));
        buf_putu(&lb, w->len);
        if (lb.over || c->send(c->ctx, lenbuf, lb.len) != 0) return -1;
    }
    if (send_str(c, conn_hdr(keep_open)) != 0) return -1;
    return c->send(c->ctx, b, w->len);
}

static int send_error_ka(ws_conn *c, uint32_t code, const char *reason,
                         const char *body, int keep_open) {
    char head[224];
    ws_buf b;
    buf_init(&b, head, sizeof(head));
    buf_puts(&b, "HTTP/1.1 ");
    buf_putu(&b, code);
    buf_putc(&b, ' ');
    buf_puts(&b, reason);
    buf_puts(&b, "\r\nContent-Type: application/json\r\nContent-Length: ");
    buf_putu(&b, ws_strlen(body));
    buf_puts(&b, conn_hdr(keep_open));
    if (b.over) return -1;
    if (c->send(c->ctx, head, b.len) != 0) return -1;
    return c->send(c->ctx, body, ws_strlen(body));
}

static int send_error(ws_conn *c, uint32_t code, const char *reason, const char *body) {
    return send_error_ka(c, code, reason, body, 0);
}

static int skip_bytes(ws_server *s, void *h, uint32_t count, uint32_t *crc_acc) {
    while (count) {
        uint32_t want = (count > s->io_cap) ? s->io_cap : count;
        int n = s->fs.read(s->fs.ctx, h, s->io, want);
        if (n <= 0) return -1;
        if (crc_acc) *crc_acc = ws_crc32_update(s, *crc_acc, s->io, (uint32_t)n);
        count -= (uint32_t)n;
    }
    return 0;
}

static uint16_t tiff_u16(const uint8_t *p, int le) {
    return le ? (uint16_t)(p[0] | ((uint16_t)p[1] << 8))
              : (uint16_t)(((uint16_t)p[0] << 8) | p[1]);
}

static uint32_t tiff_u32(const uint8_t *p, int le) {
    return le ? ((uint32_t)p[0] | ((uint32_t)p[1] << 8) |
                 ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24))
              : (((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
                 ((uint32_t)p[2] << 8) | (uint32_t)p[3]);
}

static int find_arw_preview(const uint8_t *buf, uint32_t hdr_len, uint32_t file_size,
                            uint32_t *out_off, uint32_t *out_len) {
    int le;
    uint32_t ifd_q[8];
    uint32_t visited[8];
    uint32_t q_head = 0, q_tail = 0, vis_n = 0;
    uint32_t best_off = 0, best_len = 0;

    if (hdr_len < 12u) return -1;
    if (buf[0] == 'I' && buf[1] == 'I') le = 1;
    else if (buf[0] == 'M' && buf[1] == 'M') le = 0;
    else return -1;
    if (tiff_u16(buf + 2, le) != 42u) return -1;

    ifd_q[q_tail++] = tiff_u32(buf + 4, le);
    while (q_head < q_tail && vis_n < 8u) {
        uint32_t ifd = ifd_q[q_head++];
        uint32_t count, k, next_pos;
        uint32_t jpg_off = 0, jpg_len = 0;
        int dup = 0;
        if (ifd == 0u || ifd + 2u > hdr_len) continue;
        for (k = 0; k < vis_n; k++) {
            if (visited[k] == ifd) { dup = 1; break; }
        }
        if (dup) continue;
        visited[vis_n++] = ifd;
        count = tiff_u16(buf + ifd, le);
        for (k = 0; k < count && k < 256u; k++) {
            uint32_t ent = ifd + 2u + k * 12u;
            uint16_t tag;
            uint32_t val;
            if (ent + 12u > hdr_len) break;
            tag = tiff_u16(buf + ent, le);
            val = tiff_u32(buf + ent + 8u, le);
            if (tag == 0x0201u) {
                jpg_off = val;
            } else if (tag == 0x0202u) {
                jpg_len = val;
            } else if (tag == 0x014Au) {
                uint32_t sub_cnt = tiff_u32(buf + ent + 4u, le);
                if (sub_cnt == 1u) {
                    if (q_tail < 8u) ifd_q[q_tail++] = val;
                } else if (sub_cnt >= 2u && sub_cnt <= 8u && val > 0u &&
                           val <= hdr_len && (hdr_len - val) >= sub_cnt * 4u) {
                    uint32_t s_i;
                    for (s_i = 0; s_i < sub_cnt && q_tail < 8u; s_i++) {
                        ifd_q[q_tail++] = tiff_u32(buf + val + s_i * 4u, le);
                    }
                }
            } else if (tag == 0x8769u) {
                if (q_tail < 8u) ifd_q[q_tail++] = val;
            }
        }
        if (jpg_off > 0u && jpg_len >= 1024u && jpg_len <= 4194304u &&
            jpg_off < file_size && jpg_len <= file_size - jpg_off) {
            if (jpg_len > best_len) {
                best_off = jpg_off;
                best_len = jpg_len;
            }
        }
        next_pos = ifd + 2u + count * 12u;
        if (next_pos + 4u <= hdr_len) {
            uint32_t next_ifd = tiff_u32(buf + next_pos, le);
            if (next_ifd > 0u && q_tail < 8u) ifd_q[q_tail++] = next_ifd;
        }
    }
    if (best_len == 0u) return -1;
    *out_off = best_off;
    *out_len = best_len;
    return 0;
}

static int serve_preview(ws_server *s, ws_conn *c, uint32_t seq, int *keep_open) {
    int32_t idx = ws_queue_find(s, seq);
    ws_entry *e;
    char path[WS_PATH_MAX];
    void *h = 0;
    uint32_t hdr_cap, hdr_len = 0;
    uint32_t prev_off = 0, prev_len = 0;
    uint32_t t0;
    char head[384];
    ws_buf b;

    if (idx < 0) return send_error_ka(c, 404, "Not Found", "{\"error\":\"unknown_seq\"}", *keep_open);
    e = &s->q.slot[(uint32_t)idx];
    if (e->ext != WS_EXT_ARW) {
        return send_error_ka(c, 404, "Not Found", "{\"error\":\"no_preview\"}", *keep_open);
    }
    if (ws_build_path(&s->cfg, e->index, e->ext, path) != 0) {
        *keep_open = 0;
        return send_error(c, 500, "Internal Server Error", "{\"error\":\"path\"}");
    }
    t0 = s->plat.now_ms ? s->plat.now_ms(s->plat.ctx) : 0u;
    if (s->fs.open(s->fs.ctx, path, &h) != 0) {
        s->errors++;
        *keep_open = 0;
        log_http(s, "preview_open_fail", seq);
        return send_error(c, 503, "Service Unavailable", "{\"error\":\"open_failed\"}");
    }
    hdr_cap = (s->io_cap < 65536u) ? s->io_cap : 65536u;
    if (hdr_cap > e->size) hdr_cap = e->size;
    while (hdr_len < hdr_cap) {
        int n = s->fs.read(s->fs.ctx, h, s->io + hdr_len, hdr_cap - hdr_len);
        if (n <= 0) break;
        hdr_len += (uint32_t)n;
    }
    if (find_arw_preview(s->io, hdr_len, e->size, &prev_off, &prev_len) != 0) {
        s->fs.close(s->fs.ctx, h);
        return send_error_ka(c, 404, "Not Found", "{\"error\":\"no_preview\"}", *keep_open);
    }

    if (prev_off < hdr_len) {
        uint32_t avail = hdr_len - prev_off;
        uint32_t in_buf = (prev_len <= avail) ? prev_len : avail;
        uint32_t left;
        if (avail < 2u || s->io[prev_off] != 0xFFu || s->io[prev_off + 1u] != 0xD8u) {
            s->fs.close(s->fs.ctx, h);
            return send_error_ka(c, 404, "Not Found", "{\"error\":\"no_preview\"}", *keep_open);
        }
        buf_init(&b, head, sizeof(head));
        buf_puts(&b, "HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: ");
        buf_putu(&b, prev_len);
        buf_puts(&b, "\r\nX-Preview-Size: ");
        buf_putu(&b, prev_len);
        buf_puts(&b, "\r\nX-File-Size: ");
        buf_putu(&b, e->size);
        buf_puts(&b, "\r\nX-File-Name: ");
        buf_puts(&b, e->name);
        buf_puts(&b, "\r\nX-WS-Protocol: 1");
        buf_puts(&b, conn_hdr(*keep_open));
        if (b.over || c->send(c->ctx, head, b.len) != 0) {
            s->fs.close(s->fs.ctx, h);
            *keep_open = 0;
            return -1;
        }
        if (c->send(c->ctx, s->io + prev_off, in_buf) != 0) {
            s->fs.close(s->fs.ctx, h);
            *keep_open = 0;
            return -1;
        }
        left = prev_len - in_buf;
        while (left) {
            uint32_t want = (left > s->io_cap) ? s->io_cap : left;
            int n = s->fs.read(s->fs.ctx, h, s->io, want);
            if (n <= 0 || c->send(c->ctx, s->io, (uint32_t)n) != 0) {
                s->fs.close(s->fs.ctx, h);
                *keep_open = 0;
                return -1;
            }
            left -= (uint32_t)n;
        }
    } else {
        uint32_t first_want = (prev_len < s->io_cap) ? prev_len : s->io_cap;
        uint32_t first_len = 0, left;
        if (skip_bytes(s, h, prev_off - hdr_len, 0) != 0) {
            s->fs.close(s->fs.ctx, h);
            *keep_open = 0;
            return send_error(c, 503, "Service Unavailable", "{\"error\":\"read_failed\"}");
        }
        while (first_len < first_want) {
            int n = s->fs.read(s->fs.ctx, h, s->io + first_len, first_want - first_len);
            if (n <= 0) break;
            first_len += (uint32_t)n;
        }
        if (first_len < 2u || s->io[0] != 0xFFu || s->io[1] != 0xD8u) {
            s->fs.close(s->fs.ctx, h);
            return send_error_ka(c, 404, "Not Found", "{\"error\":\"no_preview\"}", *keep_open);
        }
        buf_init(&b, head, sizeof(head));
        buf_puts(&b, "HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: ");
        buf_putu(&b, prev_len);
        buf_puts(&b, "\r\nX-Preview-Size: ");
        buf_putu(&b, prev_len);
        buf_puts(&b, "\r\nX-File-Size: ");
        buf_putu(&b, e->size);
        buf_puts(&b, "\r\nX-File-Name: ");
        buf_puts(&b, e->name);
        buf_puts(&b, "\r\nX-WS-Protocol: 1");
        buf_puts(&b, conn_hdr(*keep_open));
        if (b.over || c->send(c->ctx, head, b.len) != 0 ||
            c->send(c->ctx, s->io, first_len) != 0) {
            s->fs.close(s->fs.ctx, h);
            *keep_open = 0;
            return -1;
        }
        left = prev_len - first_len;
        while (left) {
            uint32_t want = (left > s->io_cap) ? s->io_cap : left;
            int n = s->fs.read(s->fs.ctx, h, s->io, want);
            if (n <= 0 || c->send(c->ctx, s->io, (uint32_t)n) != 0) {
                s->fs.close(s->fs.ctx, h);
                *keep_open = 0;
                return -1;
            }
            left -= (uint32_t)n;
        }
    }

    s->fs.close(s->fs.ctx, h);
    s->last_activity_ms = s->plat.now_ms ? s->plat.now_ms(s->plat.ctx) : 0u;
    log_http(s, "preview_served", seq);
    log_http(s, "preview_ms", (uint32_t)(s->last_activity_ms - t0));
    return 0;
}

static int serve_file(ws_server *s, ws_conn *c, uint32_t seq,
                      uint8_t have_range, uint32_t range_start, uint32_t range_end,
                      uint8_t have_end, int *keep_open) {
    int32_t idx = ws_queue_find(s, seq);
    ws_entry *e;
    char path[WS_PATH_MAX];
    void *h = 0;
    char head[512];
    ws_buf b;
    uint32_t start, end, total;
    int n;

    if (idx < 0) { *keep_open = 0; return send_error(c, 404, "Not Found", "{\"error\":\"unknown_seq\"}"); }
    e = &s->q.slot[(uint32_t)idx];
    if (ws_build_path(&s->cfg, e->index, e->ext, path) != 0) {
        *keep_open = 0;
        return send_error(c, 500, "Internal Server Error", "{\"error\":\"path\"}");
    }
    if (e->crc_state != WS_CRC_KNOWN) {
        uint32_t sz = 0, mt = 0;
        if (s->fs.stat(s->fs.ctx, path, &sz, &mt) == 0 && sz >= ws_min_size_for(s, e->ext)) {
            e->size = sz;
            e->mtime = mt;
        }
    }
    total = e->size;
    start = 0;
    end = total;
    if (have_range) {
        if (range_start >= total) {
            char hdr[256];
            ws_buf rb;
            *keep_open = 0;
            buf_init(&rb, hdr, sizeof(hdr));
            buf_puts(&rb, "HTTP/1.1 416 Range Not Satisfiable\r\nContent-Range: bytes */");
            buf_putu(&rb, total);
            buf_puts(&rb, "\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
            return c->send(c->ctx, hdr, rb.len);
        }
        start = range_start;
        if (have_end && range_end + 1u < total) end = range_end + 1u;
    }
    if (s->fs.open(s->fs.ctx, path, &h) != 0) {
        s->errors++;
        *keep_open = 0;
        log_http(s, "serve_open_fail", seq);
        return send_error(c, 503, "Service Unavailable", "{\"error\":\"open_failed\"}");
    }
    {
        uint8_t stream_crc = (end == total && e->crc_state != WS_CRC_KNOWN) ? 1u : 0u;
        uint32_t crc_acc = 0xFFFFFFFFu;
        uint32_t t0 = s->plat.now_ms ? s->plat.now_ms(s->plat.ctx) : 0u;
        uint32_t read_ms = 0u, crc_ms = 0u, send_ms = 0u;

        buf_init(&b, head, sizeof(head));
        buf_puts(&b, have_range ? "HTTP/1.1 206 Partial Content\r\n" : "HTTP/1.1 200 OK\r\n");
        buf_puts(&b, "Content-Type: ");
        buf_puts(&b, (e->ext == WS_EXT_ARW) ? "image/x-sony-arw" : "image/jpeg");
        buf_puts(&b, "\r\nContent-Length: ");
        buf_putu(&b, end - start);
        buf_puts(&b, "\r\nAccept-Ranges: bytes\r\nX-File-Size: ");
        buf_putu(&b, total);
        buf_puts(&b, "\r\nX-CRC32: ");
        if (e->crc_state == WS_CRC_KNOWN) buf_puthex8(&b, e->crc32);
        else buf_puts(&b, "pending");
        buf_puts(&b, "\r\nX-File-Name: ");
        buf_puts(&b, e->name);
        buf_puts(&b, "\r\nX-WS-Protocol: 1");
        if (have_range) {
            buf_puts(&b, "\r\nContent-Range: bytes ");
            buf_putu(&b, start);
            buf_putc(&b, '-');
            buf_putu(&b, end - 1u);
            buf_putc(&b, '/');
            buf_putu(&b, total);
        }
        buf_puts(&b, conn_hdr(*keep_open));
        if (b.over || c->send(c->ctx, head, b.len) != 0) {
            s->fs.close(s->fs.ctx, h);
            *keep_open = 0;
            return -1;
        }
        if (start && skip_bytes(s, h, start, stream_crc ? &crc_acc : 0) != 0) {
            s->fs.close(s->fs.ctx, h);
            s->errors++;
            *keep_open = 0;
            return -1;
        }
        {
            uint32_t left = end - start;
            while (left) {
                uint32_t want = (left > s->io_cap) ? s->io_cap : left;
                uint32_t p0 = s->plat.now_ms ? s->plat.now_ms(s->plat.ctx) : 0u;
                n = s->fs.read(s->fs.ctx, h, s->io, want);
                if (s->plat.now_ms) {
                    uint32_t p1 = s->plat.now_ms(s->plat.ctx);
                    read_ms += (uint32_t)(p1 - p0);
                    p0 = p1;
                }
                if (n <= 0) { s->fs.close(s->fs.ctx, h); s->errors++; *keep_open = 0; return -1; }
                if (stream_crc) crc_acc = ws_crc32_update(s, crc_acc, s->io, (uint32_t)n);
                if (s->plat.now_ms) {
                    uint32_t p1 = s->plat.now_ms(s->plat.ctx);
                    crc_ms += (uint32_t)(p1 - p0);
                    p0 = p1;
                }
                if (c->send(c->ctx, s->io, (uint32_t)n) != 0) {
                    s->fs.close(s->fs.ctx, h);
                    *keep_open = 0;
                    return -1;
                }
                if (s->plat.now_ms) {
                    uint32_t p1 = s->plat.now_ms(s->plat.ctx);
                    send_ms += (uint32_t)(p1 - p0);
                }
                s->bytes_sent += (uint32_t)n;
                left -= (uint32_t)n;
            }
        }
        s->fs.close(s->fs.ctx, h);
        if (stream_crc) {
            e->crc32 = crc_acc ^ 0xFFFFFFFFu;
            e->crc_state = WS_CRC_KNOWN;
            s->events++;
        }
        s->files_served++;
        s->last_activity_ms = s->plat.now_ms ? s->plat.now_ms(s->plat.ctx) : 0u;
        log_http(s, "file_served", seq);
        log_http(s, "serve_read_ms", read_ms);
        log_http(s, "serve_crc_ms", crc_ms);
        log_http(s, "serve_send_ms", send_ms);
        log_http(s, "serve_ms", (uint32_t)(s->last_activity_ms - t0));
    }
    return 0;
}

static uint32_t next_ready_unacked(const ws_server *s) {
    uint32_t seq;
    for (seq = s->q.ack + 1u; seq <= s->q.head; seq++) {
        const ws_entry *e = &s->q.slot[ws_slot_of(s, seq)];
        if (e->seq == seq && e->ready) return seq;
    }
    return 0u;
}

static int handle_ack(ws_server *s, ws_conn *c, uint32_t seq, const char *body, uint32_t body_len) {
    int32_t idx;
    ws_entry *e;
    uint32_t want_crc = 0, want_size = 0, expected;
    int has_crc = 0, has_size = 0;
    char out[96];
    ws_buf b;
    if (body && body_len) {
        const char *p = body;
        /* Minimal key scan: {"crc32":"1a2b3c4d","size":12345} */
        while ((p = find_key(p, body + body_len, "crc32", 5)) != 0) {
            uint32_t v = 0;
            int i;
            for (i = 0; i < 8 && p[i]; i++) {
                char ch = p[i];
                v <<= 4;
                if (ch >= '0' && ch <= '9') v |= (uint32_t)(ch - '0');
                else if (ch >= 'a' && ch <= 'f') v |= (uint32_t)(ch - 'a' + 10);
                else if (ch >= 'A' && ch <= 'F') v |= (uint32_t)(ch - 'A' + 10);
                else break;
            }
            if (i) { want_crc = v; has_crc = 1; }
            break;
        }
        p = body;
        while ((p = find_key(p, body + body_len, "size", 4)) != 0) {
            if (ws_parse_u32(p, &want_size) == 0) { has_size = 1; }
            break;
        }
    }
    idx = ws_queue_find(s, seq);
    if (idx < 0) {
        /* An acknowledgement is idempotent: repeating it after the watermark
         * moved reports the same success without touching the queue. */
        if (seq != 0u && seq <= s->q.ack && seq <= s->q.head) {
            buf_init(&b, out, sizeof(out));
            buf_puts(&b, "{\"acked\":true,\"ack\":");
            buf_putu(&b, s->q.ack);
            buf_puts(&b, ",\"head\":");
            buf_putu(&b, s->q.head);
            buf_puts(&b, ",\"duplicate\":true}");
            if (send_str(c, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ") != 0)
                return -1;
            {
                char lenbuf[12];
                ws_buf lb;
                buf_init(&lb, lenbuf, sizeof(lenbuf));
                buf_putu(&lb, b.len);
                if (lb.over || c->send(c->ctx, lenbuf, lb.len) != 0) return -1;
            }
            if (send_str(c, "\r\nConnection: close\r\n\r\n") != 0) return -1;
            return c->send(c->ctx, out, b.len);
        }
        return send_error(c, 404, "Not Found", "{\"error\":\"unknown_seq\"}");
    }
    expected = next_ready_unacked(s);
    if (expected != 0u && seq != expected) {
        buf_init(&b, out, sizeof(out));
        buf_puts(&b, "{\"error\":\"non_contiguous_ack\",\"expected\":");
        buf_putu(&b, expected);
        buf_puts(&b, "}");
        return send_error(c, 409, "Conflict", out);
    }
    e = &s->q.slot[(uint32_t)idx];
    if (has_size && want_size != e->size)
        return send_error(c, 409, "Conflict", "{\"error\":\"size_mismatch\"}");
    if (has_crc && e->crc_state == WS_CRC_KNOWN && want_crc != e->crc32)
        return send_error(c, 409, "Conflict", "{\"error\":\"crc_mismatch\"}");
    ws_queue_ack(s, seq);
    buf_init(&b, out, sizeof(out));
    buf_puts(&b, "{\"acked\":true,\"ack\":");
    buf_putu(&b, s->q.ack);
    buf_puts(&b, ",\"head\":");
    buf_putu(&b, s->q.head);
    buf_puts(&b, "}");
    if (b.over) return -1;
    if (send_str(c, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ") != 0)
        return -1;
    {
        char lenbuf[12];
        ws_buf lb;
        buf_init(&lb, lenbuf, sizeof(lenbuf));
        buf_putu(&lb, b.len);
        if (lb.over || c->send(c->ctx, lenbuf, lb.len) != 0) return -1;
    }
    if (send_str(c, "\r\nConnection: close\r\n\r\n") != 0) return -1;
    log_http(s, "acked", seq);
    return c->send(c->ctx, out, b.len);
}

static const char *find_key(const char *from, const char *end, const char *key, uint32_t key_len) {
    const char *p = from;
    while (p && p < end) {
        if (ws_strneq(p, key, key_len) && p + key_len < end) {
            const char *q = p + key_len;
            while (q < end && (*q == ' ' || *q == ':' || *q == '=' || *q == '"' || *q == '\t')) q++;
            return q;
        }
        p++;
    }
    return 0;
}

static void parse_range(const char *v, uint8_t *have, uint32_t *start,
                        uint8_t *have_end, uint32_t *end) {
    *have = 0; *have_end = 0; *start = 0; *end = 0;
    if (!v) return;
    while (*v == ' ') v++;
    if (!ws_strneq(v, "bytes=", 6)) return;
    v += 6;
    if (ws_parse_u32(v, start) != 0) return;
    while (*v && *v != '-') v++;
    if (*v != '-') return;
    *have = 1;
    v++;
    if (*v >= '0' && *v <= '9') {
        if (ws_parse_u32(v, end) == 0) *have_end = 1;
    }
}

static int wait_event(ws_server *s, ws_conn *c, uint32_t since, int *keep_open) {
    uint32_t waited = 0;
    uint32_t start_head = s->q.head;
    uint32_t start_events = s->events;
    int woke = 0;
    while (waited < WS_EVENT_TIMEOUT_MS) {
        int r = c->wait ? c->wait(c->ctx, WS_EVENT_SLICE_MS) : 0;
        if (r < 0) { *keep_open = 0; return -1; }
        if (s->plat.should_stop && s->plat.should_stop(s->plat.ctx)) {
            *keep_open = 0;
            return send_error(c, 503, "Service Unavailable", "{\"error\":\"stopping\"}");
        }
        if (c->idle) c->idle(c->ctx);
        waited += WS_EVENT_SLICE_MS;
        if (s->q.head != start_head || s->events != start_events) { woke = 1; break; }
    }
    {
        char out[96];
        ws_buf b;
        buf_init(&b, out, sizeof(out));
        buf_puts(&b, "{\"head\":");
        buf_putu(&b, s->q.head);
        buf_puts(&b, ",\"since\":");
        buf_putu(&b, since);
        buf_puts(&b, ",\"pending\":");
        buf_putu(&b, ws_queue_pending(s));
        buf_puts(&b, ",\"woke\":");
        buf_puts(&b, woke ? "true" : "false");
        buf_puts(&b, "}");
        if (b.over) { *keep_open = 0; return -1; }
        if (send_str(c, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ") != 0) {
            *keep_open = 0;
            return -1;
        }
        {
            char lenbuf[12];
            ws_buf lb;
            buf_init(&lb, lenbuf, sizeof(lenbuf));
            buf_putu(&lb, b.len);
            if (lb.over || c->send(c->ctx, lenbuf, lb.len) != 0) { *keep_open = 0; return -1; }
        }
        if (send_str(c, conn_hdr(*keep_open)) != 0) { *keep_open = 0; return -1; }
        return c->send(c->ctx, out, b.len);
    }
}

typedef struct ws_http_rx {
    char     buf[WS_REQ_MAX];
    uint32_t len;
} ws_http_rx;

static int ws_http_serve_one(ws_server *s, ws_conn *c, ws_http_rx *rx,
                             int allow_keepalive, int *keep_open, int is_first) {
    uint32_t head_len = 0;
    int n, found = 0;
    const char *line, *sp1, *sp2, *ver, *conn_val;
    char method[8];
    char target[96];
    char prebody[256];
    uint32_t prebody_len = 0, extra = 0;
    uint32_t i;
    const char *range;
    uint8_t have_range = 0, have_end = 0;
    uint32_t range_start = 0, range_end = 0;
    uint32_t seq = 0;
    const char *content_length;
    int want_ka = 0;
    int rc;
#define WS_FAIL(code) do { *keep_open = 0; s->errors++; if (s->plat.log) s->plat.log(s->plat.ctx, "http_fail", (code)); return -1; } while (0)

    *keep_open = 0;
    while (1) {
        for (i = 0; i + 3u < rx->len; i++) {
            if (rx->buf[i] == '\r' && rx->buf[i + 1] == '\n' &&
                rx->buf[i + 2] == '\r' && rx->buf[i + 3] == '\n') {
                head_len = i + 4u;
                found = 1;
                break;
            }
        }
        if (found) break;
        if (rx->len + 1u >= sizeof(rx->buf)) break;
        n = c->recv(c->ctx, rx->buf + rx->len, (uint32_t)(sizeof(rx->buf) - 1u - rx->len));
        if (n <= 0) {
            if (!is_first && rx->len == 0u) return 0;
            WS_FAIL(1);
        }
        rx->len += (uint32_t)n;
        rx->buf[rx->len] = 0;
    }
    if (!found || head_len < 8u) {
        rx->len = 0;
        return send_error(c, 400, "Bad Request", "{\"error\":\"short_request\"}");
    }
    extra = rx->len - head_len;
    if (extra > 0u) {
        prebody_len = (extra > sizeof(prebody)) ? (uint32_t)sizeof(prebody) : extra;
        ws_memcpy(prebody, rx->buf + head_len, prebody_len);
    }
    /* Bound header scans to the current request head (replaces trailing LF with NUL). */
    rx->buf[head_len - 1u] = 0;

    line = rx->buf;
    sp1 = line;
    while (*sp1 && *sp1 != ' ') sp1++;
    sp2 = (*sp1) ? sp1 + 1 : sp1;
    while (*sp2 && *sp2 != ' ') sp2++;
    if (!*sp1 || !*sp2) {
        rx->len = 0;
        return send_error(c, 400, "Bad Request", "{\"error\":\"bad_line\"}");
    }
    if ((uint32_t)(sp1 - line) >= sizeof(method)) WS_FAIL(2);
    for (i = 0; line + i < sp1; i++) method[i] = line[i];
    method[i] = 0;
    if ((uint32_t)(sp2 - (sp1 + 1)) >= sizeof(target)) WS_FAIL(3);
    for (i = 0; sp1 + 1 + i < sp2; i++) target[i] = sp1[1 + i];
    target[i] = 0;

    ver = sp2 + 1;
    want_ka = ws_strneq(ver, "HTTP/1.1", 8) ? 1 : 0;
    conn_val = header_value(rx->buf, "Connection", 10);
    if (conn_val) {
        if (ci_contains(conn_val, "close")) want_ka = 0;
        else if (ci_contains(conn_val, "keep-alive")) want_ka = 1;
    }

    range = header_value(rx->buf, "Range", 5);
    parse_range(range, &have_range, &range_start, &have_end, &range_end);

    if (ws_streq(method, "GET") && ws_streq(target, "/status")) {
        *keep_open = allow_keepalive && want_ka;
        if (extra) { ws_memcpy(rx->buf, rx->buf + head_len, extra); rx->len = extra; rx->buf[extra] = 0; }
        else rx->len = 0;
        return send_status(s, c, *keep_open);
    }
    if (ws_streq(method, "GET") && ws_streq(target, "/")) {
        *keep_open = allow_keepalive && want_ka;
        if (extra) { ws_memcpy(rx->buf, rx->buf + head_len, extra); rx->len = extra; rx->buf[extra] = 0; }
        else rx->len = 0;
        return send_error_ka(c, 200, "OK", "{\"service\":\"wifisync\",\"protocol\":1}", *keep_open);
    }
    if (ws_streq(method, "GET") && ws_strneq(target, "/events", 7)) {
        uint32_t since = 0;
        const char *q = target;
        while (*q && *q != '?') q++;
        if (*q == '?') {
            const char *pv = find_key(q, q + ws_strlen(q), "since", 5);
            if (pv) ws_parse_u32(pv, &since);
        }
        *keep_open = allow_keepalive && want_ka;
        if (extra) { ws_memcpy(rx->buf, rx->buf + head_len, extra); rx->len = extra; rx->buf[extra] = 0; }
        else rx->len = 0;
        return wait_event(s, c, since, keep_open);
    }
    if (ws_streq(method, "GET") && ws_strneq(target, "/manifest", 9)) {
        uint32_t since = 0;
        const char *q = target;
        while (*q && *q != '?') q++;
        if (*q == '?') {
            const char *pv = find_key(q, q + ws_strlen(q), "since", 5);
            if (pv) ws_parse_u32(pv, &since);
        }
        *keep_open = allow_keepalive && want_ka;
        if (extra) { ws_memcpy(rx->buf, rx->buf + head_len, extra); rx->len = extra; rx->buf[extra] = 0; }
        else rx->len = 0;
        return send_manifest(s, c, since, *keep_open);
    }
    if (ws_strneq(target, "/preview/", 9)) {
        if (!ws_streq(method, "GET")) {
            rx->len = 0;
            return send_error(c, 405, "Method Not Allowed", "{\"error\":\"method\"}");
        }
        if (ws_parse_u32(target + 9, &seq) != 0) {
            rx->len = 0;
            return send_error(c, 400, "Bad Request", "{\"error\":\"seq\"}");
        }
        *keep_open = allow_keepalive && want_ka;
        if (extra) { ws_memcpy(rx->buf, rx->buf + head_len, extra); rx->len = extra; rx->buf[extra] = 0; }
        else rx->len = 0;
        return serve_preview(s, c, seq, keep_open);
    }
    if (ws_strneq(target, "/file/", 6)) {
        if (!ws_streq(method, "GET")) {
            rx->len = 0;
            return send_error(c, 405, "Method Not Allowed", "{\"error\":\"method\"}");
        }
        if (ws_parse_u32(target + 6, &seq) != 0) {
            rx->len = 0;
            return send_error(c, 400, "Bad Request", "{\"error\":\"seq\"}");
        }
        *keep_open = allow_keepalive && want_ka;
        if (extra) { ws_memcpy(rx->buf, rx->buf + head_len, extra); rx->len = extra; rx->buf[extra] = 0; }
        else rx->len = 0;
        return serve_file(s, c, seq, have_range, range_start, range_end, have_end, keep_open);
    }
    if (ws_strneq(target, "/ack/", 5)) {
        char body[256];
        uint32_t blen = 0;
        if (!ws_streq(method, "POST")) {
            rx->len = 0;
            return send_error(c, 405, "Method Not Allowed", "{\"error\":\"method\"}");
        }
        if (ws_parse_u32(target + 5, &seq) != 0) {
            rx->len = 0;
            return send_error(c, 400, "Bad Request", "{\"error\":\"seq\"}");
        }
        content_length = header_value(rx->buf, "Content-Length", 14);
        if (content_length) {
            uint32_t want = 0;
            if (ws_parse_u32(content_length, &want) == 0) {
                uint32_t take;
                if (want > sizeof(body) - 1u) want = (uint32_t)(sizeof(body) - 1u);
                take = (prebody_len < want) ? prebody_len : want;
                if (take) { ws_memcpy(body, prebody, take); blen = take; }
                while (blen < want) {
                    n = c->recv(c->ctx, body + blen, want - blen);
                    if (n <= 0) break;
                    blen += (uint32_t)n;
                }
                body[blen] = 0;
            }
        }
        rx->len = 0;
        return handle_ack(s, c, seq, body, blen);
    }
    rx->len = 0;
    rc = send_error(c, 404, "Not Found", "{\"error\":\"no_route\"}");
    return rc;
#undef WS_FAIL
}

int ws_http_serve(ws_server *s, ws_conn *c) {
    ws_http_rx rx;
    int keep_open = 0;
    rx.len = 0;
    rx.buf[0] = 0;
    return ws_http_serve_one(s, c, &rx, 0, &keep_open, 1);
}

int ws_http_serve_conn(ws_server *s, ws_conn *c, uint32_t max_reqs) {
    ws_http_rx rx;
    uint32_t count = 0;
    uint32_t limit = max_reqs ? max_reqs : 1u;
    int keep_open = 1;
    rx.len = 0;
    rx.buf[0] = 0;
    while (keep_open && count < limit) {
        int rc;
        keep_open = 0;
        rc = ws_http_serve_one(s, c, &rx, (count + 1u < limit), &keep_open, count == 0u);
        if (rc != 0) return rc;
        if (count > 0u && !keep_open && rx.len == 0u) break;
        count++;
    }
    return 0;
}

/* ------------------------------------------------------------- init */

void ws_init(ws_server *s, const ws_config *cfg, const ws_fs_ops *fs,
             const ws_platform *plat, uint8_t *io, uint32_t io_cap) {
    ws_memset(s, 0, sizeof(*s));
    ws_memcpy(&s->cfg, cfg, sizeof(*cfg));
    ws_memcpy(&s->fs, fs, sizeof(*fs));
    ws_memcpy(&s->plat, plat, sizeof(*plat));
    s->io = io;
    s->io_cap = io_cap;
    s->power = WS_POWER_OFF;
    s->start_ms = plat->now_ms ? plat->now_ms(plat->ctx) : 0u;
    s->last_activity_ms = s->start_ms;
    s->fw[0] = 0;
    ws_crc_init(s);
}
