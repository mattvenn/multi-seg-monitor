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
  gamma shaping. 
* Colour — a whole-display choice, not per-segment colour, since every display
  this imitates is single colour anyway. A reset-time strap picks the physical
  Pmod and one of 8 built-in palettes; a config packet sent before streaming can
  override the Pmod and load any curve. With no host the generator cycles
  through the 8 presets, and `ui_in`'s spare bits are live DIP switches that
  hold one instead. See "Reset-time config strap", "DIP switches" and "Config
  packet" below, and `tools/palette_builder` to design palettes.

# Status

| | |
|---|---|
| VGA | 800x600 @ 60 Hz, 40 MHz pixel clock |
| Tests | 19 cocotb (5 compare pixel for pixel against `test/gold/`), 3 converter, 17 palette, 23 palette-builder; 5 formal proofs |
| FPGA | 1101 / 5280 logic cells (20%), 2 / 30 EBR, 3 / 8 DSP on the UP5K |
| FPGA timing | nextpnr reports ~31–34 MHz depending on seed, against the 40 MHz target — it fails the check but runs on real hardware anyway |
| ASIC | `ihp-sg13g2`, 2x2 tiles: GDS builds, LVS matches, no setup or hold violations, gate-level sim and the Tiny Tapeout precheck pass. Tight — 86% core utilisation at the last measurement, before the attract-mode logic went in |

The internal generator and streamed playback both run on the FPGA breakout, driven from
the demoboard. Steps 1 and 2 of [Bring-up](#bring-up) are the ones that have been through
it; vsync tearing was found and fixed there — see
[If the picture tears](#if-the-picture-tears).

# Building

Needs [oss-cad-suite](https://github.com/YosysHQ/oss-cad-suite-build) on the path. The
converter and palette-builder tests also need numpy, and the builder's GUI needs Tk.

    make test                 # cocotb tests + converter and palette tests
    make formal               # SymbiYosys proofs
    make bitstream            # synth + place & route + pack, for the TT FPGA breakout
    make -C test delay-sweep  # vsync latency sweep, ~3 min under verilator, writes frame_delay_*.png
    make -C test gold         # rewrite the gold images after an intended change

`SIM=verilator` speeds the frame-capture tests up several times over icarus, the default
(the whole suite runs in about a minute). `IHP_SRAM=1` runs the suite against the
foundry SRAM macro instead of an inferred array, which is what the ASIC hardens; CI runs
both.

`make bitstream` mirrors `tt_fpga.py harden` so it runs without the tt-support-tools
python environment. It currently stops at nextpnr's 40 MHz timing check, before
`icepack`; add `--timing-allow-fail` to the nextpnr line in the `Makefile` to get a
flashable `.bin`. Deploying to the demoboard needs the real tool:

    python tt_fpga.py --project-dir . configure --upload

or, equivalently:

    make flash TT_TOOLS=/path/to/tt-support-tools PORT=/dev/ttyACM4

`flash` rebuilds the bitstream if needed, then runs `tt_fpga.py configure --upload
--set-default --clockrate 40000000` against it. `TT_TOOLS` defaults to
`~/asic/tt-support-tools` and `PORT` to `/dev/ttyACM4`. `configure --upload` needs
tt-support-tools' python environment, which can be missing `klayout` and `chevron` —
`pip install -r requirements.txt` in that repo.

The frame tests capture from the output pins — what the Pmod would see — and write
`test/frame.png`, so geometry can be iterated without hardware. Five captures are also
compared pixel for pixel against committed images in `test/gold/`, which is what catches
a change in how the picture looks rather than a violation of a rule — a segment a pixel
wide, a brightness code off by one. A mismatch writes a `_diff.png` with the disagreeing
pixels in red. After an intended change, `make -C test gold` rewrites them; look at what
it produces before committing, as nothing else will.

# Pinout

The default output is a **Digilent PmodVGA** across both output headers:

| Pin | Use |
|---|---|
| `uo_out[3:0]` / `uo_out[7:4]` | R / B nibbles |
| `uio[3:0]` | G nibble |
| `uio[4]` / `uio[5]` | hsync / vsync |
| `uio[6]` | stream strobe (config strobe while `uio[7]` is low) |
| `uio[7]` | mode select: high = stream, low = internal generator + config packets |
| `ui_in[7:0]` | stream data (bits 3:0 double as the reset strap) |

Strobe and mode select sit where PmodVGA's second connector leaves two pins not
connected. The **Tiny Tapeout VGA Pmod** is the alternative output: 2 bits per channel,
all on `uo_out` as `{hsync, B0, G0, R0, vsync, B1, G1, R1}`, `uio[0:5]` unused and
`uio_oe` all-input. `uio[6:7]` stay exactly where they are in both modes.

### Reset-time config strap

`ui_in[3:0]` is sampled once, held for the whole reset pulse, then reverts to
ordinary stream data for the rest of the chip's life:

| Bit | Meaning |
|---|---|
| `ui_in[0]` | 0 = Digilent PmodVGA (default), 1 = classic Tiny Tapeout VGA Pmod |
| `ui_in[3:1]` | which of the 8 built-in palettes (`tools/palette_builder/presets.json`): 0 grey, 1 blue, 2 green, 3 purple, 4 amber, 5 red, 6 cyan, 7 fire |

The host must hold its chosen value on these bits for the entire reset pulse,
not just assert it once — the strap register re-samples every cycle `rst_n`
is low, so the *last* value before it rises is what sticks. The strap is what
makes the pins safe from the first cycle out of reset: a Tiny VGA board must
never see `uio` driven while it waits for a config packet. See
`src/config_port.v` and `src/palette.v`.

### DIP switches

`ui_in[0]` is the Pmod strap and is read at reset only, never live. The rest of
`ui_in` is read live, but only in generator mode, where nothing else is using those
pins. All off is the default attract mode:

| Bit | Off (default) | On |
|---|---|---|
| `ui_in[3:1]` | the starting palette (reset strap) | the palette held in manual mode |
| `ui_in[4]` | palette changes every 1024 frames (~17 s), fading through black | hold the palette `ui_in[3:1]` names |
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
preset every 1024 frames (~17 s), fading through black across the change, until
a packet or the manual switch turns cycling off. A valid header also hands the
`ui_in` switches to the host for good, as above. `firmware/`'s
`main(curve=...)` sends one for you; `tools/segments.py`'s `config_packet()`
builds one from Python, and `tools/palette_builder` exports the exact bytes.

# Bring-up

**1. Internal generator, no firmware.** Leave `uio[7]` low and the design ignores the
stream port entirely. You should get a zone plate drawn across the 64x37 grid:
concentric rings that tighten outward and flow slowly, with the palette changing
every ~17 s through a fade to black. Compare against `test/gold/generator.png`,
which is the same thing from simulation, a few frames after reset.

The chip has no pull on `uio[7]`, so what the demoboard does with that pin at
power-up decides whether you get this or a blank screen waiting for a stream.
That has not been checked on hardware yet; if nothing appears, tie it low
before suspecting anything else. Then work through the switches above: each
one should change what you see within two frames.

# Playing video

    tools/video2seg.py clip.mp4 video.seg --fps 24    # 9472 bytes per frame
    tools/seg2png.py video.seg preview.png --frame 30 # check it before deploying

Each of the 18944 segments averages the source pixels its own rectangle covers, in
linear light — one sample per digit would throw away most of the resolution that
per-segment brightness exists to provide.

Copy the `.seg` file and `firmware/seg_player.py` to the demoboard. Frames are 9472
bytes; `.seg` files made for the old 640x480 mode (6240 bytes a frame) need
regenerating, not reusing.

The chip has no framebuffer, so the player re-pushes every displayed frame at 60.3 Hz
(≈571 kB/s) regardless of the video's own rate. Pacing is free: a digit row is 16
scanlines and 256 bytes, so one byte every 66 pixel clocks tracks the raster exactly,
with no remainder to accumulate.

### Choosing the Pmod and palette

`firmware/seg_player.py` (streaming) and `firmware/gen_mode.py` (internal generator)
both take the choice as arguments to `main()`, defaulting to the `PMOD_TYPE` and
`PALETTE` constants at the top of each file:

| Argument | Values |
|---|---|
| `pmod_type` | 0 = Digilent PmodVGA, 1 = Tiny VGA Pmod |
| `palette` | 0 grey, 1 blue, 2 green, 3 purple, 4 amber, 5 red, 6 cyan, 7 fire |
| `curve` | optional custom palette, overriding `palette`: three `(x1, y1, x2, y2)` point pairs for R, G, B, as `tools/palette_builder`'s Export prints |
| `cycle` | `gen_mode.py` only: `True` (the default) steps through all 8 presets every ~17 s |

`pmod_type` and `palette` go in as the reset strap; `curve` and `cycle` are sent as a
config packet after reset, before any pixels (see "Config packet" above). To change the
defaults, edit the constants and use `mpremote run`:

    mpremote run firmware/seg_player.py

or copy the file across once and call `main()` yourself (`mpremote run` execs a single
file, so the firmware can't import from `tools/`):

    mpremote cp firmware/seg_player.py :
    mpremote exec "import seg_player; seg_player.main(pmod_type=0, palette=4)"

    mpremote cp firmware/gen_mode.py :
    mpremote exec "import gen_mode; gen_mode.main(pmod_type=0, curve=((1, 0, 8, 15), (1, 0, 15, 10), (10, 0, 15, 5)))"

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
