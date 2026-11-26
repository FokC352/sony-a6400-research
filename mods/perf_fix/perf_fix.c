/* PerfFix: SMOD v2 RAM-only mod for Sony ILCE-6400 (2.00 captured layout).
 *
 * Optimizes stock UI responsiveness while preserving smooth animations:
 *   1. InputService::SetKeyRepeat (+0x003d01d4, ref VA 0x060e81d4):
 *      Eliminates initial key-repeat lag (repeat0 <= 60, repeat <= 25 when stop == 0).
 *   2. WidgetCmnAnimate::startAnimate / initAnimate (+0x003e7b10, +0x003e7e42) and
 *      GEN_FlexItemMove::requestAnimation (+0x0041a5c8):
 *      Preserves animations but shortens frame counts (> 4) to 4 frames (or frames/3)
 *      for crisp, zero-lag transitions.
 *   3. GEN_GridList::reqScrollTo / setScrollAreaPos (+0x003e8576, +0x003e8ffc, +0x003ea85a):
 *      Keeps scroll animations enabled while boosting fit interp rate at [self + 0x364]
 *      to 0.65f (0x3f266666) so shutter/aperture/Fn/menu scrolling is fast and smooth.
 *   4. WidgetSystem::sys_getInstance (+0x005f0d30, ref VA 0x06308d30):
 *      Sets [ws + 0x480] = 0 (60 fps frame step, linear interp) on perf_on() and
 *      restores the original value on perf_off().
 */
#include <stdint.h>

#define PF_MAX_REPEAT0    60
#define PF_MAX_REPEAT     25
#define PF_ANIM_CNT_OFF   0x170u
#define PF_ANIM_SANITY    0x1000u
#define PF_MAX_ANIM_FRAMES 4u

#define PF_GRID_INTERP_OFF 0x364u
#define PF_FLOAT_0_50      0x3f000000u
#define PF_FLOAT_0_65      0x3f266666u

#define PF_WS_FRAMESTEP_OFF 0x480u

struct perf_fix_state {
    uint32_t enabled;
    uint32_t key_repeat_calls;
    uint32_t key_repeat_clamped;
    uint32_t anim_tuned;
    uint32_t grid_tuned;
    uint32_t ws_patched;
    uint32_t ws_orig_step;
    uint32_t reserved;
};

_Static_assert(sizeof(struct perf_fix_state) == 32u, "state size must match --bss 32");

extern uint8_t __smod_bss[] __attribute__((visibility("hidden")));
#define PF_STATE ((volatile struct perf_fix_state *)__smod_bss)

extern void *pf_get_widget_system(void) __attribute__((visibility("hidden")));
extern uint32_t pf_orig_set_key_repeat(int key_code, int stop, int repeat0, int repeat)
    __attribute__((visibility("hidden")));
extern void pf_orig_start_animate(void *self, int flag)
    __attribute__((visibility("hidden")));
extern void pf_orig_init_animate(void *self, int enable, uint32_t target, uint32_t frames)
    __attribute__((visibility("hidden")));
extern void pf_orig_flex_req_anim(void *self, uint32_t pt, uint32_t a, uint32_t b,
                                  uint32_t c, uint32_t d)
    __attribute__((visibility("hidden")));
extern void pf_orig_grid_scroll_dir(void *self, uint32_t dir, uint32_t mode, uint32_t step,
                                    uint32_t anim, uint32_t fval)
    __attribute__((visibility("hidden")));
extern void pf_orig_grid_set_area_pos(void *self, int pos, uint32_t foff, uint32_t anim)
    __attribute__((visibility("hidden")));
extern void pf_orig_grid_scroll_iib(void *self, int x, int y, uint32_t anim)
    __attribute__((visibility("hidden")));

static uint32_t shorten_frames(uint32_t cur) {
    uint32_t target = cur / 3u;
    if (target < 2u) {
        target = 2u;
    }
    if (target > PF_MAX_ANIM_FRAMES) {
        target = PF_MAX_ANIM_FRAMES;
    }
    return target;
}

static void tune_anim_frames(void *self, volatile uint32_t *cnt) {
    if (!self) {
        return;
    }
    volatile uint32_t *frames = (volatile uint32_t *)((uintptr_t)self + PF_ANIM_CNT_OFF);
    uint32_t cur = *frames;
    if (cur > PF_MAX_ANIM_FRAMES && cur < PF_ANIM_SANITY) {
        *frames = shorten_frames(cur);
        (*cnt)++;
    }
}

static void boost_grid_interp(void *self, volatile uint32_t *cnt) {
    if (!self) {
        return;
    }
    volatile uint32_t *rate = (volatile uint32_t *)((uintptr_t)self + PF_GRID_INTERP_OFF);
    uint32_t cur = *rate;
    if (cur > 0u && cur < PF_FLOAT_0_50) {
        *rate = PF_FLOAT_0_65;
        (*cnt)++;
    }
}

__attribute__((visibility("default")))
int perf_on(void) {
    volatile struct perf_fix_state *st = PF_STATE;
    st->enabled = 1u;
    void *ws = pf_get_widget_system();
    if (ws && !st->ws_patched) {
        volatile uint32_t *step = (volatile uint32_t *)((uintptr_t)ws + PF_WS_FRAMESTEP_OFF);
        st->ws_orig_step = *step;
        *step = 0u;
        st->ws_patched = 1u;
    }
    return 0;
}

__attribute__((visibility("default")))
int perf_off(void) {
    volatile struct perf_fix_state *st = PF_STATE;
    st->enabled = 0u;
    if (st->ws_patched) {
        void *ws = pf_get_widget_system();
        if (ws) {
            volatile uint32_t *step = (volatile uint32_t *)((uintptr_t)ws + PF_WS_FRAMESTEP_OFF);
            *step = st->ws_orig_step;
        }
        st->ws_patched = 0u;
    }
    return 0;
}

__attribute__((visibility("default")))
uint32_t pf_hook_set_key_repeat(int key_code, int stop, int repeat0, int repeat) {
    volatile struct perf_fix_state *st = PF_STATE;
    st->key_repeat_calls++;
    if (st->enabled && stop == 0) {
        int changed = 0;
        if (repeat0 > PF_MAX_REPEAT0) {
            repeat0 = PF_MAX_REPEAT0;
            changed = 1;
        }
        if (repeat > PF_MAX_REPEAT) {
            repeat = PF_MAX_REPEAT;
            changed = 1;
        }
        if (changed) {
            st->key_repeat_clamped++;
        }
    }
    return pf_orig_set_key_repeat(key_code, stop, repeat0, repeat);
}

__attribute__((visibility("default")))
void pf_hook_start_animate(void *self, int flag) {
    volatile struct perf_fix_state *st = PF_STATE;
    if (st->enabled) {
        tune_anim_frames(self, &st->anim_tuned);
    }
    pf_orig_start_animate(self, flag);
}

__attribute__((visibility("default")))
void pf_hook_init_animate(void *self, int enable, uint32_t target, uint32_t frames) {
    volatile struct perf_fix_state *st = PF_STATE;
    if (st->enabled && frames > PF_MAX_ANIM_FRAMES && frames < PF_ANIM_SANITY) {
        frames = shorten_frames(frames);
        st->anim_tuned++;
    }
    pf_orig_init_animate(self, enable, target, frames);
}

__attribute__((visibility("default")))
void pf_hook_flex_req_anim(void *self, uint32_t pt, uint32_t a, uint32_t b,
                           uint32_t c, uint32_t d) {
    volatile struct perf_fix_state *st = PF_STATE;
    if (st->enabled) {
        tune_anim_frames(self, &st->anim_tuned);
    }
    pf_orig_flex_req_anim(self, pt, a, b, c, d);
}

__attribute__((visibility("default")))
void pf_hook_grid_scroll_dir(void *self, uint32_t dir, uint32_t mode, uint32_t step,
                             uint32_t anim, uint32_t fval) {
    volatile struct perf_fix_state *st = PF_STATE;
    if (st->enabled) {
        boost_grid_interp(self, &st->grid_tuned);
    }
    pf_orig_grid_scroll_dir(self, dir, mode, step, anim, fval);
}

__attribute__((visibility("default")))
void pf_hook_grid_set_area_pos(void *self, int pos, uint32_t foff, uint32_t anim) {
    volatile struct perf_fix_state *st = PF_STATE;
    if (st->enabled) {
        boost_grid_interp(self, &st->grid_tuned);
    }
    pf_orig_grid_set_area_pos(self, pos, foff, anim);
}

__attribute__((visibility("default")))
void pf_hook_grid_scroll_iib(void *self, int x, int y, uint32_t anim) {
    volatile struct perf_fix_state *st = PF_STATE;
    if (st->enabled) {
        boost_grid_interp(self, &st->grid_tuned);
    }
    pf_orig_grid_scroll_iib(self, x, y, anim);
}
