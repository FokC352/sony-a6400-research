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

    for (i = 0; i < A6700_NUM_UARTS; i++) {
        pl011_create(A6700_UART0 + i * A6700_UART_STRIDE,
                     qdev_get_gpio_in(gic, A6700_UART_SPI0 + i),
                     serial_hd(i));
    }

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
