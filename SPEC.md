# Multi Segment Monitor — Design Specification

Draft, 2026-08-06. Derived from design discussion; open items marked **OPEN**.

An ASIC that simulates a large array of 7-segment displays on a VGA monitor, built
to the Tiny Tapeout standard. Segment data is streamed in from one of three sources
and rendered by racing the beam — the chip holds no frame.

---

## 1. Display geometry

Fixed at synthesis time.

| Parameter | Value |
|---|---|
| Resolution | 640×480 @ 72 Hz (31.5 MHz pixel clock) |
| Digit cell | 12 × 16 px |
| Grid | 52 columns × 30 rows |
| Digits | 1,560 |
| Segments | 12,480 (8/digit incl. DP) |
| Horizontal margin | 16 px total (8 each side) — 52 × 12 = 624 |
| Vertical margin | 0 — 30 × 16 = 480 exactly |

### 1.1 Segment layout within a cell

2 px segment thickness. The digit body is 10×14 inside the 12×16 cell; the spare
column and two spare rows are gaps. Coordinates are relative to the cell origin.

| Segment | x | y |
|---|---|---|
| a (top) | 2–7 | 0–1 |
| f (upper left) | 0–1 | 2–5 |
| b (upper right) | 8–9 | 2–5 |
| g (middle) | 2–7 | 6–7 |
| e (lower left) | 0–1 | 8–11 |
| c (lower right) | 8–9 | 8–11 |
| d (bottom) | 2–7 | 12–13 |
| DP | 10 | 12–13 |
| *(gap)* | 11 | 14–15 |

**The gaps are load-bearing, not cosmetic.** The first RTL revision filled the whole
cell, so each digit's right rail merged into its neighbour's left rail and each row's
bottom bar merged into the next row's top bar. The grid rendered as a mesh and was
completely illegible. Shrinking the body to 10×14 fixed it; see `test/frame.png`.

The decimal point sits in the spare column, which is where a real display puts it,
and is 1 px wide so it stays clear of the next cell.

**OPEN** — intra-digit gaps, inter-digit gaps, and mitred vs square segment ends.
All are synthesis constants: settle appearance in the playground, then sweep
variants through synthesis for area. Mitring costs roughly 100 cells (two shared
adders producing `cx+cy` and `cx−cy`, then constant comparisons), so it is unlikely
to be the deciding factor.

### 1.2 Scale register

Runtime power-of-2 scale, implemented as a mux selecting which bits of the pixel
counter feed cell-index vs cell-position. Costs ~40 cells.

| Scale | Cell | Grid | Digits |
|---|---|---|---|
| 1× | 12×16 | 52×30 | 1,560 |
| 2× | 24×32 | 26×15 | 390 |
| 4× | 48×64 | 13×7 | 91 |
| 8× | 96×128 | 6×3 | 18 |

Scaling is by decimation, so large digits are drawn in blocky pixel groups. This is
accepted as a deliberate retro look. 4× and 8× exist to make the internal clock mode
legible.

---

## 2. Data format

Arbitrary segment patterns — no BCD, no hardware digit decode in the main datapath.

- **4 bits of intensity per segment.** Intensity 0 means off; there is no separate
  on/off bit.
- **8 segments per digit** (a, b, c, d, e, f, g, DP) = **32 bits = 4 bytes per digit**.
- Byte-aligned end to end: 2 segments per byte, 4 bytes per digit, 208 bytes per row.

| Quantity | Value |
|---|---|
| Bytes per digit | 4 |
| Bytes per digit-row | 208 |
| Bytes per frame | 6,240 (6.24 kB) |

Nibble ordering within the 32-bit word, low to high: **a, b, c, d, e, f, g, DP**.
Byte *k* therefore carries segment 2*k* in its low nibble and 2*k*+1 in its high
nibble. This is fixed — host software and flash images depend on it.

### 2.1 Gamma

Stored intensity is an index into a 16-entry gamma LUT producing the 6-bit output
level. Perception is logarithmic; 16 gamma-spaced steps read as smooth where 16
linear steps band visibly at the low end.

Implemented as **synthesised logic, not memory**, on both ASIC and FPGA, to avoid
divergence between platforms. ~100 cells.

**OPEN** — 4-bit is chosen for byte alignment. If the playground shows visible
banding, 5- or 6-bit remains affordable (see §6) at the cost of alignment.

---

## 3. Architecture

The chip races the beam. It holds **no framebuffer** — only the digit-row currently
being drawn.

```
  source mux ──> line buffer (double) ──> segment renderer ──> gamma ──> 6-bit out
       │                                        ▲
  internal gen                            cell coords
  QSPI flash                              from VGA timing
  RP2350
```

### 3.1 Line buffer

| Parameter | Value |
|---|---|
| One digit-row | 208 B (1,664 bits) |
| Double-buffered | 416 B |
| Load window | one digit-row = 16 scanlines = 423 µs |
| Required load rate | **3.9 Mbit/s** |

Double-buffered: the renderer reads one half while the source fills the other, swapping
at the digit-row boundary. Single-buffering would require reloading inside one blank
scanline (63 Mbit/s), which only QSPI could sustain and with no margin.

The line buffer is what keeps display geometry inside the chip. Sources send *digits*,
never scanlines, so host software and flash images stay independent of cell size,
segment layout and scale factor.

### 3.2 Renderer

Per pixel, derive within-cell coordinates `cx, cy` and decode a small set of zone
signals:

- `cy` ∈ {top bar, mid bar, bottom bar, upper half, lower half}
- `cx` ∈ {left rail, right rail, horizontal span}

Each segment is one AND of two zones. The selected segment's 4-bit intensity indexes
the gamma LUT. Roughly 8 constant range comparators, 7 ANDs, one 8-way select.

---

## 4. Data sources

One always-streaming input behind a source mux. All three feed the same line-buffer
port; they are modes, not separate designs.

| Source | Use | Notes |
|---|---|---|
| Internal generator | standalone, bring-up | clock / counter, no external data |
| QSPI flash | standalone installation | no host required |
| RP2350 | host-driven, live video | streams from its own SRAM |

### 4.1 Internal generator

Contains the only 7-segment decode ROM in the design (~16 entries × 7 bits, ~60 cells).
Runs at power-on with no external data, so the chip produces a picture even if the
stream interface is broken — the primary silicon bring-up safety net.

**OPEN** — scope: clock only, or also a free-running counter and a number taken from
the digital inputs.

### 4.2 QSPI flash

Follows the approach proven in silicon by `tt_um_MichaelBell_rle_vga` (ttsky25a,
single tile): W25Q128JV or compatible, h6B Fast Read Quad Output.

Capacity at 6.24 kB/frame on a 16 MB part: **~2,560 frames ≈ 35 s at 72 Hz,
uncompressed.** RLE would extend this considerably.

### 4.3 RP2350

Holds the frame in its own SRAM (520 kB available; a frame is 6.24 kB) and re-streams
it every frame via PIO + DMA. From the host's perspective this restores "write once,
it persists" — the persistence lives in the MCU rather than the chip.

**Raster sync is free.** The demoboard RP2350 is wired to all TT IO including
`uo_out`, so it can observe hsync and vsync directly. No back-channel pin is needed.

Live video from a PC over USB full-speed: 454 kB/s against ~1 MB/s practical, **45%
utilisation.** USB frame jitter is absorbed by double-buffering in RP2350 SRAM.

### 4.4 Stream format

**OPEN.** Word layout to be defined. Recommendation: carry mode, colour and
brightness control **in-band** rather than on config pins — pins are scarce, and
in-band control lets the source change parameters per frame.

---

## 5. Output

Digital only. On-chip analog DACs were considered and rejected (§10).

| Signal | Width |
|---|---|
| Intensity | 6 bits |
| hsync, vsync | 1 each |

All eight signals fit a single 8-pin Pmod.

### 5.1 Custom Pmod

Colour is a build-time property of the Pmod, not a runtime control.

- **Three independent R-2R ladders**, all driven from the same 6 intensity pins.
- Per-channel jumper connects each VGA channel either to its ladder output or to
  ground. R, G, B or any combination; all three gives white.
- Three ladders rather than one shared ladder is **required**: a single ladder feeding
  jumpered channels sees 75 Ω with one channel closed and 25 Ω with three, making
  white roughly 3× dimmer. Independent ladders keep the load constant.
- ~36 resistors, or three SIL networks.

At 6 bits an LSB is 1.6% of full scale, so 1% resistors are marginal for guaranteed
monotonicity. The occasional non-monotonic step will not be visible in this
application; 0.1% parts are not worth the cost.

---

## 6. Timing and bandwidth

640×480 @ 72 Hz, 31.5 MHz pixel clock. Constants match `VgaSyncGen.v` as reused from
the `ttihp0p4-vga-clock` project, so the timing is already proven on hardware.

| Parameter | Value |
|---|---|
| H total | 832 (640 + 24 fp + 40 sync + 128 bp) |
| V total | 520 (480 + 9 fp + 3 sync + 28 bp) |
| Line period | 26.41 µs |
| Frame period | 13.73 ms (72.8 Hz) |
| Vertical blanking | 1.06 ms (40 lines) |
| Digit-row period | 422.6 µs (16 lines) |

### 6.1 Budgets

| Path | Required | Available | Utilisation |
|---|---|---|---|
| Line buffer fill | 3.9 Mbit/s | — | — |
| QSPI flash | 3.9 Mbit/s | ~96 Mbit/s | 4% |
| USB full-speed | 3.6 Mbit/s | ~8 Mbit/s | 45% |
| Line buffer read | ~2.6 MHz | macro ≥100 MHz | 3% |

Bandwidth is comfortable on every path. Intensity depth could rise to 5- or 6-bit
(4.9 / 5.9 Mbit/s) without stressing any interface; only byte alignment argues
against it.

---

## 7. Pin assignment

`uo_out` is fully committed. The remaining banks depend on an open decision.

**Native mode** — custom 6-bit Pmod (§5.1):

| Bank | Assignment |
|---|---|
| `uo_out[5:0]` | intensity[5:0] |
| `uo_out[6]` | hsync |
| `uo_out[7]` | vsync |

**Prototype mode** — Tiny VGA Pmod, used for FPGA bring-up until the custom Pmod
exists. The 6-bit gamma output is truncated to 2 bits and replicated across all three
channels to give grey:

| Bank | Assignment |
|---|---|
| `uo_out[2:0]` | R1, G1, B1 |
| `uo_out[3]` | vsync |
| `uo_out[6:4]` | R0, G0, B0 |
| `uo_out[7]` | hsync |

Note this yields **4 intensity levels, not 64.** The prototype can validate geometry,
streaming, timing and protocol, but *not* the smooth-fading goal that motivated
per-segment brightness. That requires the custom Pmod.

**OPEN — D6, stream pin scheme.** Two options:

- **A — separate push port.** `ui_in[5:0]` = data[3:0] + clk + valid. Flash keeps
  `uio[5:0]`. Simple PIO program. Leaves 2 `ui_in` + 2 `uio` for config.
- **B — shared wires.** RP2350 holds flash CS high and drives the same `uio[5:0]`
  with the simpler protocol; a mode bit selects which the chip speaks. Frees all of
  `ui_in` for config, at the cost of CS contention management.

B is preferred for pin comfort.

---

## 8. Memory

Requirement is **416 B**, which every candidate PDK can satisfy. Foundry choice is
therefore not an architectural constraint — select the shuttle on schedule and cost.

| PDK | Candidate | Notes |
|---|---|---|
| IHP sg13g2 | `RM_IHPSG13_1P_1024x8` | 1 kB, fits 2×2 tiles, silicon-proven (ttihp0p2) |
| gf180 | `sram256x8` × 2 | 256 B each |
| sky130 | OpenRAM / Sylvain's register file | register file is the proven option |
| — | flip-flops | ~4.8 tiles single-buffered; comparable area to a macro but 208 B instead of 1 kB |

A 1 kB macro is the natural fit: it costs about the same area as a flop-based single
buffer while providing double-buffering plus ~3 digit-rows of jitter slack.

### 8.1 Portability rules

The memory is a swappable wrapper targeting the **narrowest common denominator**:
single-port, byte-wide, 1-cycle read latency. This maps to the IHP macro, gf180,
iCE40 EBR and iCE40 SPRAM alike. Design to single-port — dual-port designs cannot
follow to the ASIC.

Two rules prevent the FPGA passing where silicon would fail:

1. **Never read and write the same address in the same cycle.** iCE40 EBR and SPRAM
   have defined read-during-write behaviour; ASIC macros differ. The double-buffered
   design satisfies this by construction — assert it in the testbench rather than
   relying on it.
2. **Never assume initialised memory.** EBR can be initialised from the bitstream;
   ASIC SRAM powers up undefined. Clear the buffer explicitly at reset.

---

## 9. FPGA target

The iCE40 UP5K is a **feature-parity verification target**, not a superset. Its extra
capacity goes deliberately unused so the prototype stays faithful.

| Resource | UP5K | Used |
|---|---|---|
| Logic cells | 5,280 | ~500–1,000 |
| EBR | 30 × 4 kbit (15 kB) | 416 B — one EBR |
| SPRAM | 4 × 32 kB (128 kB) | unused |
| PLL | 1 | 12 MHz → ~25 MHz |

The ASIC is the tighter target throughout. Open toolchain (yosys / nextpnr /
icestorm) on iCEBreaker or UPduino allows the real RTL to be run against a real
monitor, real QSPI flash and a real RP2350 before tapeout.

---

## 10. Area budget

| Block | Estimate (cells) |
|---|---|
| VGA timing | 80 |
| Coordinate + scale decode | 120 |
| Segment zone decode (+ mitre) | 200 |
| Gamma LUT | 100 |
| Stream interface FSM | 400 |
| Memory controller / arbiter | 200 |
| Internal generator (incl. 7-seg ROM) | 200 |
| Glue | 200 |
| **Logic total** | **~1,500 ≈ 1.5 tiles** |
| Memory macro | ~4 tiles (2×2) |
| **Total** | **~6 tiles** |

Target is ≤8 tiles; up to 4 tiles high is available.

---

## 11. Verification and bring-up

Order matters — each stage shares the datapath with the next, so a failure isolates
cleanly.

1. **Internal generator.** Proves VGA timing and the renderer with no external
   dependency. Produces a picture at power-on regardless of interface state.
2. **QSPI flash.** Proves the streaming path standalone, along a route already
   proven in TT silicon.
3. **RP2350 push.** Adds host-driven and live video.

Below that: cocotb for RTL, VGA playground for visual constants, UP5K as the final
pre-tapeout verification platform.

Power-on behaviour: the internal generator runs by default, so the chip is never
blank on power-up and never depends on an external source to prove itself. This is
implemented as of stage 1.

---

## 12. Open decisions

| # | Decision | Blocks |
|---|---|---|
| D6 | Stream pin scheme — separate port (A) or shared wires (B) | RTL interface |
| — | Stream word format; in-band vs pin control | host + flash format |
| — | Internal generator: keep clock mode, or test pattern only | RTL |
| — | Mitred vs square segment ends | synthesis sweep |
| — | Intensity depth if 4-bit bands visibly | format, alignment |

Settled: foundry (D1) is dissolved by the 416 B requirement; D2 (memory macro) and
D3 (line buffer) are decided; segment layout, nibble ordering, pixel clock and
power-on behaviour are all fixed by the stage 1 implementation.

Mitring cannot be judged on the prototype — at 1× the cell is 12×16 with 2 px
segments, and a chamfer has nowhere to show. It only reads at 4× and 8×, so leave it
until clock mode exists.

---

## 13. Rejected alternatives

Recorded so the reasoning is not relitigated.

**On-chip framebuffer.** A frame is 6.24 kB. Even a 1-bit-per-segment frame is
1.56 kB, exceeding a 1 kB macro. Holding a full-spec frame needs ~2× 4 kB macros and
lands at 16–32 tiles — a 3× bigger chip. Fitting a framebuffer in 8 tiles would mean
dropping to 256 digits or losing per-segment brightness. Persistence is instead
provided by the RP2350 re-streaming from its own SRAM, and standalone operation by
flash and internal-generator modes. Streaming also self-heals: corruption lasts one
frame (16 ms) rather than persisting until rewritten.

**No line buffer (per-scanline streaming).** Bandwidth is affordable — 20 Mbit/s
sending only the 2–3 segments present on each scanline, 52 Mbit/s naive — but it
forces the source to know the segment layout. That makes host software and flash
images layout-specific, breaks the scale register (each scale needs a different
scanline→segment mapping), and multiplies host reads by 16×. The ~4 tiles saved cost
the clean host contract and format portability.

**On-chip analog output.** Mono analog needs only 3 pins and TT's analog pins are
separate from `uo_out`, so it was attractive. Rejected for risk: analog design,
75 Ω output buffers, spice verification instead of cocotb, and a less mature flow —
with the failure mode being no display at all. An R-2R ladder on the Pmod is the same
DAC in passive components, fixable with a soldering iron.

**Runtime colour control.** Eight output pins cannot carry both fine gradation and
per-pixel colour. Every inspiration project is a single-colour LED array, so
monochrome with fine gradation is the more authentic look; colour moves to a Pmod
jumper. This supersedes the "Colour" runtime control listed in the README.

**BCD data with hardware digit decode.** Halves storage but restricts output to 0–F.
Arbitrary patterns are a core requirement.

**Per-digit rather than per-segment brightness.** Cheaper (12 bits/digit vs 32) but
loses the grayscale-from-video capability that the reference installation relies on.

**Gap-row reload trick.** Rendering the last scanline of each digit row blank to free
the buffer for reload. Saved a buffer's worth of flops, but at 32 bits/digit the
reload needs 52 Mbit/s inside one scanline — 54% of QSPI with no margin, and
unusable from simpler interfaces. Made unnecessary by double-buffering in a macro.

---

## 14. Reference

- Tiny Tapeout VGA Pmod — https://tinytapeout.com/specs/pinouts/#vga-output
- VGA playground — https://vga-playground.com/
- ETR demoboard — https://tinytapeout.com/guides/get-started-demoboard-etr/
- `tt_um_MichaelBell_rle_vga` — QSPI flash video streaming, 1 tile, silicon-proven
  https://tinytapeout.com/chips/ttsky25a/tt_um_MichaelBell_rle_vga
- Sea of Segments — 1,536 digits, 5-bit grayscale, PocketBeagle + PRU, no framebuffer
  in the display. Closest real-world comparable. https://willga.llia.io/sea-of-segments/build/
- `SRAM.csv` — survey of SRAM use across TT shuttles
