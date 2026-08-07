# Multi Segment Monitor

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

Stage 1 is done: the design renders the full grid from its internal generator, with
no external data.

| | |
|---|---|
| VGA | 640x480 @ 72 Hz, 31.5 MHz |
| FPGA | 384 / 5280 logic cells (7%), 1 / 30 EBR |
| Timing | 40.68 MHz max, passes at 31.5 MHz |
| Tests | VGA timing, blanking, rendered frame — all passing |

Next: the RP2350 push port, then streaming video.

# Building

Needs [oss-cad-suite](https://github.com/YosysHQ/oss-cad-suite-build) on the path.

    make bitstream      # synth + place & route + pack, for the TT FPGA breakout
    make test           # cocotb tests, writes test/frame.png

`make bitstream` mirrors `tt_fpga.py harden` so it runs without the tt-support-tools
python environment. Deploying to the demoboard still needs the real tool:

    python tt_fpga.py --project-dir . configure --upload

The frame test captures a real frame from the output pins and writes
`test/frame.png`, so geometry can be iterated without hardware. That loop is what
caught the first segment layout filling its whole cell — adjacent digits merged into
each other and the grid was illegible.

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
