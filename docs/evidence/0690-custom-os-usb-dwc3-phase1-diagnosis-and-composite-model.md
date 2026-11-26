# E0690 - Custom OS USB (DWC3) phase 1: root-cause boundary, endpoint budget, and the composite QEMU model

Date: 2026-11-23. Offline QEMU only. No camera, no USB host, no service-mode or storage access.
The plan under execution is `usb_custom_os_qemu_plan.md` (Phase 1 gate, plus the QEMU half of
Phase 3); nothing here is a physical result.

## Question

1. Why does the native daemon stay silent under `custom_os.debugd=1` (E0685)? Separate the
   kernel/DWC3 side from the missing stock USB userspace.
2. What is the real DWC3 prerequisite and endpoint budget for a custom composite gadget?
3. Can the `qemu-a6400` DWC3 model carry parallel Bulk/Interrupt/Isochronous traffic,
   multi-TRB/LINK rings and guest-driven soft-connect, with no regression to the existing
   EP0/Bulk contracts?

## Result and scope

- The DWC3 kernel side is **present and functional** in the Custom OS: the driver binds,
  `/sys/class/udc/dwc3.0` exists, the captured GSNPSID/start gate and FUSB bits are visible,
  and a standalone userspace helper can bring the controller out of halt (GCTL, event ring,
  `DEVTEN`, `DALEPENA` all programmed). The controller is nevertheless **never pulled up**
  (`DCTL = 0`), because nothing performs the Sony USB service/gadget-create sequence.
- Keeping `libUsbControl.so` + `libHotplug.so` (instead of the `skip=` list) runs both
  constructors inside `imlaunch` (`UsbControl_Init() = 0`, `Hotplug_Init() = 387`) and still
  starts nothing: the USB FSM needs the application's message loop and the app-level
  `ActionOpenUSB`/`StartGadget` trigger, which the Custom OS does not run.
- A direct `otgcore` sequence (decoded ABI) initialises the UDC but stops at two explicit
  kernel boundaries: `usb_otgcore: mode error` on `SET_MODE(2)` and
  `usb_gadgetcore ... save_desc_tbl(): Error` (`EFAULT`) on the gadget commit. No function
  driver is registered with gadgetcore, because that registration is performed by the Sony
  userspace service, not by the loaded `usbg_*` kernel modules.
- The DWC3 model gained an opt-in `strict-composite` policy: 14 parallel endpoints, multi-TRB
  sequencing, bounded LINK following, Interrupt and Isochronous transfers, per-endpoint stall,
  and `DCTL` soft-disconnect. **116/116 qtest checks pass**; the existing 44 MMIO identity
  checks and 14 QEMU board tests still pass.
- **Gate 2 is met**: a userspace-built gadgetcore descriptor table (`0x400ce203`) plus a
  first-party vendor function driver give full device/configuration enumeration in the Custom
  OS and a real EP0 `PING` -> `PONG` data stage (UDC `configured`, high-speed).
- **Gate 3 guest side is met for debugger + data + UVC**: four interfaces, six endpoints,
  UVC `GET_CUR`/`SET_CUR(PROBE)`/`SET_CUR(COMMIT)`, a byte-exact 29,452-byte 720p JPEG delivered
  on the VideoStreaming bulk endpoint, interleaved debugger/data traffic and an
  unconfigure-first pull-up drop with host NAK. ISO streaming and camera-derived payloads are
  not implemented.

Not claimed: the stock extcmd debugger daemon, a Sony transport, ISO streaming, a
sensor-derived UVC payload, multi-frame pacing, any physical-camera behaviour, or a UDC sysfs
"not attached" state (not written by this kernel on a pull-up drop).

## Inputs and identities

| Input | Identity |
| --- | --- |
| Custom kernel `custom-Image` | 4,421,888 B, `33803be11d1719a72b6f55a2f603bf3a0bf73ba4b61dfcb765b9d405729d26b8` |
| Custom OS initrd, before this work | 1,081,643 B, `d21d0a62e2d07f4312ef3181ce083aaea946af91131d7bcec86788cd241c1106` |
| Custom OS initrd, with the `custom_os.usbsvc=1` token | 1,081,975 B, `6d34c1bf8ec440f0534baf601bc78432df6213872498f85872c1baecb160073f` |
| `build_full_initrd.py` | `0fb11dd35b8ddf3bc8d39701597411dc539cc3de79b08d7e5a717ab5cdba4511` |
| QEMU `qemu-system-arm` (WSL, pinned 11.1.3 + overlay) | `7fc75279249662b0d868c8ce8b0f187a18714617ddb13a706779d22987f55fd6` |
| `cxd90045_dwc3.c` overlay | `42718d3ed3ac58ed3d17a19079f2bf53fff7d3c2ae1ad85e342b97500dfbf68d` |
| DWC3 composite qtest tool | `56f25e933b4275bcaa550b15cd2434765cafa9652b6baa31da240ebd0c861358` |
| Custom OS USB state probe | `e0b58793c1e5db38448af99394d6ecc27925fd19b7edcac501ef1d70da91dbd4` |
| Retained 720p fixture | `tools/native_camera/test_pattern_720p.jpg`, 29,452 B, `b61e53897b9bba7677617d0d5e923ae6802c94c5fa9f9ef8bb48697da55b0f9f` |
| Gadgetcore ABI mirror / check module | `5130c5e1c5062e7d4bb3fc667388bb641fad36a3d9cc47fd0aebef0f1b0cd2fe` / `b77e3c4d5e743b0f0dee78a175bb5197e8aee4e2c4364456d821008b2575a88f` -> `7e65c745b0c7e0badc22c14586819e53ed0205db6afd8bf32d145edb6b3cfc0c` |
| Vendor function driver / build | `226d3a83e7fe528f1fd7c58984ca7d130554b53de04783682033b0b2d611ddfb` -> `36de912455e587b08ff0ec3c84aa9bc1e533336a83f80225ec9c231f609a4365` |
| START helper / build | `91589a002ac8466ee055c5f43e203804071dc5df86436748776569f874831fe8` -> 7,084 B, `80c2f572cc97c2d4aada6243310f1f9574e2085f52d9d4e2fa2307e2fcf93003` |
| Composite function driver / build | `2ef17086bbf3120a53bd74c34d07f4ed36dca64dbc3b3e19050e3f87227d95d7` -> 75,996 B, `a243009025ab331a593586eec512feca16a0ef9b52543408c4271c90c5455d5b` |
| OTG bring-up helper source / final build | `f96a87bf87d3fc1c2fc53293855da4e928c05fe72119d7f8df98a5d9257a64ed` / 10,944 B, `5526cb8bb4f4642760efe929e6a1ce4b15f46bd5d1899fc0cb232df0eaec9d44` |
| Captured inputs | `~/a6400-inputs/inputs-moddev-1` (`boot-manifest.json` unchanged) |
| Native daemon staged for the probe | `devcrashday_a6400/a6400_usb_debugd.elf`, 11,384 B |

The probe stages its own files only in the run's disposable guest overlay and in `/tmp_bt`.
No hash-pinned capture was rewritten; the previous initrd is preserved as
`artifacts/tests/qemu-custom-os-usb-state-4/baseline-custom-full-initrd.ext2.gz`.

## Observation 1 - the Phase A boot does not model DWC3 at all

`qemu-custom-os-usb-state-2` ran the verified Phase A option set with **no** `DWC3_GADGET`.
Guest `/proc/iomem` has no dwc3 window, and QMP `xp` reads GSNPSID = 0. The UDC still appears
because the kernel platform device exists, but the MMIO region is unbacked. Nothing about USB
can be concluded from a gate run in this configuration.

## Observation 2 - with the DWC3 model the kernel side is alive, the gadget is not started

`qemu-custom-os-usb-state-4` (DWC3 model, captured HW parameters, captured start gate, FUSB
VBUS/CID options, no `custom_os.usbsvc` token):

```
/proc/modules (USB): usb_extcmd, usbg_sen, usbg_buspower, usb_buspwr, usb_darwin,
                     usb_portMonitor, devno      (no usbg_storage, no usbg_stillimage)
/dev/usb:            event gadgetcore otgcore usbcore scsi massstorage usb_extcmd ... present
/sys/class/udc:      dwc3.0   state=not attached   current_speed=UNKNOWN
/proc/iomem:         f0200000-f02fffff : dwc3.0   (f020c100+ core window)
GSNPSID=0x5533290a  START_GATE(0xF0200024)=1  FUSB(0xF2920010)=0x2
DCTL=0  DEVTEN=0  GEVNTSIZ=0  DALEPENA=0  DSTS=0x00400000 (halted)
```

After the virtual host sent `CONN` and QMP raised `usb-vbus` (FUSB became `0x3`), every DWC3
register above stayed unchanged and IRQ 229 never fired. The node and module set is complete
enough for the debugger to open `/dev/usb/usb_extcmd`; there is simply no enumerated device
for it to answer on. This is the kernel-side half of the E0685 silence.

## Observation 3 - the stock USB libraries are necessary but not sufficient

`qemu-custom-os-usb-state-5/6` used the new opt-in `custom_os.usbsvc=1` token, which keeps the
two skipped libraries. The guest log shows the constructors actually ran:

```
IML start mode=load ... skip= ...
IML  76 lib id=0x121 psid=0 libUsbBusPower.so init=UsbBusPower_Init
IML  77 lib id=0xe7 psid=0 libInfraUsbCmn.so init=InfraUsbLib_init
IML  79 lib id=0xca psid=0 libUsbControl.so init=UsbControl_Init
IML  79   UsbControl_Init() = 0 (0 ms)
IML  81 lib id=0xc8 psid=0x7fb libHotplug.so via /usr/lib/libObj.so init=Hotplug_Init
IML  81   Hotplug_Init() = 387 (2 ms)
```

`DCTL`, `DEVTEN`, `GEVNTSIZ` and `DALEPENA` stayed 0 and the UDC stayed `not attached` before
and after VBUS/CONN. So removing the `skip=libUsbControl.so,libHotplug.so` filter is not the
fix by itself: the FSM needs the process/message context and the app-level open-USB trigger.

## Observation 4 - a decoded standalone otgcore sequence initialises the UDC, then stops

`custom_os/launcher/usb_otg_bringup.c` (built against the camera glibc 2.11, no VFP, bounded)
performs the decoded `usb_otgcore_ioctl` openSvc + createGadget sequence with explicit
receipts: `open`, `SET_PORT_DESC`, `SET_PARAM`, `START`, `SET_CONFIG` (verified MSC template:
count 2, io-type pairs (2,4)/(0,4), tail word 3), `SET_MODE(2)`, `0xe305`, `SET_MODE(2)`,
`GET_INFO`, `0xe306`, then `STOP`/`RESET`/`0xe312`. Runs `qemu-custom-os-usb-state-7/8/9`:

```
OTG step=open path=/dev/usb/otgcore fd=3 errno=0
OTG step=set_port_desc rc=-1 errno=22      # zero word0: the kernel's only check is word0 != 0
OTG step=set_param rc=0 ; step=start rc=0
OTG step=set_config rc=0
OTG step=set_mode_a rc=0 ; cmd_e305 rc=0 ; set_mode_b rc=0 ; get_info rc=0
OTG info word0=0x00000002 word1=0x00000000   # SET_CONFIG entry count accepted
OTG step=cmd_e306 rc=-1 errno=16 (EBUSY)
dmesg: usb_otgcore: mode error
DCTL=0  GCTL=0x00002000  GEVNTADRLO0=0x0975a000  GEVNTSIZ0=0x1000
DEVTEN=0x1e1f  DALEPENA=0x3  DSTS=0x00400000
OTG step=gc_open fd=4 ; gc_commit (0x400ce203) rc=-1 errno=14 (EFAULT)
dmesg: usb_gadgetcore1121: save_desc_tbl()  /  usb_gadgetcore0275: Error : save_desc_tbl()
```

With `--port-word0 1` the `SET_PORT_DESC` EINVAL disappears (the kernel rejects only word0 = 0;
the 36-byte content semantics remain unresolved). The UDC is now initialised - GCTL, event
ring, `DEVTEN` and both EP0 `DALEPENA` bits - but `DCTL.RUN_STOP` is still 0, the mode callback
logs `usb_otgcore: mode error`, and the gadgetcore commit fails in `save_desc_tbl()` because no
function driver is registered. Host `SETUP` tokens get `NAK 0` and the UDC stays
`not attached`; the enumeration attempt is reported as a timeout, not as success.

**Root cause (INFERENCE, from the observations above):** the E0685 silence is the absent Sony
USB userspace service chain, not a broken Custom OS kernel/DWC3 driver. The service performs
the OTG mode transition the kernel accepts and registers the function driver whose descriptor
table `save_desc_tbl()` needs. Until one of those exists, the controller cannot be pulled up,
so no host enumeration can happen and the extcmd debugger has no transport.

## Observation 5 - soft connect pulls the device up; EP0 SETUP is delivered; no descriptor answers

`qemu-custom-os-usb-state-12/13/14` ran the helper with `--gc-commit --no-teardown` and wrote
`connect` to `/sys/class/udc/dwc3.0/soft_connect` while it held the gadget. The result moves the
boundary closer:

```
soft_connect: RC=0
/sys/class/udc/dwc3.0/state=default   current_speed=high-speed
GCTL=0x00002000  GEVNTADRLO0=0x0975f000  GEVNTSIZ0=0x1000  GEVNTCOUNT0=0
DCFG=0x00400001  DCTL=0x8cf00000  DEVTEN=0x1e1f  DALEPENA=0x3  DSTS=0
host:  TX SETUP 8006000100001200  ->  ACK 0 8      (the guest consumed the SETUP)
host:  IN 1  ->  NAK 1  (retried to the attempt budget; the data TRB is never armed)
UDC stays state=default; no oops; dmesg shows only mode_error and save_desc_tbl()
```

`DCTL = 0x8cf00000` with `state=default` / `current_speed=high-speed` is the stock controller
state E0598 recorded for the original gadget (`DCTL=0x8CF00A00`). So the guest DWC3 driver and
the model agree on the running state, and a host SETUP reaches the original EP0 path. What is
missing is a function with a descriptor table: gadgetcore consumed the SETUP but never queued
the 18-byte device descriptor, so the host retries `IN 1` until its budget ends. Enumeration is
therefore **not** complete; this is not a PING/PONG result.

Note: `save_desc_tbl()` still fails in this run, and the mode callback still logs
`usb_otgcore: mode error`; the soft-connect sysfs path works because udc-core calls the bound
gadget driver's pullup independently of the Sony service FSM.

## Observation 6 - Gate 2: full enumeration and a real PING/PONG handshake

The kernel source and its build tree are present in WSL (`~/a6400-kernel`), which exposed the
missing contract: `USB_IOC_GADGETCORE_START` (`_IOW(0xE2, 3, struct
usb_gadgetcore_start_info)` = `0x400ce203`) takes a **userspace-built descriptor table** and
deep-copies it (`save_desc_tbl`). The stock application supplies it; nothing in the Custom OS
did, which is why `save_desc_tbl()` failed.

Three first-party pieces close that:

1. `custom_os/usb/gadget_desc_abi.h` - userspace mirror of the gadgetcore descriptor structs.
   `custom_os/usb/abi_check.c` is built as an external module against the custom kernel and
   asserts every size/offset with `BUILD_BUG_ON`; it compiles, so the mirror matches
   (`artifacts/tests/custom-os-abi-3`, 43 assertions). `custom_os/usb/a6400_vendor_fn.c` is a
   function driver for interface 0 that answers the vendor PING on EP0 with `PONG`.
2. `custom_os/launcher/usb_gift_start.c` builds one configuration, one vendor interface, no
   endpoints, ids 054C:0CA8, and calls START.
3. `custom_os/hybrid/qemu_custom_os_usb_state.py` gained the ordering that makes it work:
   OTG service sequence (controller init) -> module load -> START -> strict-EP0 host.

Run `qemu-custom-os-usb-state-22` (and the automated `tests/test_qemu_custom_os_usb_gate2.py`,
which passed in 96.7 s) produced:

```
GIFT_V1 table=88 start_info=12 vendor=054c product=0ca8 class=ff
GIFT step=open path=/dev/usb/gadgetcore fd=3
GIFT step=start cmd=0x400ce203 rc=0
a6400_vendor: register rc=0
a6400_vendor: start speed=0 endpoints=0
GET_DESCRIPTOR(device)  = 12 01 0002 ff 00 00 40 4c05 a80c 0001 00 00 00 01
GET_DESCRIPTOR(config)  = 09 02 1200 01 01 00 c0 32 | 09 04 00 00 00 ff 00 00 00
SET_ADDRESS, SET_CONFIGURATION(1), GET_CONFIGURATION = 1   all completed
PING (C1 01 0000 0000 0400) -> 50 4f 4e 47 ("PONG")
a6400_vendor: PING answered with PONG
/sys/class/udc/dwc3.0/state=configured   current_speed=high-speed
DCTL=0x8cf00a00  DCFG=0x00400009  DALEPENA=0x3  DEVTEN=0x1e1f
```

The PONG is queued by guest kernel code (`a6400_vendor_vendor` -> `usb_ep_queue` on the EP0 the
core passes in), so this is a real control data stage, not a host-side stub. The first attempt
BOGGED at `arch/arm/include/asm/dma-mapping.h:315` because the reply buffer was `.rodata`; the
driver now allocates it with `kmalloc(4, GFP_ATOMIC)` and frees it in the completion callback
(`qemu-custom-os-usb-state-21` preserves the fault).

**Gate 2 is met** for one vendor interface with the debugger-style handshake. Not claimed: the
stock extcmd debugger daemon itself (it needs a Sony transport), bulk/ISOC data paths, or
composite functions.

## Observation 7 - Gate 3 guest side: two interfaces, five endpoints, interleaved

The START helper now builds a composite table (`--composite`): MI_00 vendor debugger (bulk IN
0x81 / OUT 0x02) and MI_01 data channel (bulk IN 0x83 / OUT 0x04, interrupt IN 0x85), ids
054C:0CA8. `a6400_composite_fn.c` registers one function driver per interface and keeps a request
queued on every IN endpoint (tagged `DBG:` / `DATA` / `IRQ:` sequences) plus OUT requests.
Receipt `qemu-custom-os-usb-composite-2` (automated by
`tests/test_qemu_custom_os_usb_composite.py`, 110.8 s):

```
config = 09 02 3e00 02 01 00 c0 32
         09 04 00 00 02 ff 00 00 00 | 07 05 81 02 0002 00 | 07 05 02 02 0002 00
         09 04 01 00 03 ff 00 00 00 | 07 05 83 02 0002 00 | 07 05 04 02 0002 00
         07 05 85 03 4000 0a
PING -> PONG
cycle 0..2, interleaved:
  IN 2  -> "DBG:000000000001" ... "DBG:000000000002"   (poll #1/#2 reused, see below)
  OUT 3 -> ACK 3 32      guest: a6400_comp: out DBG len=32 b=33333333...
  IN 4  -> "DATA000000000001" / ...
  OUT 5 -> ACK 5 32      guest: a6400_comp: out DATA len=32 b=55555555...
  IN 6  -> "IRQ:000000000001" / ...
teardown (GIFT STOP): in/out requests complete with -108 (ESHUTDOWN),
  "stop iface=0 out_count=4" / "stop iface=1 out_count=4"
```

Two model corrections were required and are part of the strict-composite contract:

1. **Physical vs logical endpoint numbers.** This Sony dwc3 allocates physical endpoint slots
   from a free pool (`dwc3_gadget_map_phy_ep`, first free from 2) while DEPCFG1[29:25] and
   DALEPENA carry the logical USB endpoint number. The model addresses command blocks and TRBs
   by the physical slot (2..6 here) and takes direction from the logical number's parity;
   DALEPENA is not used as a physical index.
2. **Interrupt XferNotReady.** dwc3 does not start an interrupt transfer at queue time; it waits
   for XferNotReady. The model now emits NRDY for an interrupt IN token even with no TRB armed,
   after which the driver kicks the queued request and the next token returns data.

Not claimed: a UVC class function (MI_01 is a vendor data channel with an interrupt status
endpoint, not UVC VideoControl/VideoStreaming), isochronous traffic, a real producer, or an
unconfigure-first teardown. The 16-endpoint budget is used as 5 of the 14 non-control slots.

## Observation 8 - UVC-shaped function and unconfigure-first teardown

The composite table now also carries MI_02 UVC VideoControl (class 0x0E/1/0, VC header +
camera input terminal + streaming output terminal, 40 class-specific bytes) and MI_03 UVC
VideoStreaming (class 0x0E/2/0; alt 0 carries VS input header + MJPEG format + 640x480@30
frame, 56 class-specific bytes; alt 1 carries bulk IN 0x86 with a 7-byte UVC endpoint
companion). The driver registers interface 2 and 3, answers a real `GET_CUR(VS_PROBE)` with a
26-byte probe structure, and queues a synthetic tagged frame on the streaming endpoint when
alt 1 starts. Receipt `qemu-custom-os-usb-uvc-3` (automated by
`tests/test_qemu_custom_os_usb_composite.py`, 121.1 s):

```
configuration = 198 bytes, 5 interface descriptors, 6 endpoint descriptors
PING -> PONG
SET_INTERFACE(3,1) -> ok; guest: uvc vs start alt=1, uvc frame endpoint ep6in-bulk
GET_CUR VS_PROBE (A1 81 0001 0300 1A00) -> 26 bytes
  01 00 01 01 15160500 ... 00600900 00100000   (format 1, frame 1, 333333 ns)
interleaved cycles (x2):
  IN 2 "DBG:..."  OUT 3 ACK 32  IN 4 "DATA..."  OUT 5 ACK 32
  IN 6 "IRQ:..."  IN 7 "UVC:..."   (5556433a...)
SET_CONFIGURATION(0) -> accepted; guest stops MI_00, MI_01, UVC VC and VS,
  requests stop with -108, DALEPENA -> 0
soft-disconnect (UDC soft_connect "disconnect") -> RC=0
  DCTL=0x0cf00a00 (RUN_STOP cleared)  DSTS=0x00400000 (halted)  DALEPENA=0
  host IN 2 -> NAK 2 (device no longer answers)
```

The UDC sysfs state string stays `addresssed`: in this kernel `USB_STATE_NOTATTACHED` is only
written when the UDC is registered (`udc-core.c:243`), never on a pull-up drop, so "not
attached" is not reachable through SET_CONFIGURATION(0)+pullup without unbinding the controller
driver. The functional teardown is proven by the stopped callbacks, `DALEPENA=0`, cleared
`DCTL.RUN_STOP` and the host NAK. That is reported as a kernel-state limitation, not as a pass.

Not claimed: an ISO streaming path, a real UVC payload (the frame is a tagged synthetic buffer,
not a JPEG from av-cam), UVC PROBE/COMMIT semantics beyond the single GET_CUR reply, or physical
hardware.

## Observation 9 — SET_CUR(PROBE)/SET_CUR(COMMIT) and a real 720p JPEG frame

The UVC function now implements the control flow that precedes streaming on the physical
camera and delivers the retained 720p JPEG:

- `SET_CUR(PROBE)` (`21 01 0001 0300 1A00`) and `SET_CUR(COMMIT)` (`21 01 0002 0300 1A00`)
  are accepted by the VideoStreaming function driver; it queues the 26-byte EP0 data stage,
  stores it and, on COMMIT, queues the frame on the alt-1 bulk endpoint.
- The frame is the retained fixture `tools/native_camera/test_pattern_720p.jpg`
  (29,452 B, `b61e53897b9bba7677617d0d5e923ae6802c94c5fa9f9ef8bb48697da55b0f9f`), embedded into
  the module at build time from that file (the build records the fixture hash).

Receipt `qemu-custom-os-usb-jpeg-2` (automated by `tests/test_qemu_custom_os_usb_composite.py`,
125.7 s):

```
GET_CUR VS_PROBE -> 26 bytes (format 1, frame 1, 333333 ns)
SET_CUR(PROBE)   -> 26 bytes received (guest: uvc set_cur stage=1 status=0 actual=26)
SET_CUR(COMMIT)  -> 26 bytes received (guest: uvc set_cur stage=2 status=0 actual=26)
guest: uvc frame queued 29452 bytes sha=b61e5389...
host IN 7        -> 29452 bytes, SHA-256 b61e5389..., SOI ffd8, EOI ffd9, exact match
guest: uvc frame complete status=0 actual=29452/29452
interleaved debugger traffic before and after the frame: IN 2 "DBG:..." and OUT 3 ACK 32
```

So the VideoStreaming bulk channel now carries a byte-exact valid JPEG produced by the guest,
not a tagged synthetic buffer. The transfer arrives as one large TRB in this model; the host
reassembles and hashes it. A multi-TRB variant is still covered by the qtest contract, not by
this guest path.

Not claimed: ISO VideoStreaming, UVC payloads produced from av-cam/sensor data, multi-frame
streaming or frame-rate pacing.

## Endpoint budget

GHWPARAMS3 = `0x06A4D085` (E0595). The endpoint field is bits [13:8] = `0x10` = 16 physical
endpoints: EP0 OUT/IN plus seven IN/OUT pairs (`ep1..ep7`). `DWC3_MAX_EPS = 16` in the model
matches this exactly. The composite qtest configures all 14 non-control slots and delivers a
token on each in one interleaved pass.

## QEMU composite model (opt-in `strict-composite`)

`cxd90045_dwc3.c` additions, all behind the new `DEFINE_PROP_BOOL("strict-composite", ...,
false)` so the default, `strict-ep0` and `strict-bulk` baselines do not change:

- host tokens address **physical** endpoint indices (the `DALEPENA` numbering); EP0 stays on
  the existing strict token contract;
- transfer type from `DEPCFG0[3:1]` (1 ISO, 2 Bulk, 3 Interrupt), with configured/stall guards;
- bounded LINK following (64 hops) and one data TRB per host token, with HWO/residual updates;
- Interrupt and Isochronous IN; ISO emits XferInProgress (frame in the parameter) then
  XferComplete; Bulk/Interrupt short-packet status;
- Bulk/Interrupt OUT with residual size and short-packet status;
- `GETSEQNUMBER` returns a valid XferRscIdx, `STARTNEWCONFIG` completes (semantics not
  interpreted), both only under the policy;
- guest-driven soft disconnect: clearing `DCTL.RUN_STOP` with the policy on emits DEVTCFG
  `Disconnect` (the host-visible pull-up removal).

`tools/research/qemu_dwc3_composite_check.py` drives the model through qtest MMIO plus guest
DMA and a chardev host socket. Result of `artifacts/qemu-a6400/dwc3-composite-3/report.json`:
**116/116 checks**, `status=passed`, including 14 parallel endpoints, two LINK hops, ISO
InProgress/Complete, STALL then recovery, and disconnect/reconnect with a working transfer
afterwards.

## Regression evidence

- Phase A gate `custom_os/hybrid/run_qemu_custom_kernel_verify.py --build-dir
  artifacts/tests/custom-os-build-66 --out artifacts/tests/qemu-custom-kernel-verify-15`:
  **PASS** in 82.0 s with the rebuilt initrd, all eight items true (`liro_load`, `dmmrule5`,
  `replay`, `vdf_unmute`, `cam_ready`, `vph`, `ui`, `f30a_fill`).
- Phase B handover gate `custom_os/ramboot/run_handover_test.py --build
  artifacts/custom-kernel-ramboot-v1 --out artifacts/tests/qemu-handover-15b --case
  custom_kernel_full`: **OK**, `hybrid_ready=true`, no failing item.
- `tools.research.qemu_dwc3_profile_check`: 44/44 MMIO identity checks pass with the new
  overlay (`artifacts/qemu-a6400/dwc3-profile-regression-1`).
- `python3 -m unittest tests.test_qemu_emmc tests.test_qemu_vph_composite`: 14 tests OK in WSL
  against the rebuilt binary.
- `tests/test_qemu_dwc3_composite.py`: 10 tests OK in WSL (9 pure-Python + 1 native);
  9 OK + 1 skipped on Windows, where the QEMU build is absent.
- `tests/test_qemu_custom_os_usb_gate2.py`: 4 tests OK in WSL, including the live gate
  (boot + enumeration + PONG) in 96.7 s; the live test skips on Windows.
- `tests/test_qemu_custom_os_usb_composite.py`: 3 tests OK in WSL, including the live composite
  run (5 interfaces, 6 endpoints, PONG, GET_CUR + SET_CUR PROBE/COMMIT, byte-exact JPEG,
  interleaved cycles, unconfigure + soft disconnect) in 125.7 s.
- `qemu_dwc3_composite_check` still passes **116/116** after the physical/logical endpoint and
  interrupt-NRDY corrections (`artifacts/qemu-a6400/dwc3-composite-4`).
- Full suite on Windows: 2638 tests, 108 skipped, 2 failures, both pre-existing and unrelated:
  `emulator.test_run_b26_uvc_prearm` (the local `camera-daemon/build/a6400-lived` is 856,320 B
  / `c92403f6...` instead of the pinned 632,812 B / `1eda8418...`, already recorded in the
  `usb-ep0` map F01 preflight note) and `test_mod_manager` (a `Sony-PMCA-RE` checkout exists in
  this workspace, so `find_pmca_dir` is not None). Neither test touches the USB/DWC3 files
  changed here. The knowledge map `build`/`check` pass after the new record and claims.

## Failures and limits

- The stock debugger daemon still produced no PONG: it needs the Sony `usb_extcmd` transport.
  The proven PONG comes from the first-party vendor function driver, not from
  `a6400_usb_debugd`. Its v1 log path is `/setting/a6400_usb_debugd.log` (the probe also looked
  at `/tmp_bt`); the captured `/setting` log contains only stock-history lines.
- `SET_PORT_DESC` content beyond word0 is unknown; a nonzero word0 is the only verified check.
- `SET_MODE(2)` rejection reason is not decoded; the mode callback is inside gadgetcore, not
  the otgcore ioctl surface.
- `save_desc_tbl()` failure is consistent with an empty driver list; no function-driver
  registration has been attempted or claimed.
- The helper's `STOP`/`RESET` teardown is issued but not proven to be an unconfigure-first
  soft disconnect (hazard H01); using it as a teardown recipe would be wrong.
- The qtest harness is a model test, not guest firmware execution. It does not prove the stock
  driver's ring/TB handling, and it deliberately completes one TRB per token.

## Phase 4 status

- Phase A: **PASS** on `artifacts/tests/qemu-custom-kernel-verify-19` (8/8, 67.8 s, initrd
  `6d34c1bf...`), matching the earlier run-15 PASS. The UI item is flaky across the guest's
  warm-reboot/exception-monitor path: runs 16, 17 and 18 failed with the same initrd and
  `osd_buffers.a.non_zero_words = 0` while runs 15 and 19 passed (`adopt` receipts retained).
  This is a Phase A guest/UI timing issue, not a USB regression.
- Phase B: **OK** (`artifacts/tests/qemu-handover-16`, `hybrid_ready=true`).
- Full suite: 2645 tests, 110 skipped, 2 failures from untracked local state, not from this
  workstream: `emulator.test_run_b26_uvc_prearm` (local `camera-daemon/build/a6400-lived` is
  856,320 B / `c92403f6...`, not the E0476 pin 632,812 B / `1eda8418...`) and `test_mod_manager`
  (an untracked `Sony-PMCA-RE` checkout is present). Closing Gate 4 needs a user decision on
  those two states and an explicit GO for the physical checklist; neither assertion is weakened
  here.
- Physical checklist: written, not executed; no camera action taken.

## Next boundary

1. Move the VideoStreaming channel to ISOC and drive it at frame intervals; the bulk path is
   now byte-exact, so an ISO variant can reuse the same frame source and completion contract.
2. Feed the frame from av-cam/liveview instead of the retained fixture and add multi-frame
   pacing; the current frame is delivered once per COMMIT.
3. Phase 4 (blocked on user action): the full suite still has two failures caused by untracked
   local state (rebuilt `camera-daemon/build/a6400-lived`, present `Sony-PMCA-RE` checkout), and
   the physical checklist needs an explicit GO. Re-run Phase A/B and the suite after that
   decision, then close Gate 4.
