.syntax unified
.thumb
.section .text

.balign 4
.global pf_get_widget_system
.hidden pf_get_widget_system
.thumb_func
pf_get_widget_system:
    ldr r3, .Lws_get_inst
    bx r3
.align 2
.Lws_get_inst:
    .word 0x06308d31

.balign 4
.global pf_orig_set_key_repeat
.hidden pf_orig_set_key_repeat
.thumb_func
pf_orig_set_key_repeat:
    stmdb sp!, {r4, r5, r6, r7, r8, r9, sl, lr}
    sub sp, #8
    add r7, sp, #8
    mov r6, r0
    mov r8, r1
    ldr.w pc, [pc, #0]
    .word 0x060e81e1

.balign 4
.global pf_orig_start_animate
.hidden pf_orig_start_animate
.thumb_func
pf_orig_start_animate:
    stmdb sp!, {r4, r5, r6, r7, r8, lr}
    add.w r5, r0, #364
    add r7, sp, #0
    mov r4, r0
    ldr.w pc, [pc, #0]
    .word 0x060ffb1d

.balign 4
.global pf_orig_init_animate
.hidden pf_orig_init_animate
.thumb_func
pf_orig_init_animate:
    stmdb sp!, {r4, r5, r6, r7, r8, r9, sl, lr}
    add.w r8, r0, #364
    mov r4, r0
    add r7, sp, #0
    ldr.w pc, [pc, #0]
    .word 0x060ffe4f

.balign 4
.global pf_orig_flex_req_anim
.hidden pf_orig_flex_req_anim
.thumb_func
pf_orig_flex_req_anim:
    push {r4, r5, r6, r7, lr}
    sub sp, #12
    add r7, sp, #0
    str.w r3, [r0, #768]
    mov r4, r0
    ldr.w pc, [pc, #0]
    .word 0x061325d5

.balign 4
.global pf_orig_grid_scroll_dir
.hidden pf_orig_grid_scroll_dir
.thumb_func
pf_orig_grid_scroll_dir:
    stmdb sp!, {r4, r5, r6, r7, r8, r9, sl, lr}
    mov sl, r3
    ldr.w r3, [r0, #412]
    add r7, sp, #0
    ldr.w pc, [pc, #0]
    .word 0x06100583

.balign 4
.global pf_orig_grid_set_area_pos
.hidden pf_orig_grid_set_area_pos
.thumb_func
pf_orig_grid_set_area_pos:
    stmdb sp!, {r4, r5, r6, r7, r8, lr}
    mov r4, r0
    add r7, sp, #0
    mov r5, r1
    mov r8, r2
    ldr.w pc, [pc, #0]
    .word 0x06101009

.balign 4
.global pf_orig_grid_scroll_iib
.hidden pf_orig_grid_scroll_iib
.thumb_func
pf_orig_grid_scroll_iib:
    stmdb sp!, {r4, r5, r6, r7, r8, r9, sl, lr}
    sub sp, #8
    add r7, sp, #8
    mov r4, r0
    mov r5, r2
    ldr.w pc, [pc, #0]
    .word 0x06102867

.section .note.GNU-stack,"",%progbits
