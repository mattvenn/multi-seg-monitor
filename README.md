# Multi Segment Monitor

[![Made with Claude](https://img.shields.io/badge/Made%20with-Claude-D97757?logo=anthropic&logoColor=white)](https://claude.com/claude-code)

Instead of putting hundreds of 7 segment displays together (which is awesome),
take the short cut of using a VGA screen to simulate the displays.

An ASIC does the VGA signal generation, using the Tiny Tapeout standard.

The RP2350 on the demoboard interfaces with the ASIC to send data.

A 64 x 37 grid of digits — 2368 digits, 18944 segments — each segment with its own
4 bit brightness. The chip holds no framebuffer: it races the beam, keeping only the
digit row it is currently drawing. See [SPEC.md](SPEC.md) for the full design and the
reasoning behind it, and [resolution_discussion.md](resolution_discussion.md) section
11-12 for why the resolution moved from SPEC.md's original 640x480@72 to 800x600@60.

# Controls

* Data to display
* Levels of brightness — 4 bits per segment, sent through a colour palette (see
  below). A palette is three per-channel curves, each two straight lines through
  a movable knee, clipped at 15 — enough for tinted ramps and basic per-monitor
  gamma shaping. A 4-bit DAC can't bend a curve and still keep all 16 stored
  levels distinct (pigeonhole — see the comment in `src/palette.v`), so the knee
  chooses where levels collide rather than avoiding it; preset 0 is the plain
  grey identity.
* Colour — a whole-display choice, not per-segment colour, since every display
  this imitates is single colour anyway. A reset-time strap picks the physical
  Pmod and one of 8 built-in palettes; a config packet sent before streaming can
  override the Pmod and load any curve. With no host the generator cycles
  through the 8 presets, and `ui_in`'s spare bits are live DIP switches that
  hold one instead. See "Reset-time config strap", "DIP switches" and "Config
  packet" below, and `tools/palette_builder` to design palettes.

# Status

RTL complete: internal generator, byte-wide stream port, and a video pipeline that
turns any clip into segment intensities.

| | |
|---|---|
| VGA | 800x600 @ 60 Hz, 40 MHz |
| FPGA | not re-measured since the move to 800x600 (was 431 / 5280 logic cells, 8%, 2 / 30 EBR, at 640x480) |
| Timing | 39.58 MHz max, ~1% short of the 40 MHz target — runs clean on real UP5K hardware anyway (resolution_discussion.md §11) |
| Tests | 4 cocotb + 3 converter, all passing, 3 frames against gold images |

**Working on the FPGA breakout.** The internal generator and the stream port both run
on hardware, driven from the demoboard — steps 1 and 2 of [Bring-up](#bring-up),
which is also where what that run cost is written down. Full rate video, step 3, has
not been run.

**All 16 grey levels confirmed distinguishable on hardware.** A gamma curve here
can't both warp the perceptual response and keep 16 stored levels mapped to 16
distinct DAC codes -- a 4-bit output only has 16 codes to begin with, so any
non-trivial curve is forced to collide two of them (indices 14 and 15 did,
exactly, before this was found). Removing the curve and sending the stored
intensity straight to the DAC was confirmed on real hardware, not just in
simulation -- see the comment above `grey` in `src/multi_seg_monitor.v` and
`dithering_investigation.md` on the `gamma-dithering` branch for the investigation
that found it.

**The vsync tearing is fixed.** `firmware/seg_player.py` takes the vsync interrupt
with `hard=True`, which puts the DMA restart about 80 µs after the edge against a
budget of roughly 450 µs. Measured on a scope, not inferred — see
[If the picture tears](#if-the-picture-tears) for the captures and for why the
handler, not the restart code, was the slow part.

For the ASIC, `info.yaml`/`src/config.json` now target 40 MHz to match the RTL, and
the GDS builds, LVS matches, timing closes at that constraint on `ihp-sg13g2`, and
gate level simulation passes. Tiny Tapeout's precheck still fails on 2672 KLayout DRC
violations, unrelated to any of this — all of them are inside the IHP SRAM macro's
own cells rather than in this design.

# Building

Needs [oss-cad-suite](https://github.com/YosysHQ/oss-cad-suite-build) on the path.

    make bitstream            # synth + place & route + pack, for the TT FPGA breakout
    make test                 # cocotb tests + converter tests
    make -C test delay-sweep  # vsync latency sweep, ~9 min, writes frame_delay_*.png
    make -C test gold         # rewrite the gold images after an intended change

`make bitstream` mirrors `tt_fpga.py harden` so it runs without the tt-support-tools
python environment. Deploying to the demoboard still needs the real tool:

    python tt_fpga.py --project-dir . configure --upload

or, equivalently:

    make flash TT_TOOLS=/path/to/tt-support-tools PORT=/dev/ttyACM4

`flash` rebuilds the bitstream if needed, then runs `tt_fpga.py configure --upload
--set-default --clockrate 40000000` against it. `PORT` defaults to `/dev/ttyACM4`.

The frame test captures from the output pins and writes `test/frame.png`, so geometry
can be iterated without hardware. That loop is what caught the first segment layout
filling its whole cell — adjacent digits merged into each other and the grid was
illegible.

Three captures are also compared pixel for pixel against committed images in
`test/gold/`, which is what catches a change in how the picture looks rather than a
violation of a rule — a segment a pixel wide, a brightness code off by one. A mismatch
writes a `_diff.png` with the disagreeing pixels in red. After an intended change,
`make -C test gold` rewrites them; look at what it produces before committing, as
nothing else will.

# Bring-up

The order to try it in, chosen so each step adds one thing and a failure points
somewhere specific. Steps 1 and 2 have been through this on the FPGA breakout, and
the notes below are what that cost rather than what was expected to.

**On a new machine.** The build needs two paths:

    export PATH=/path/to/oss-cad-suite/bin:$PATH
    make bitstream TT_TOOLS=/path/to/tt-support-tools

`configure --upload` also needs tt-support-tools' python environment, which was
missing `klayout` and `chevron` on the machine this was written on —
`pip install -r requirements.txt` in that repo.

The default output is a **Digilent PmodVGA** across both output headers: R on
`uo_out[3:0]`, B on `uo_out[7:4]`, G on `uio[3:0]`, hsync on `uio[4]` and vsync on
`uio[5]`. Strobe and mode select sit on `uio[6]` and `uio[7]`, which is where
PmodVGA leaves two pins not connected.

### Reset-time config strap

`ui_in[3:0]` is sampled once, held for the whole reset pulse, then reverts to
ordinary stream data for the rest of the chip's life:

| Bit | Meaning |
|---|---|
| `ui_in[0]` | 0 = Digilent PmodVGA (default), 1 = classic Tiny Tapeout VGA Pmod |
| `ui_in[3:1]` | which of the 8 built-in palettes (`tools/palette_builder/presets.json`): 0 grey, 1 blue, 2 green, 3 purple, 4 amber, 5 red, 6 cyan, 7 fire |

The host must hold its chosen value on these bits for the entire reset pulse,
not just assert it once — the strap register re-samples every cycle `rst_n`
is low, so the *last* value before it rises is what sticks. Tiny VGA mode
only uses `uo_out` (2 bits/channel); `uio[0:5]` go unused and `uio_oe` goes
all-input in that mode, `uio[6:7]` (strobe/mode select) stay exactly where
they are in both modes. See `src/config_port.v` and `src/palette.v`.

### DIP switches

`ui_in[0]` is the Pmod strap and is read at reset only, never live — a Tiny
VGA board must not see `uio` driven while it waits. The rest of `ui_in` is
read live, but only in generator mode, where nothing else is using those pins.
All off is the default attract mode:

| Bit | Off (default) | On |
|---|---|---|
| `ui_in[3:1]` | the starting palette (reset strap) | the palette held in manual mode |
| `ui_in[4]` | palette changes every 512 frames (~8.5 s), fading through black | hold the palette `ui_in[3:1]` names |
| `ui_in[5]` | the rings' flow speed wanders | steady flow |
| `ui_in[6]` | the sources' drift speed wanders | steady drift |
| `ui_in[7]` | spare | spare |

Two things keep a host out of this. A switch setting is only applied once two
consecutive frames have sampled the same value, so a config byte sitting on
the bus across one frame boundary can't be read as a switch position. And the
first valid config header turns the switches off for good — the RP2350 drives
these pins to send a packet, and whatever it leaves there afterwards is not a
switch. The setting last applied stays in force. Streaming ignores them
outright, because `ui_in` is pixel data then.

The variation is deliberately slow and never jumps. Only the two speeds move;
the ring phase and the source positions are accumulators, so a speed change
bends the motion rather than displacing it. The ring speed steps every 256
frames over a 2.3-minute round trip (slightly reversed, through stopped, to
3x) and the drift speed every 1024 frames over 4.5 minutes (0.5x to 1.4x).

### Config packet

With `uio[7]` low the internal generator draws the picture, and each strobe on
`uio[6]` carries a config byte instead of a pixel byte. Every fall of `uio[7]`
starts a new packet:

| Byte | Contents |
|---|---|
| 0 | header `{4'hA, load_preset, cycle_en, 0, pmod_type}` — without the `A` the whole packet is ignored, so a stray strobe on a bare board can't flip the Pmod |
| 1–3 | red curve: `{knee_x, knee_y}`, `m1`, `m2` (slopes in eighths, 5 bits) |
| 4–6 | green, the same |
| 7–9 | blue, the same |

A header alone is enough to change the Pmod or turn preset cycling on or off;
`load_preset` puts the strapped preset back. The generator steps to the next
preset every 512 frames (~8.5 s), fading through black across the change, until
a packet or the manual switch turns cycling off. A valid header also hands the
`ui_in` switches to the host for good, as above. `firmware/`'s
`main(curve=...)` sends one for you; `tools/segments.py`'s `config_packet()`
builds one from Python, and `tools/palette_builder` exports the exact bytes.

**1. Internal generator, no firmware.** Leave `uio[7]` low and the design ignores the
stream port entirely. You should get a zone plate drawn across the 64x37 grid:
concentric rings that tighten outward and flow slowly, with the palette changing
every ~8.5 s through a fade to black. Compare against `test/gold/generator.png`,
which is the same thing from simulation, a few frames after reset.

The chip has no pull on `uio[7]`, so what the demoboard does with that pin at
power-up decides whether you get this or a blank screen waiting for a stream.
That has not been checked on hardware yet; if nothing appears, tie it low
before suspecting anything else. Then work through the switches above: each
one should change what you see within two frames.

If this fails, in rough order of likelihood:

| Symptom | Look at |
|---|---|
| Nothing at all, and the project never came up | `tt.shuttle.<name>.enable()` reads a dedicated GPIO to detect the FPGA carrier, and on this board that detect is unreliable — it falls back to the ASIC shuttle mux and the name lookup fails. `firmware/seg_player.py` pushes the bitstream with `spi_transferPIO` instead, which is all `.enable()` does for an FPGA target |
| No signal / monitor out of range | The clock. Ask `clock_project_PWM` what it actually produced rather than assuming a divider: it retunes the RP2350's own sysclk to whatever divides most cleanly to the target, so the ratio from sysclk to pixel clock is not fixed. Anything derived from the pixel clock has to be derived from the value it returns |
| Sync but no picture, or garbage | Pmod pinout. Only PmodVGA is wired up, and it needs both headers — hsync and vsync are on `uio[4:5]`, not on `uo_out` at all. Tiny VGA and the VGA Clock pmod both have a different order |
| Picture but sheared or rolling | VGA timing constants — but these come from `ttihp0p4-vga-clock`, which works, so suspect the clock first |

**2. Streamed static image.** Set `uio[7]` high and push a single frame repeatedly.
`tools/video2seg.py` on a still image gives you one, and `tools/make_diag_pattern.py`
gives you a frame built to make corruption countable rather than subtle — one segment
per row keyed on `row % 4`, so stale data from the wrong line buffer slot lights the
wrong segment instead of shifting a brightness. If the generator worked and this does
not, the problem is in the stream port or the player, not the renderer.

**3. Video.** Only after 2 is stable.

## If the picture tears

This is the failure the hardware run actually produced, and it is worth recognising
on sight because the cause is not where it appears to be.

Each digit row is drawn from a line buffer the host is still filling. The renderer
re-reads the whole 256 byte row on every one of its 16 scanlines — 1056 clocks — while
the host takes 16896 to fill it, so the host has to be a **full row ahead**. It builds
that lead during vertical blanking, 713 µs, and anything spent before the first byte
comes straight off it.

`make -C test delay-sweep` puts numbers on it by delaying the testbench host's first
byte after vsync, 0 to 1000 µs, and writing a frame for each:

| Delay before first byte | Lead | Result |
|---|---|---|
| ≤ 400 µs | ≥ 190 bytes | clean |
| 500 µs | 129 bytes | tears from column 52 |
| 600 µs | 68 bytes | tears from column 36 |
| 800 µs | -53 bytes | tears from column 4 |

So the budget from vsync to the first byte is **about 450 µs**, and what blew it was
the *dispatch*, not the handler body.

`Pin.irq` defaults to a soft IRQ, which does not run in the interrupt at all — it
waits for `micropython.schedule()` to reach a bytecode boundary, and `service()`
reads 6240 bytes off flash in one uninterruptible call. That routinely pushed the
first strobe out to 500–600 µs. Passing `hard=True` dispatches from the interrupt
itself and brings it to about 80 µs, comfortably inside budget, with the ordinary
`dma.config()` body left alone.

Both captures below are the same trigger on vsync falling, with the cursor pair set
542 µs apart:

![soft IRQ](docs/scope/instrumented_soft_irq.png)

*Soft IRQ: nothing has moved by the 542 µs cursor, and the byte stream starts after
it.*

![hard IRQ](docs/scope/instrumented_hard_irq.png)

*`hard=True`: the handler runs at the left edge of the window instead.*

Rewriting the handler to poke the DMA registers directly was tried first and is not
the answer. The restart code was never the slow part, and the register version broke
the DMA trigger outright — no strobe and no data at all.

If it ever tears again, measure from vsync falling to the first strobe before
changing anything. Interrupt latency itself is under 10 µs on this platform, so it is
not a plausible cause on its own.

The signature, if you want to confirm the diagnosis rather than infer it: the tear
starts at column ≈ lead/4 + 2.5 and walks right as the row is drawn, so digits show
one frame in their top half and another in their bottom. The stale content is digit
row **R−4**, four buffers back — on a still image that reads as a piece of the picture
from elsewhere, not as a repeat.

# Playing video

    tools/video2seg.py clip.mp4 video.seg --fps 24    # 9472 bytes per frame
    tools/seg2png.py video.seg preview.png --frame 30 # check it before deploying

Each of the 18944 segments averages the source pixels its own rectangle covers, in
linear light — one sample per digit would throw away most of the resolution that
per-segment brightness exists to provide.

Copy the `.seg` file and `firmware/seg_player.py` to the demoboard. Frames grew 52%
over the 640x480 mode's 6240 bytes (`resolution_discussion.md` §11), so the same 4 MB
flash now holds about 17 seconds at 24 fps rather than 26 — existing `.seg` files
predate the frame size change and need regenerating, not reusing.

### Choosing the Pmod and palette

`firmware/seg_player.py` (streaming) and `firmware/gen_mode.py` (internal generator)
both take the choice as arguments to `main()`:

| Argument | Values |
|---|---|
| `pmod_type` | 0 = Digilent PmodVGA, 1 = Tiny VGA Pmod |
| `palette` | 0 grey, 1 blue, 2 green, 3 purple, 4 amber, 5 red, 6 cyan, 7 fire |
| `curve` | optional custom palette, overriding `palette`: three `(x1, y1, x2, y2)` point pairs for R, G, B, as `tools/palette_builder`'s Export prints |
| `cycle` | `gen_mode.py` only: `True` (the default) steps through all 8 presets every ~8.5 s |

`mpremote run firmware/seg_player.py` calls `main()` with its defaults, so to choose,
copy the file across once and call it yourself:

    mpremote cp firmware/seg_player.py :
    mpremote exec "import seg_player; seg_player.main(pmod_type=0, palette=4)"

    mpremote cp firmware/gen_mode.py :
    mpremote exec "import gen_mode; gen_mode.main(pmod_type=0, curve=((1, 0, 8, 15), (1, 0, 15, 10), (10, 0, 15, 5)))"

Or edit the `main()` call at the bottom of the file and keep using `mpremote run`.
`pmod_type` and `palette` go in as the reset strap; `curve` and `cycle` are sent as a
config packet after reset, before any pixels (see "Config packet" above).

The chip has no framebuffer, so the player re-pushes every displayed frame at 60.3 Hz
(≈571 kB/s) regardless of the video's own rate. Pacing is free: a digit row is 16
scanlines and 256 bytes, so one byte every 66 pixel clocks tracks the raster exactly,
with no remainder to accumulate.

# Inspiration

* https://hackaday.com/2020/03/05/144-7-segment-displays-combine-to-form-a-mighty-clock/
* https://hackaday.com/2025/05/24/ai-art-installation-swaps-diffusion-for-reflection/
* https://hackaday.com/2013/11/21/7-segment-display-matrix-visualizes-more-than-numbers/
* https://hackaday.com/2021/09/14/whats-cooler-than-a-7-segment-display-a-7200-segment-display/
* https://hackaday.com/2023/02/23/sailing-on-a-sea-of-seven-segment-displays/
* https://hackaday.com/2012/03/30/display-made-out-of-hundreds-of-seven-segment-lcds/
* Sea of Segments — 1536 digits, 5 bit grayscale, the closest real world comparable
  https://willga.llia.io/sea-of-segments/build/

# Resources

* Tiny Tapeout VGA standard https://vga-playground.com/
* Tiny Tapeout VGA Pmod https://tinytapeout.com/specs/pinouts/#vga-output
* Tiny Tapeout ETR demoboard https://tinytapeout.com/guides/get-started-demoboard-etr/
* Tiny Tapeout FPGA breakout https://tinytapeout.com/guides/fpga-breakout/
* Example RLE Video player: https://tinytapeout.com/chips/ttsky25a/tt_um_MichaelBell_rle_vga
