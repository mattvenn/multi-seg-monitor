# Multi Segment Monitor

[![Made with Claude](https://img.shields.io/badge/Made%20with-Claude-D97757?logo=anthropic&logoColor=white)](https://claude.com/claude-code)

Instead of putting hundreds of 7 segment displays together (which is awesome),
take the short cut of using a VGA screen to simulate the displays.

An ASIC does the VGA signal generation, using the Tiny Tapeout standard.

The RP2350 on the demoboard interfaces with the ASIC to send data.

A 52 x 30 grid of digits — 1560 digits, 12480 segments — each segment with its own
4 bit brightness. The chip holds no framebuffer: it races the beam, keeping only the
digit row it is currently drawing. See [SPEC.md](SPEC.md) for the full design and the
reasoning behind it.

# Controls

* Data to display
* Levels of brightness — 4 bits per segment, gamma corrected
* Colour — set by a jumper on the output PMOD, not at runtime. Six output pins cannot
  carry both fine gradation and per-pixel colour, and every display this imitates is
  single colour anyway.

# Status

RTL complete: internal generator, byte-wide stream port, and a video pipeline that
turns any clip into segment intensities.

| | |
|---|---|
| VGA | 640x480 @ 72 Hz, 31.5 MHz |
| FPGA | 422 / 5280 logic cells (7%), 2 / 30 EBR |
| Timing | 41.13 MHz max, passes at 31.5 MHz |
| Tests | 4 cocotb + 3 converter tests, all passing |

**None of this has run on hardware yet.** Everything is verified in simulation: the
stream test pushes a full frame in over the port and reads all 12480 segments back
off the screen, so the protocol, the pacing and the packing are proven end to end —
but the FPGA breakout has not been available to try it on. The MicroPython player in
particular is written against the demoboard pinout and the `rp2` API without ever
having been run.

# Building

Needs [oss-cad-suite](https://github.com/YosysHQ/oss-cad-suite-build) on the path.

    make bitstream      # synth + place & route + pack, for the TT FPGA breakout
    make test           # cocotb tests + converter tests

`make bitstream` mirrors `tt_fpga.py harden` so it runs without the tt-support-tools
python environment. Deploying to the demoboard still needs the real tool:

    python tt_fpga.py --project-dir . configure --upload

The frame test captures from the output pins and writes `test/frame.png`, so geometry
can be iterated without hardware. That loop is what caught the first segment layout
filling its whole cell — adjacent digits merged into each other and the grid was
illegible.

# Bring-up

Nothing here has touched hardware. This is the order to try it in, chosen so each
step adds one thing and a failure points somewhere specific.

**On a new machine.** The build needs two paths:

    export PATH=/path/to/oss-cad-suite/bin:$PATH
    make bitstream TT_TOOLS=/path/to/tt-support-tools

`configure --upload` also needs tt-support-tools' python environment, which was
missing `klayout` and `chevron` on the machine this was written on —
`pip install -r requirements.txt` in that repo.

**1. Internal generator, no firmware.** Leave `uio[1]` low and the design ignores the
stream port entirely. You should get a 52x30 grid of hex digits scrolling diagonally
with brightness bands across it, at 4 grey levels. Compare against `test/frame.png`,
which is the same thing from simulation.

If this fails, in rough order of likelihood:

| Symptom | Look at |
|---|---|
| No signal / monitor out of range | The clock. 31.5 MHz needs a sysclk that divides to it — 126 MHz / 4. Check what `clock_project_PWM` actually produced |
| Sync but no picture, or garbage | Pmod pinout. Only Tiny VGA is wired up (`uo_out[3]`=vsync, `uo_out[7]`=hsync); the VGA Clock pmod has a different order |
| Picture but sheared or rolling | VGA timing constants — but these come from `ttihp0p4-vga-clock`, which works, so suspect the clock first |

**2. Streamed static image.** Set `uio[1]` high and push a single frame repeatedly.
`tools/video2seg.py` on a still image gives you one. If the generator worked and this
does not, the problem is in the stream port or the player, not the renderer.

**3. Video.** Only after 2 is stable.

The player is the least proven part — it has never been executed. The DMA register
plumbing (`PIO0_TXF0`, `treq_sel`) is the first thing to check, then whether the TT
SDK is still driving GPIO17-24 after PIO claims them.

# Playing video

    tools/video2seg.py clip.mp4 video.seg --fps 24    # 6240 bytes per frame
    tools/seg2png.py video.seg preview.png --frame 30 # check it before deploying
    tools/seg2png.py video.seg preview.png --levels 4 # as the Tiny VGA prototype

Each of the 12480 segments averages the source pixels its own rectangle covers, in
linear light — one sample per digit would throw away most of the resolution that
per-segment brightness exists to provide.

Copy the `.seg` file and `firmware/seg_player.py` to the demoboard. At 6240 bytes per
frame a 4 MB flash holds about 26 seconds at 24 fps.

The chip has no framebuffer, so the player re-pushes every displayed frame at 72.8 Hz
(454 kB/s) regardless of the video's own rate. Pacing is free: a digit row is 16
scanlines and 208 bytes, so one byte every 64 pixel clocks tracks the raster exactly,
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
