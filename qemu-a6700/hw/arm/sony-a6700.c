/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Sony ILCE-6700 (BIONZ XR, CXD90057) first-boot machine - research scaffold.
 *
 * Every address below comes from the camera's own device tree (cxd90057.dtb read from
 * /setting, evidence E9201): DRAM at 0x4_0000_0000, GIC-400 (GICv2) at 0xF13A1000,
 * five PL011 UARTs from 0xF1000000, generic timer at 2 MHz. The SoC register blocks
 * (SCU, clocks, resets, DMA, USB, PCIe, ...) are NOT modelled: the whole 0xF0000000 and
 * 0xFE000000 windows are unimplemented devices that read as zero and log each access
 * (-d unimp), so the first register contract the kernel needs can be discovered.
 * The kernel image starts at EL3 (it programs VBAR_EL3/SCR_EL3 itself), hence
 * secure_boot. This file is not a verified model of the SoC.
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/units.h"
#include "qemu/error-report.h"
#include "hw/boards.h"
#include "hw/loader.h"
#include "system/device_tree.h"
#include <libfdt.h>
#include "hw/char/pl011.h"
#include "hw/intc/arm_gic.h"
#include "hw/intc/arm_gic_common.h"
#include "hw/misc/unimp.h"
#include "hw/qdev-properties.h"
#include "hw/sysbus.h"
#include "exec/address-spaces.h"
#include "system/system.h"
#include "target/arm/cpu.h"
#include "target/arm/gtimer.h"

#define A6700_RAM_BASE      0x400000000ULL
#define A6700_GIC_DIST      0xF13A1000ULL
#define A6700_GIC_CPU       0xF13A2000ULL
#define A6700_UART0         0xF1000000ULL
#define A6700_UART_STRIDE   0x1000ULL
#define A6700_NUM_UARTS     5
#define A6700_NUM_SPI       448
#define A6700_NUM_IRQS      (A6700_NUM_SPI + 32)   /* GIC num-irq: SPIs + 32 internal */
#define A6700_UART_SPI0     0xDA         /* DT: <0 0xda 4> for serial@f1000000 */

/* DT timer interrupts: <1 13>, <1 14>, <1 11>, <1 10> -> PPI numbers. */
#define PPI_TIMER_S_EL1     13
#define PPI_TIMER_NS_EL1    14
#define PPI_TIMER_VIRT      11
#define PPI_TIMER_NS_EL2    10

#define A6700_KERNEL_OFFSET 0x80000ULL      /* text_offset of the arm64 Image header */
#define A6700_INITRD_OFFSET 0x10000000ULL
#define A6700_DTB_OFFSET    0x2000ULL           /* the image's own EL3 stub hands the kernel RAM_BASE+0x2000 */

/*
 * The image enters at EL3, which arm_load_kernel() refuses for AArch64. A tiny reset
 * trampoline at address 0 (the CPU reset vector, RVBAR = 0) therefore sets x0 = DTB and
 * jumps to the Image, with the CPU left in its reset exception level (EL3).
 */
static void a6700_load_boot(MachineState *machine)
{
    hwaddr kernel = A6700_RAM_BASE + A6700_KERNEL_OFFSET;
    hwaddr initrd = A6700_RAM_BASE + A6700_INITRD_OFFSET;
    hwaddr dtb = A6700_RAM_BASE + A6700_DTB_OFFSET;
    int64_t size;
    uint32_t code[16] = { 0 };
    uint64_t *literals = (uint64_t *)&code[8];

    if (!machine->kernel_filename) {
        error_report("sony-a6700: -kernel is required");
        exit(1);
    }
    size = load_image_targphys(machine->kernel_filename, kernel,
                               machine->ram_size - A6700_KERNEL_OFFSET);
    if (size < 0) {
        error_report("sony-a6700: cannot load kernel %s", machine->kernel_filename);
        exit(1);
    }
    if (machine->dtb) {
        int fdt_size;
        void *fdt = load_device_tree(machine->dtb, &fdt_size);

        if (!fdt) {
            error_report("sony-a6700: cannot load device tree %s", machine->dtb);
            exit(1);
        }
        qemu_fdt_setprop_sized_cells(fdt, "/memory@400000000", "reg", 2,
                                     A6700_RAM_BASE, 2, machine->ram_size);
        if (machine->kernel_cmdline) {
            qemu_fdt_setprop_string(fdt, "/chosen", "bootargs", machine->kernel_cmdline);
        }
        if (machine->initrd_filename) {
            int64_t isize = load_image_targphys(machine->initrd_filename, initrd,
                                                machine->ram_size - A6700_INITRD_OFFSET);

            if (isize < 0) {
                error_report("sony-a6700: cannot load initrd");
                exit(1);
            }
            qemu_fdt_setprop_u64(fdt, "/chosen", "linux,initrd-start", initrd);
            qemu_fdt_setprop_u64(fdt, "/chosen", "linux,initrd-end", initrd + isize);
        }
        fdt_size = fdt_totalsize(fdt);   /* the property edits above may have grown it */
        rom_add_blob_fixed_as("a6700.dtb", fdt, fdt_size, dtb, &address_space_memory);
    }
    {
        MemoryRegion *bootrom = g_new(MemoryRegion, 1);

        memory_region_init_rom(bootrom, NULL, "a6700.bootrom", 0x1000, &error_fatal);
        memory_region_add_subregion(get_system_memory(), 0, bootrom);
    }
    code[0] = 0x58000100;   /* ldr x0, [pc, #0x20]  -> DTB address   */
    code[1] = 0x58000121;   /* ldr x1, [pc, #0x24]  -> kernel entry  */
    code[2] = 0xaa1f03e2;   /* mov x2, xzr                           */
    code[3] = 0xaa1f03e3;   /* mov x3, xzr                           */
    code[4] = 0xd61f0020;   /* br  x1                                */
    literals[0] = dtb;
    literals[1] = kernel;
    rom_add_blob_fixed_as("a6700.reset", code, sizeof(code), 0, &address_space_memory);
}


/* arm,pl330 DMA controllers from the device tree: mdma@F1056000 (disabled in the DT, SPI 0x50..)
 * and mdma@F1057000 (enabled, SPI 0x71..0x78). The first interrupt is wired to the abort line and
 * every event line; the real event-to-SPI split of the SoC is not known yet. */
static void a6700_create_pl330(MemoryRegion *sysmem, DeviceState *gic, hwaddr base, unsigned spi0)
{
    DeviceState *dev = qdev_new("pl330");
    SysBusDevice *sbd = SYS_BUS_DEVICE(dev);
    unsigned i;

    qdev_prop_set_uint32(dev, "num_chnls", 8);
    qdev_prop_set_uint8(dev, "num_periph_req", 32);
    qdev_prop_set_uint8(dev, "num_events", 8);
    qdev_prop_set_uint8(dev, "data_width", 64);
    object_property_set_link(OBJECT(dev), "memory", OBJECT(sysmem), &error_abort);
    sysbus_realize_and_unref(sbd, &error_fatal);
    sysbus_mmio_map(sbd, 0, base);
    sysbus_connect_irq(sbd, 0, qdev_get_gpio_in(gic, spi0));
    for (i = 0; i < 8; i++) {
        sysbus_connect_irq(sbd, i + 1, qdev_get_gpio_in(gic, spi0 + i));
    }
}


/*
 * Small stateful register blocks, from Sony's GPL kernel source for this body
 * (drivers/udif/mach-cxd900xx/include/mach/platform.h, regs-scu.h, regs-ddrc.h and the
 * dwc3/pcie PHY drivers):
 *  - SCU at 0xF1388000: 16-byte groups of {DATA +0x0, SET +0x4, CLR +0x8}; CLKEN(ch)=+ch*16,
 *    PLL(ch)=+0x100+ch*4, CLKSEL(ch)=+0x200+ch*16, RESET(ch)=+0x300+ch*16.
 *  - DDRC(0..3) at 0xF105A000 + 0x1000*n: INTSTAT is DDR_CTL(275) = +0x44C; the mode-register
 *    write (bit 26) and low-power-command (bit 5) completions are reported as done.
 *  - USB PHY CFGR0 at 0xF1080020 (assumed base, see dwc3 cxd-phy.c): bit 18 SRAM_INIT_DONE.
 * Everything else in the SoC windows stays an unimplemented device.
 */
typedef struct A6700Regs {
    MemoryRegion mr;
    hwaddr base;
    bool setclr;
    uint32_t *r;
} A6700Regs;

static uint32_t a6700_regs_word(A6700Regs *s, hwaddr off)
{
    uint32_t v = s->r[off >> 2];
    hwaddr abs = s->base + off;

    if (abs >= 0xF105A000 && abs < 0xF105E000 && (abs & 0xFFF) == 0x44C) {
        v |= BIT(26) | BIT(5);
    }
    if (abs == 0xF1080020) {
        v |= BIT(18);
    }
    return v;
}

static uint64_t a6700_regs_read(void *opaque, hwaddr off, unsigned size)
{
    A6700Regs *s = opaque;
    uint32_t v = a6700_regs_word(s, off & ~3ULL);

    return (v >> ((off & 3) * 8)) & MAKE_64BIT_MASK(0, size * 8);
}

static void a6700_regs_write(void *opaque, hwaddr off, uint64_t val, unsigned size)
{
    A6700Regs *s = opaque;
    hwaddr word = off & ~3ULL;
    uint32_t v = (uint32_t)val;

    if (size != 4) {
        unsigned shift = (off & 3) * 8;
        uint32_t mask = MAKE_64BIT_MASK(0, size * 8) << shift;

        v = (s->r[word >> 2] & ~mask) | (((uint32_t)val << shift) & mask);
    }
    if (s->setclr) {
        switch (word & 0xC) {
        case 0x4:
            s->r[(word & ~0xFULL) >> 2] |= v;
            return;
        case 0x8:
            s->r[(word & ~0xFULL) >> 2] &= ~v;
            return;
        case 0x0:
            s->r[word >> 2] = v;
            return;
        default:
            return;
        }
    }
    s->r[word >> 2] = v;
}

static const MemoryRegionOps a6700_regs_ops = {
    .read = a6700_regs_read,
    .write = a6700_regs_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 8 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};

static void a6700_create_regs(MemoryRegion *sysmem, const char *name, hwaddr base,
                              hwaddr size, bool setclr)
{
    A6700Regs *s = g_new0(A6700Regs, 1);

    s->base = base;
    s->setclr = setclr;
    s->r = g_new0(uint32_t, size / 4);
    memory_region_init_io(&s->mr, NULL, &a6700_regs_ops, s, name, size);
    memory_region_add_subregion_overlap(sysmem, base, &s->mr, 1);
}

static void sony_a6700_init(MachineState *machine)
{
    MemoryRegion *sysmem = get_system_memory();
    unsigned ncpus = machine->smp.cpus;
    DeviceState *gic;
    SysBusDevice *gicbus;
    unsigned i;

    memory_region_add_subregion(sysmem, A6700_RAM_BASE, machine->ram);

    gic = qdev_new(gic_class_name());
    qdev_prop_set_uint32(gic, "revision", 2);
    qdev_prop_set_uint32(gic, "num-cpu", ncpus);
    qdev_prop_set_uint32(gic, "num-irq", A6700_NUM_IRQS);
    qdev_prop_set_bit(gic, "has-security-extensions", true);
    gicbus = SYS_BUS_DEVICE(gic);
    sysbus_realize_and_unref(gicbus, &error_fatal);
    sysbus_mmio_map(gicbus, 0, A6700_GIC_DIST);
    sysbus_mmio_map(gicbus, 1, A6700_GIC_CPU);

    for (i = 0; i < ncpus; i++) {
        Object *cpuobj = object_new(machine->cpu_type);
        DeviceState *cpudev = DEVICE(cpuobj);
        int ppibase = A6700_NUM_SPI + i * GIC_INTERNAL + GIC_NR_SGIS;

        object_property_set_int(cpuobj, "mp-affinity", i, &error_abort);
        object_property_set_link(cpuobj, "memory", OBJECT(sysmem), &error_abort);
        object_property_set_bool(cpuobj, "has_el3", true, &error_abort);
        object_property_set_bool(cpuobj, "has_el2", true, &error_abort);
        object_property_set_int(cpuobj, "cntfrq", 2000000, &error_abort);
        qdev_realize_and_unref(cpudev, NULL, &error_fatal);

        sysbus_connect_irq(gicbus, i, qdev_get_gpio_in(cpudev, ARM_CPU_IRQ));
        sysbus_connect_irq(gicbus, i + ncpus, qdev_get_gpio_in(cpudev, ARM_CPU_FIQ));
        qdev_connect_gpio_out(cpudev, GTIMER_PHYS,
                              qdev_get_gpio_in(gic, ppibase + PPI_TIMER_NS_EL1));
        qdev_connect_gpio_out(cpudev, GTIMER_VIRT,
                              qdev_get_gpio_in(gic, ppibase + PPI_TIMER_VIRT));
        qdev_connect_gpio_out(cpudev, GTIMER_HYP,
                              qdev_get_gpio_in(gic, ppibase + PPI_TIMER_NS_EL2));
        qdev_connect_gpio_out(cpudev, GTIMER_SEC,
                              qdev_get_gpio_in(gic, ppibase + PPI_TIMER_S_EL1));
    }

    /* Unmodelled SoC windows: low priority, read as zero, every access logged. */
    create_unimplemented_device("a6700-soc", 0xF0000000, 0x02000000);
    create_unimplemented_device("a6700-fe", 0xFE000000, 0x01000000);
    {
        /* eSRAM, 16 MiB at 0xFE000000 (CXD900XX_ESRAM_BASE/SIZE) */
        MemoryRegion *esram = g_new(MemoryRegion, 1);

        memory_region_init_ram(esram, NULL, "a6700.esram", 16 * MiB, &error_fatal);
        memory_region_add_subregion_overlap(sysmem, 0xFE000000, esram, 1);
    }
    a6700_create_regs(sysmem, "a6700-scu", 0xF1388000, 0x1000, true);
    a6700_create_regs(sysmem, "a6700-ddrc", 0xF105A000, 0x4000, false);
    a6700_create_regs(sysmem, "a6700-usbphy", 0xF1080000, 0x1000, false);

    for (i = 0; i < A6700_NUM_UARTS; i++) {
        pl011_create(A6700_UART0 + i * A6700_UART_STRIDE,
                     qdev_get_gpio_in(gic, A6700_UART_SPI0 + i),
                     serial_hd(i));
    }

    a6700_create_pl330(sysmem, gic, 0xF1056000ULL, 0x50);
    a6700_create_pl330(sysmem, gic, 0xF1057000ULL, 0x71);

    a6700_load_boot(machine);
}

static void sony_a6700_machine_class_init(ObjectClass *oc, void *data)
{
    MachineClass *mc = MACHINE_CLASS(oc);

    mc->desc = "Sony ILCE-6700 (CXD90057) first-boot research machine";
    mc->init = sony_a6700_init;
    mc->max_cpus = 4;
    mc->default_cpus = 1;
    mc->default_cpu_type = ARM_CPU_TYPE_NAME("cortex-a35");
    mc->default_ram_size = 512 * MiB;
    mc->default_ram_id = "a6700.ram";
    mc->no_cdrom = true;
    mc->no_floppy = true;
    mc->no_parallel = true;
}

static const TypeInfo sony_a6700_machine_type = {
    .name       = MACHINE_TYPE_NAME("sony-a6700"),
    .parent     = TYPE_MACHINE,
    .class_init = sony_a6700_machine_class_init,
};

static void sony_a6700_machine_register(void)
{
    type_register_static(&sony_a6700_machine_type);
}

type_init(sony_a6700_machine_register)
