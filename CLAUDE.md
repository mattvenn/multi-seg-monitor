# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Tiny Tapeout ASIC that renders a 64x37 grid of 7-segment digits (2368 digits,
18944 segments, 4 bits of brightness each) as an 800x600@60Hz VGA signal. Data is
streamed in a byte at a time by the demoboard's RP2350. `SPEC.md` carries the full
design and the reasoning behind the rejected alternatives; read it before proposing
an architectural change, because most obvious ones were already considered there.
It documents the original 640x480@72Hz mode's arithmetic throughout -- the digit
geometry and every architectural decision still hold at 800x600 (`resolution_discussion.md`
section 11-12 covers why and what changed), but its numeric tables are stale by the
resolution ratio; don't copy a number out of `SPEC.md` without checking which mode
it was computed for.

## Toolchain

`oss-cad-suite` (yosys / nextpnr / icestorm / iverilog / verilator) is **not on
PATH by default**, and the cocotb suite additionally needs its Python (not just
its `bin/`) on PATH, so source the environment script rather than just
prepending `bin`:

    source ~/asic/oss-cad-suite/environment

FPGA builds also want tt-support-tools, defaulting to `~/asic/tt-support-tools`;
override with `TT_TOOLS=`.

## Commands

    make test                       # cocotb suite + converter and palette tests
    make formal                     # SymbiYosys proofs (see formal/Makefile for which pass)
    make bitstream                  # yosys + nextpnr + icepack for the TT FPGA breakout
    make flash PORT=/dev/ttyACM4    # upload to the demoboard (needs tt-support-tools' venv)

    make -C test IHP_SRAM=1         # same suite against the IHP macro instead of an inferred array
    make -C test delay-sweep        # vsync latency sweep, ~5 min under icarus, writes frame_delay_*.png
    make -C test gold               # rewrite the gold images after an intended change
    tools/palette_builder/palette_builder.py              # design palettes (needs Tk + numpy)
    tools/palette_builder/palette_builder.py --write-rtl  # regenerate src/palette_presets.v
    git config core.hooksPath .githooks                   # once per clone: enables the pre-commit hook

`.githooks/pre-commit` refuses a commit whose staged `presets.json` and
`src/palette_presets.v` disagree (it runs `tools/test_palettes.py` on the staged
files). It only runs when one of the palette files is staged. `git commit
--no-verify` skips it deliberately.

Prefer `SIM=verilator` for the frame-capture tests and `gold` specifically --
icarus takes ~30s per frame-capture test, verilator 2-7s, and the whole
suite runs in under a minute under verilator. Frame captures wait on vsync
edges, not clock-cycle counts (`capture_frame()` in `test_multi_seg.py`):
cocotb's `ClockCycles(n)` goes through Python on every edge, and waiting 5
frames that way used to be most of each test's run time. Icarus stays the
default (`SIM ?= icarus` in `test/Makefile`) since it's what CI and the
gate-level (`GATES=yes`) run are proven against; verilator is opt-in for local
iteration:

    SIM=verilator make -C test gold

On macOS the Verilator-built `Vtop` loads oss-cad-suite's libpython, which
looks for its own dependencies at `@executable_path/../lib` -- i.e.
`test/sim_build/lib`. If a Verilator run dies with `Failed to preload Python
library`, symlink it: `ln -sfn ~/asic/oss-cad-suite/lib test/sim_build/lib`.
Also delete `test/sim_build/rtl` when switching `IHP_SRAM` on or off: the
build is only rebuilt on source timestamps, not on the changed define.

Note `test/Makefile` gates `-g2012` (Icarus) vs `--language 1800-2012`
(Verilator) on `$(SIM)` -- they're the same language-level flag under
different names, not a real behavioural difference between the two.

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

- **Line buffer** (`line_buffer.v`) holds **four** rows of 256 bytes, exactly filling
  the 1 kB address space with no spare left. Four rather than two is what lets the
  host free-run at a fixed byte rate between one and three rows ahead instead of
  handshaking every row.
- **Single-port discipline.** The renderer reads 2 of every 12 cycles to prefetch
  the next digit — only the two bytes holding the two segments the current
  scanline can show, not all four; `wr_grant = !lb_re` gives writes the other
  10. `we` and `re` must **never** be high in the same cycle — on the IHP macro that combination is
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
- **The generator draws a zone plate** (`zoneplate.v`), not the scrolling hex test
  pattern it started with. Two accumulators carry the motion — `ring_ph` for
  the ring phase, `t_src` for the source points' time — rather than products
  of the frame number, and the slow variation moves only their *rates*. That is
  the rule the attract mode is built on: a speed may change, a position may
  never jump.
- **Config port** (`config_port.v`): while `uio[7]` is low the strobes carry a
  config packet instead of pixels — Pmod type, preset cycling, and a palette
  curve — and `ui_in[6:1]` are read as live DIP switches. Byte layout, switch
  map and the `dip_live` host-takeover latch are in that file's header and
  `README.md`. The palette changes itself every 1024 frames (~17 s) with a
  64-frame fade through black either side of the change. The header's magic nibble (`0xA`) is what stops a floating strobe
  on a bare board flipping the Pmod. `formal/config_port.sby` proves the protocol
  on this module alone, switches included.
- **Palette** (`palette.v`) is three per-channel curves, not a table: two lines
  through a knee, slopes in eighths, clipped at 15 — 54 bits of state rather
  than the 192 flops a loadable 16x12 table would cost. It is a 2-stage
  pipeline, and the index is registered before it, so the syncs are delayed 4
  cycles to match; all of that was needed to get the iCE40 back to ~35 MHz
  (see FPGA timing below).

### Pacing is the load-bearing invariant

A digit row is 16 scanlines and 256 bytes, so `16 * 1056 / 256 = 66` pixel clocks per
byte, **exactly**. No remainder means a fixed-rate host tracks the raster
indefinitely. The host's whole head start is vertical blanking, 713 µs, and anything
spent before its first byte comes straight off it — the budget from vsync to first
byte is about **450 µs**, same as before: `resolution_discussion.md` section 11
found the delay-sweep tearing threshold itself didn't move when the resolution did,
only the margin above it shrank. This is not theoretical: exceeding it is what tore the
picture on hardware, and `make -C test delay-sweep` reproduces it in simulation.
See "If the picture tears" in `README.md`.

### Palettes are data, generated into RTL

`tools/palette_builder/presets.json` is the source of truth for the 8 built-in
palettes. `src/palette_presets.v` is **generated** from it (`--write-rtl`) —
never hand-edit it; `tools/test_palettes.py` fails if the two disagree.
`tools/segments.py`'s `curve_value()` is the bit-exact model of `palette.v`
(the cocotb suite checks the RTL against it over the presets and random
parameter words), and `config_packet()` is the host-side encoder.
`tools/attract_proto.py` plays the same role for the generator: `zoneplate_at()`
is the picture, and `chip_state()`/`chip_step()`/`chip_fade()` are the chip's
per-frame state simulated from reset. The cocotb suite checks the RTL against
all of them, and `palette_builder`'s `chip` effect previews them. The firmware
carries its own copy of that encoder because `mpremote run` can't import
`tools/`; `test_palettes.py` checks the copy against the original for every legal
curve.

### Geometry is written down twice

`src/multi_seg_monitor.v` and `tools/segments.py` both encode the segment
rectangles and the nibble ordering. If one changes the other must too, or the
round-trip test will say so. Nibble order within a digit word is fixed
low-to-high as `a, b, c, d, e, f, g, DP` — host software depends on it.

### Pinout

Output mode is chosen at reset: a config strap on `ui_in[3:0]`, sampled every
cycle `rst_n` is low and latched once it rises, then reverting to plain stream
data. `ui_in[0]` picks the physical Pmod, `ui_in[3:1]` one of 8 built-in
palettes (applied in both modes). A config packet (see Architecture) can
override both later; the strap stays because it makes the pins safe from the
first cycle out of reset — a Tiny VGA board must never see `uio` driven while
it waits for a packet. Both live in `src/config_port.v`.

`ui_in[0]` is strap-only and never live. `ui_in[6:1]` are *also* read live, but
only in generator mode: `[3:1]` the manual palette, `[4]` manual/auto, `[5]` and
`[6]` steady ring speed / steady drift. They are debounced over two frames and
abandoned the moment a valid config header arrives (`dip_live`), because the
RP2350 drives these pins to send one. `src/config_port.v` has the map.

**Digilent PmodVGA** (`ui_in[0]=0`, default), video spans `uo_out` *and* `uio`:

| | |
|---|---|
| `uo_out[3:0]` / `uo_out[7:4]` | R / B nibbles |
| `uio[3:0]` | G nibble |
| `uio[4]` / `uio[5]` | hsync / vsync |
| `uio[6]` / `uio[7]` | stream strobe / mode select (low = generator + config packets) |
| `ui_in[7:0]` | stream data (bits 3:0 double as the reset strap) |

`uio_oe` is `8'b0011_1111`.

**Tiny Tapeout VGA Pmod** (`ui_in[0]=1`), this chip's original output mode in
its very first commit, resurrected rather than designed fresh — video is
`uo_out` only (2 bits/channel, each core channel's top 2 bits), `uio[0:5]`
unused and `uio_oe` goes fully input in this mode. `uio[6]`/`uio[7]` are the
*same* pins as Digilent mode — Tiny VGA touches no `uio` pin at all, so
there's nothing to relocate and `firmware/seg_player.py`'s GPIO map doesn't
fork on the strap. Bit order reconstructed from commit `d7fee74`: `uo_out[7:0]
= {hsync, B0, G0, R0, vsync, B1, G1, R1}`.

`SPEC.md` section 7 still describes the pre-strap single-Pmod design and the
native 6-bit custom Pmod idea (never built); **the RTL, `info.yaml`, `tb.v`
and `firmware/seg_player.py` are the truth.** Moving these pins, the strap's
bit assignment or the config packet layout breaks all four at once (plus
`tools/segments.py` for the packet).

## Testing approach

Two layers, deliberately:

- **Property assertions** — margins clear, cell corners dark, no blank digit row,
  plausible lit fraction. These say the picture is legal.
- **Gold images** (`test/gold/`) — three deterministic captures compared pixel for
  pixel. These say it is the *same* picture, which is what catches a segment a pixel
  wide or a brightness code off by one. A mismatch writes `<name>_diff.png` with the
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
and TT precheck passes. `src/config.json` skips KLayout DRC, which is what the
IHP SRAM macro's own subcells (`RM_IHPSG13_1P_BITKIT_*`, `_BLDRV`, `_COLCTRL2`)
used to fail on — never this design's logic.

`info.yaml` says `tiles: "2x2"` (419.52 x 313.74 µm), down from 3x2 and 4x2 before
that. Every TT `Nx2` block is 313.74 µm tall, so shrinking only ever took width away
and the macro placement at `[42, 80]` `R90` has survived all three unchanged. `R90`
is not optional — the macro is 336.46 µm tall upright, which does not fit in
313.74 µm of die height. At 2x2 the macro is 38% of the die, so placement density and
routing congestion around it are the things to watch, not the coordinate.

Area budget, measured against the 126.7k µm² core. The last CI number is 48.7k µm²
of standard cells (run 35467760338, the zone plate without the attract
variations); with the macro and its 10 µm halo at a fixed 60.7k that is 86.4%.
The attract variations and DIP switches add 5.3k µm² synthesised (312 -> 357
flops), so expect ~91%. An IHP `dfrbpq_1` flop is 49 µm² — flops are the
expensive thing here, and placement adds about x1.15 over synthesised area for
new logic (x1.4 over the whole design).

**That is over budget** (80% comfortable, 85% with work). The cheapest lever is
not in the RTL: `FP_MACRO_HORIZONTAL_HALO`/`FP_MACRO_VERTICAL_HALO` are at
LibreLane's default of 10 µm and nothing in `src/config.json` sets them. The
halo ring around a 336.46 x 146.88 µm macro is 10.1k µm², 7.9 points of
utilisation; halving it to 5 gives back 5.1k, about 4 points. The risk is
congestion, not legality — the free area at 2x2 is already an L of narrow
bands and the design runs with `GRT_ALLOW_CONGESTION: 1`. Leave `PDN_*_HALO`
alone; that one keeps power straps off the macro's own M4 pins.

### FPGA timing

nextpnr's 40 MHz check fails on `main` and always has (36.7 MHz before the
curve palette, 33.6-35.7 MHz across seeds 0-3 after it); the board has been run
at 40 MHz with a ~35 MHz report without trouble, so treat that as the bar rather
than the pass/fail line. The critical path is the generator's `y_px` -> vsync
comparator -> `frame_start` -> zone-plate FSM enables. The attract variations
sit at 33.2-34.0 across the same seeds, about 0.9 MHz down.

Getting them there took three flops, and each one is worth knowing about
because the same trap is waiting for anything else hung off `frame_start` or
`frame_ctr`:

- `frame_wrap` is registered rather than wired from `frame_start`. Together,
  the vsync comparator and `config_port`'s preset table plus 54-bit palette
  load were 26.8 MHz.
- `frame_start_d` feeds `config_port`'s switch sampling, purely to keep that
  logic out of `frame_start`'s fanout. Worth ~0.4 MHz.
- `gen_fade` is registered. As a wire it put `frame_ctr`'s two comparators in
  front of the fade multiplier on the way to the line buffer: 27.5 MHz.

The fade multiply itself moved from in front of `lo`/`hi` to `data`, costing a
second multiplier: in front of them it landed at the end of the longest chain
in the design (DSP square, 21-bit sum, fold) and cost 5 MHz.

Note `make bitstream` stops at that nextpnr error before `icepack`, on `main`
too (checked locally with seed 0), so a flashable `.bin` needs nextpnr's
`--timing-allow-fail`.

## Conventions

- Comments explain *why*, especially where a choice looks arbitrary or wrong — the
  `A_MEN` divergence from the proven design, the doubled quotes in the test filter,
  the load-bearing cell gaps. Match that density; a bare restatement of the code is
  worse than nothing here.
- Don't add capability the FPGA has and the ASIC does not. The UP5K is a
  feature-parity verification target; its spare EBR and SPRAM go deliberately
  unused. Design single-port, and never assume initialised memory.
