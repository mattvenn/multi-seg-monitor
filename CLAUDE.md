# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Tiny Tapeout ASIC that renders a 52x30 grid of 7-segment digits (1560 digits,
12480 segments, 4 bits of brightness each) as a 640x480@72Hz VGA signal. Data is
streamed in a byte at a time by the demoboard's RP2350. `SPEC.md` carries the full
design and the reasoning behind the rejected alternatives; read it before proposing
an architectural change, because most obvious ones were already considered there.

## Toolchain

`oss-cad-suite` (yosys / nextpnr / icestorm / iverilog) is **not on PATH by default**:

    export PATH=~/asic/oss-cad-suite/bin:$PATH

FPGA builds also want tt-support-tools, defaulting to `~/asic/tt-support-tools`;
override with `TT_TOOLS=`.

## Commands

    make test                       # cocotb suite + converter tests
    make bitstream                  # yosys + nextpnr + icepack for the TT FPGA breakout
    make flash PORT=/dev/ttyACM4    # upload to the demoboard (needs tt-support-tools' venv)

    make -C test IHP_SRAM=1         # same suite against the IHP macro instead of an inferred array
    make -C test delay-sweep        # vsync latency sweep, ~10 min, writes frame_delay_*.png
    make -C test gold               # rewrite the gold images after an intended change

A single cocotb test:

    make -C test COCOTB_TEST_FILTER='"test_stream_frame"'

The quotes are doubled deliberately — `Makefile.sim` pastes `COCOTB_TEST_FILTER`
into the recipe unquoted, so a regex containing `|` would otherwise be read by the
shell as a pipeline. Also note `make` returns success even when tests fail; CI
checks with `! grep failure results.xml`, and `results.xml` is what `Makefile.sim`
uses to decide a run is up to date, so delete it to force a rerun.

Waveforms: `make -C test COCOTB_PLUSARGS=+vcd` — the testbench gates `$dumpvars` on
a `+vcd` plusarg, so no VCD is written without it.

## Architecture

The chip **races the beam and holds no framebuffer** — only the digit row being
drawn. Everything below follows from that.

`tt_um_multi_seg_monitor.v` is the TT wrapper and does nothing but pin mapping.
`multi_seg_monitor.v` is the core, and it is where the interesting timing lives:

- **Line buffer** (`line_buffer.v`) holds **four** rows of 208 bytes in a 1 kB
  address space. Four rather than two is what lets the host free-run at a fixed byte
  rate between one and three rows ahead instead of handshaking every row.
- **Single-port discipline.** The renderer reads 4 of every 12 cycles to prefetch
  the next digit; `wr_grant = !lb_re` gives writes the other 8. `we` and `re` must
  **never** be high in the same cycle — on the IHP macro that combination is
  write-through and would silently put `wdata` at `raddr`. `tb.v` asserts this every
  cycle rather than trusting the arbitration.
- **Two memory implementations behind one interface.** Undefined, `line_buffer.v`
  infers an array (FPGA, default simulation). `IHP_SRAM` instantiates the foundry
  macro (`src/config.json` defines it for the ASIC). Synthesis sees a ports-only
  blackbox stub in `src/`; simulation sees the real behavioural models in
  `test/models/`. Changes to the buffer must be tested both ways — CI runs the suite
  twice for this reason.
- **Renderer** decodes each segment as one AND of an x zone and a y zone. The digit
  body is 10x14 inside a 12x16 cell; the spare column and two spare rows are
  load-bearing, not cosmetic — without them neighbouring digits merge and the grid
  reads as a mesh.
- **Source mux**: internal generator (`uio[7]` low, needs no external data, is the
  silicon bring-up safety net) or the stream port (`stream_in.v`). The write pointer
  resets on vsync, so the link is self-synchronising.

### Pacing is the load-bearing invariant

A digit row is 16 scanlines and 208 bytes, so `16 * 832 / 208 = 64` pixel clocks per
byte, **exactly**. No remainder means a fixed-rate host tracks the raster
indefinitely. The host's whole head start is vertical blanking, 819 µs, and anything
spent before its first byte comes straight off it — the budget from vsync to first
byte is about **450 µs**. This is not theoretical: exceeding it is what tore the
picture on hardware, and `make -C test delay-sweep` reproduces it in simulation.
See "If the picture tears" in `README.md`.

### Geometry is written down twice

`src/multi_seg_monitor.v` and `tools/segments.py` both encode the segment
rectangles, the gamma table and the nibble ordering. If one changes the other must
too, or the round-trip test will say so. Nibble order within a digit word is fixed
low-to-high as `a, b, c, d, e, f, g, DP` — host software depends on it.

### Pinout

Prototype output is a **Digilent PmodVGA across both headers**, so video spans
`uo_out` *and* `uio`:

| | |
|---|---|
| `uo_out[3:0]` / `uo_out[7:4]` | R / B nibbles |
| `uio[3:0]` | G nibble |
| `uio[4]` / `uio[5]` | hsync / vsync |
| `uio[6]` / `uio[7]` | stream strobe / mode select |
| `ui_in[7:0]` | stream data |

`uio_oe` is `8'b0011_1111`. `SPEC.md` section 7 still describes the earlier Tiny VGA
prototype and the native 6-bit custom Pmod; **the RTL, `info.yaml`, `tb.v` and
`firmware/seg_player.py` are the truth.** Moving these pins breaks all four at once.

## Testing approach

Two layers, deliberately:

- **Property assertions** — margins clear, cell corners dark, no blank digit row,
  plausible lit fraction. These say the picture is legal.
- **Gold images** (`test/gold/`) — three deterministic captures compared pixel for
  pixel. These say it is the *same* picture, which is what catches a segment a pixel
  wide or a gamma entry off by one. A mismatch writes `<name>_diff.png` with the
  disagreeing pixels in red. `make -C test gold` rewrites them; **look at what it
  produces before committing, because nothing else will.**

The frame capture is written from Verilog (`tb.v` → `frame.ppm`) rather than cocotb,
so a whole frame advances in one `ClockCycles` await instead of 432640 Python
callbacks. It samples the output pins only, so what lands in the file is what the
Pmod would see.

The delay sweep's corrupted captures are deliberately **not** golden — they are
observations of a fault, not a specification of one.

## ASIC flow

CI (`.github/workflows/gds.yaml`) hardens against `ihp-sg13g2`. Current state: GDS
builds, LVS matches uniquely, no setup or hold violations, gate-level sim passes,
but **TT precheck fails on 2672 KLayout DRC violations** — all inside the IHP SRAM
macro's own subcells (`RM_IHPSG13_1P_BITKIT_*`, `_BLDRV`, `_COLCTRL2`), none in this
design's logic. `src/config.json` skips DRC locally but precheck does not honour
that.

`info.yaml` says `tiles: "3x2"` (636.96 x 313.74 µm). Some comments in `SPEC.md`
section 8.2 and `src/config.json` still reason about the earlier 4x2; the macro
placement at `[42, 80]` `R90` fits either. `R90` is not optional — the macro is
336.46 µm tall upright, which does not fit in 313.74 µm of die height.

## Conventions

- Comments explain *why*, especially where a choice looks arbitrary or wrong — the
  `A_MEN` divergence from the proven design, the doubled quotes in the test filter,
  the load-bearing cell gaps. Match that density; a bare restatement of the code is
  worse than nothing here.
- Don't add capability the FPGA has and the ASIC does not. The UP5K is a
  feature-parity verification target; its spare EBR and SPRAM go deliberately
  unused. Design single-port, and never assume initialised memory.
