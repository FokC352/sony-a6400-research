/* Bounded v2 package reader for FAT32 and exFAT SD cards (/dev/mmca1). */
#define _DEFAULT_SOURCE
#define _FILE_OFFSET_BITS 64
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

enum { SECTOR = 512, IO_CHUNK = 64 * SECTOR, MAX_ARCHIVE = 4u * 1024u * 1024u };
#define END_CLUSTER_FAT32 0x0ffffff8u
#define END_CLUSTER_EXFAT 0xfffffff8u
#ifndef STAGE
#define STAGE "/tmp_bt/mlv2"
#endif

struct fs_info {
    int is_exfat;
    uint32_t spc;
    uint32_t fat_off;
    uint32_t heap_off;
    uint32_t cluster_count;
    uint32_t root_cluster;
    uint64_t total_sectors;
};

struct entry {
    uint32_t cluster;
    uint32_t bytes;
    unsigned directory;
    unsigned no_fat_chain;
};

static unsigned char io_buf[IO_CHUNK];

static uint16_t le16(const unsigned char *p) { return (uint16_t)(p[0] | (uint16_t)p[1] << 8); }
static uint32_t le32(const unsigned char *p) { return (uint32_t)le16(p) | (uint32_t)le16(p + 2) << 16; }
static uint64_t le64(const unsigned char *p) { return (uint64_t)le32(p) | (uint64_t)le32(p + 4) << 32; }

static int read_at(int fd, void *out, uint64_t lba, size_t size, const struct fs_info *fs) {
    if (size > IO_CHUNK) return -1;
    if (fs && fs->total_sectors && (lba >= fs->total_sectors ||
        (uint64_t)((size + SECTOR - 1) / SECTOR) > fs->total_sectors - lba)) return -1;
    return pread(fd, out, size, (off_t)(lba * SECTOR)) == (ssize_t)size ? 0 : -1;
}

static int valid_cluster(const struct fs_info *fs, uint32_t c) {
    return c >= 2u && (c - 2u) < fs->cluster_count;
}

static uint64_t cluster_lba(const struct fs_info *fs, uint32_t c) {
    return (uint64_t)fs->heap_off + (uint64_t)(c - 2u) * fs->spc;
}

static int next_cluster(int fd, const struct fs_info *fs, uint32_t *cluster) {
    unsigned char sector[SECTOR];
    uint64_t index = (uint64_t)(*cluster) * 4u;
    if (read_at(fd, sector, fs->fat_off + index / SECTOR, sizeof(sector), fs)) return -1;
    uint32_t val = le32(sector + index % SECTOR);
    *cluster = fs->is_exfat ? val : (val & 0x0fffffffu);
    return 0;
}

static int eq_ascii_ci(const char *a, const char *b) {
    return strcasecmp(a, b) == 0;
}

static int parse_fs(int fd, struct fs_info *fs) {
    unsigned char sec[SECTOR];
    memset(fs, 0, sizeof(*fs));
    if (read_at(fd, sec, 0, sizeof(sec), NULL) || le16(sec + 510) != 0xaa55) return -1;
    if (!memcmp(sec + 3, "EXFAT   ", 8)) {
        uint8_t bps_shift = sec[108], spc_shift = sec[109];
        if (bps_shift != 9 || spc_shift > 16) return -1;
        fs->is_exfat = 1;
        fs->spc = 1u << spc_shift;
        fs->fat_off = le32(sec + 80);
        fs->heap_off = le32(sec + 88);
        fs->cluster_count = le32(sec + 92);
        fs->root_cluster = le32(sec + 96);
        fs->total_sectors = le64(sec + 72);
        if (!fs->spc || !fs->cluster_count || !valid_cluster(fs, fs->root_cluster)) return -1;
        return 0;
    }
    if (le16(sec + 11) == SECTOR && sec[13] && (sec[13] & (sec[13] - 1)) == 0 &&
        sec[16] >= 1 && le32(sec + 36) > 0 && le32(sec + 44) >= 2) {
        uint32_t rsvd = le16(sec + 14);
        uint32_t fatsz = le32(sec + 36);
        uint32_t tot = le32(sec + 32);
        fs->is_exfat = 0;
        fs->spc = sec[13];
        fs->fat_off = rsvd;
        fs->heap_off = rsvd + (uint32_t)sec[16] * fatsz;
        fs->total_sectors = tot;
        if (tot <= fs->heap_off) return -1;
        fs->cluster_count = (tot - fs->heap_off) / fs->spc;
        fs->root_cluster = le32(sec + 44);
        if (!valid_cluster(fs, fs->root_cluster)) return -1;
        return 0;
    }
    return -1;
}

static int find_fat32(int fd, const struct fs_info *fs, const char *wanted, struct entry *match) {
    uint32_t cluster = fs->root_cluster;
    char lfn[66] = {0};
    unsigned remaining = 0, sequence = 0;
    unsigned char checksum = 0;
    static const unsigned offsets[] = {1,3,5,7,9,14,16,18,20,22,24,28,30};
    for (unsigned block = 0; cluster < END_CLUSTER_FAT32 && block < 8; block++) {
        if (!valid_cluster(fs, cluster)) return -1;
        uint64_t lba = cluster_lba(fs, cluster);
        uint32_t cl_bytes = fs->spc * SECTOR;
        for (uint32_t pos = 0; pos < cl_bytes; pos += IO_CHUNK) {
            size_t sz = (cl_bytes - pos) < IO_CHUNK ? (cl_bytes - pos) : IO_CHUNK;
            if (read_at(fd, io_buf, lba + pos / SECTOR, sz, fs)) return -1;
            for (size_t off = 0; off < sz; off += 32) {
                const unsigned char *e = io_buf + off;
                if (!e[0]) return 0;
                if (e[0] == 0xe5) { sequence = 0; continue; }
                if (e[11] == 15) {
                    unsigned order = e[0] & 31;
                    if (e[0] & 64) {
                        sequence = remaining = order <= 5 ? order : 0;
                        checksum = e[13];
                        memset(lfn, 0, sizeof(lfn));
                    }
                    if (!order || order != remaining || e[13] != checksum) { sequence = 0; continue; }
                    for (unsigned i = 0; i < 13; i++) {
                        uint32_t c = le16(e + offsets[i]);
                        lfn[(order - 1) * 13 + i] = c == 0xffff ? 0 : c <= 127 ? (char)c : '?';
                    }
                    remaining--;
                    continue;
                }
                unsigned char actual = 0;
                for (unsigned i = 0; i < 11; i++)
                    actual = (unsigned char)(((actual & 1 ? 128 : 0) | actual >> 1) + e[i]);
                char short_name[13];
                unsigned n = 0;
                for (unsigned i = 0; i < 8 && e[i] != ' '; i++) short_name[n++] = (char)e[i];
                if (e[8] != ' ') {
                    short_name[n++] = '.';
                    for (unsigned i = 8; i < 11 && e[i] != ' '; i++) short_name[n++] = (char)e[i];
                }
                short_name[n] = 0;
                const char *name = sequence && !remaining && actual == checksum ? lfn : short_name;
                sequence = 0;
                if (!(e[11] & 8) && eq_ascii_ci(name, wanted)) {
                    match->cluster = (uint32_t)le16(e + 20) << 16 | le16(e + 26);
                    match->bytes = le32(e + 28);
                    match->directory = !!(e[11] & 16);
                    match->no_fat_chain = 0;
                    return 1;
                }
            }
        }
        if (next_cluster(fd, fs, &cluster)) return -1;
    }
    return cluster >= END_CLUSTER_FAT32 ? 0 : -1;
}

static int find_exfat(int fd, const struct fs_info *fs, const char *wanted, struct entry *match) {
    uint32_t cluster = fs->root_cluster;
    unsigned sec_left = 0, name_len = 0, name_pos = 0;
    unsigned attr = 0, no_chain = 0;
    uint32_t f_cluster = 0;
    uint64_t f_len = 0;
    char name[64];

    for (unsigned block = 0; cluster >= 2u && cluster < END_CLUSTER_EXFAT && block < 8; block++) {
        if (!valid_cluster(fs, cluster)) return -1;
        uint64_t lba = cluster_lba(fs, cluster);
        uint32_t cl_bytes = fs->spc * SECTOR;
        for (uint32_t pos = 0; pos < cl_bytes; pos += IO_CHUNK) {
            size_t sz = (cl_bytes - pos) < IO_CHUNK ? (cl_bytes - pos) : IO_CHUNK;
            if (read_at(fd, io_buf, lba + pos / SECTOR, sz, fs)) return -1;
            for (size_t off = 0; off < sz; off += 32) {
                const unsigned char *e = io_buf + off;
                uint8_t et = e[0];
                if (!et) return 0;
                if (!(et & 0x80u)) { sec_left = 0; continue; }
                if (et == 0x85u) {
                    sec_left = e[1];
                    attr = le16(e + 4);
                    name_len = 0;
                    name_pos = 0;
                    f_cluster = 0;
                    f_len = 0;
                    no_chain = 0;
                    memset(name, 0, sizeof(name));
                    continue;
                }
                if (!sec_left) continue;
                sec_left--;
                if (et == 0xc0u) {
                    no_chain = !!(e[1] & 2u);
                    name_len = e[3];
                    f_cluster = le32(e + 20);
                    f_len = le64(e + 24);
                } else if (et == 0xc1u) {
                    for (unsigned i = 0; i < 15 && name_pos < name_len && name_pos + 1 < sizeof(name); i++) {
                        uint16_t ch = le16(e + 2 + i * 2);
                        name[name_pos++] = (ch && ch <= 127) ? (char)ch : '?';
                    }
                    name[name_pos] = '\0';
                    if (sec_left == 0 && name_len && eq_ascii_ci(name, wanted)) {
                        match->cluster = f_cluster;
                        match->bytes = (f_len > MAX_ARCHIVE) ? (MAX_ARCHIVE + 1u) : (uint32_t)f_len;
                        match->directory = !!(attr & 0x10u);
                        match->no_fat_chain = no_chain;
                        return 1;
                    }
                }
            }
        }
        if (next_cluster(fd, fs, &cluster)) return -1;
    }
    return cluster >= END_CLUSTER_EXFAT ? 0 : -1;
}

static int find_root(int fd, const struct fs_info *fs, const char *wanted, struct entry *match) {
    return fs->is_exfat ? find_exfat(fd, fs, wanted, match) : find_fat32(fd, fs, wanted, match);
}

static int extract(int fd, const struct fs_info *fs, struct entry item) {
    int out = open(STAGE "/card.tar", O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
    if (out < 0) return -1;
    int result = 0;
    uint32_t cl_bytes = fs->spc * SECTOR;
    while (item.bytes) {
        if (!valid_cluster(fs, item.cluster)) { result = -1; break; }
        uint64_t lba = cluster_lba(fs, item.cluster);
        uint32_t rem_cl = item.bytes < cl_bytes ? item.bytes : cl_bytes;
        uint32_t pos = 0;
        while (pos < rem_cl) {
            size_t sz = (rem_cl - pos) < IO_CHUNK ? (rem_cl - pos) : IO_CHUNK;
            size_t rsz = (sz + SECTOR - 1) & ~(size_t)(SECTOR - 1);
            if (read_at(fd, io_buf, lba + pos / SECTOR, rsz, fs) ||
                write(out, io_buf, sz) != (ssize_t)sz) {
                result = -1;
                break;
            }
            pos += (uint32_t)sz;
        }
        if (result) break;
        item.bytes -= rem_cl;
        if (item.bytes) {
            if (item.no_fat_chain) item.cluster++;
            else if (next_cluster(fd, fs, &item.cluster)) { result = -1; break; }
        }
    }
    if (close(out)) result = -1;
    if (result) unlink(STAGE "/card.tar");
    return result;
}

static int package(int fd, struct fs_info *fs, struct entry *archive) {
    struct entry hit;
    if (parse_fs(fd, fs) ||
        find_root(fd, fs, "disable_hook", &hit) ||
        find_root(fd, fs, "disable.txt", &hit) ||
        find_root(fd, fs, "MLV2.OFF", &hit) ||
        find_root(fd, fs, "MLV2.TAR", archive) != 1 ||
        archive->directory || !archive->bytes || archive->bytes > MAX_ARCHIVE)
        return -1;
    return 0;
}

int main(int argc, char **argv) {
    int presence_only = argc == 2 && !strcmp(argv[1], "--present");
    if (argc != 1 && !presence_only) return 2;
    int fd = open("/dev/mmca1", O_RDONLY | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) return 1;
    struct stat info;
    struct fs_info fs;
    struct entry archive;
    int result = 1;
    if (!fstat(fd, &info) && S_ISBLK(info.st_mode) &&
        major(info.st_rdev) == 250 && minor(info.st_rdev) == 11 && !package(fd, &fs, &archive))
        result = presence_only ? 0 : (extract(fd, &fs, archive) ? 2 : 0);
    close(fd);
    return result;
}
