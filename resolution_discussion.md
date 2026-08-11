# Resolution, grey depth, update rate and segment shape — design discussion

Working notes, 2026-08-08. Not a specification: `SPEC.md` is still the design of
record, and nothing here has been implemented or hardened. Numbers marked
**(verified)** were read out of the tree or the build logs; the rest are arithmetic
from the geometry, or estimates, and are labelled as such.

The question that started it: *make the digits bigger in pixels so mitred segment
ends have somewhere to show — which means fewer digits, or a higher resolution.*

---

## 1. The three equations everything falls out of

**Buffer capacity.** `multi_seg_monitor.v:241` builds the read address as
`{render_buf[1:0], fetch_col[5:0], fetch_byte[1:0]}` — 4 buffers × 64 digits ×
4 bytes = exactly the 1 kB macro **(verified)**. So

    cols × bytes_per_digit <= 256

This caps digit **count**, never digit **size**. Only the row being drawn is
buffered, so vertical resolution is essentially free.

**Pacing.** A digit row is `cell_h` scanlines and `cols × bytes_per_digit` bytes:

    clocks_per_byte = cell_h * H_total / (cols * bytes_per_digit)

Currently `16 * 832 / 208 = 64`, exact. Worth recording that **an integer is a
convenience, not a requirement** — the RP2350's DMA pacing timer is a 16/16
fractional divider off the same clock, so any rational ratio holds indefinitely.
Exactness of the *ratio* is what matters, not integrality.

**Legibility.** SPEC §1.1's mesh failure fixes a floor at roughly a 12 px cell
width with 2 px segments. Below that neighbouring digits merge and the grid stops
reading as digits.

---

## 2. Two things found in the tree along the way

### 2.1 Four of sixteen grey levels are being thrown away (verified)

`src/gamma.v` produces a 6-bit level; `tt_um_multi_seg_monitor.v:31` truncates it
with `level[5:2]` for the PmodVGA's 4-bit channels. Running the 16 table entries
through that truncation:

    idx    0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15
    6-bit  0 18 25 30 35 38 42 45 47 50 52 55 57 59 61 63
    4-bit  0  4  6  7  8  9 10 11 11 12 13 13 14 14 15 15
                                 ^^       ^^    ^^    ^^

Index pairs (7,8), (10,11), (12,13) and (14,15) collide. **12 distinct greys reach
the panel, not 16**, and all four losses are at the bright end. The table is
computed for a 6-bit DAC that is not connected.

Fixing this needs no format change, no pin change and no Pmod: recompute the
entries for the 4-bit output path in `gamma.v` and `tools/segments.py` together,
then rebuild the gold images. It is the cheapest item on the whole "more greys"
list and it is upstream of every other grey decision — right now stored depth is
*not* what is costing levels.

### 2.2 The FPGA is the speed ceiling, not the ASIC (verified)

`build/02-nextpnr.log:310` — **Fmax 39.14 MHz on the UP5K**, passing at 31.50.

That is under the 40 MHz an 800×600@60 mode needs, so the prototype fails before
the ASIC gets a vote. The ASIC slack is *unknown* — `src/config.json:21` sets
`CLOCK_PERIOD` to 31.75 ns and CI reports no violations, but the flow runs in CI so
no margin figure was available locally. **Checkable, not arguable:** shorten
`CLOCK_PERIOD` and rerun the hardening.

---

## 3. What actually limits resolution

Only the clock. In order of which bites first:

1. **The UP5K, at 39.14 MHz.** CLAUDE.md's rule is *don't use capability the ASIC
   lacks*, which constrains the design, not the choice of board. An ECP5 keeps
   yosys/nextpnr and keeps the single-port, uninitialised-memory discipline intact
   while removing a ceiling that is an artifact of a slow fabric. Changing the
   verification target looks cheaper than pipelining to satisfy it.
2. **TT's shared IO mux and pad ring**, plus video pins toggling through pads →
   demoboard → Pmod header. This is the one that cannot be pipelined away and the
   number was not available here. **Get it first — it gates everything else.**
3. **The ASIC logic.** 687 cells on 130 nm sg13g2, where 15.4 ns is a very long
   period; the macro is rated ≥100 MHz (SPEC §6). Expected to be the easy half,
   but unmeasured.
4. **Clock generation.** 65 MHz is 130/2 out of the RP2350; 40 MHz is 120/3. The
   pixel clock and the byte pacer must stay derived from the same source or the
   exact-ratio property dies.

**What does not limit it,** which is counterintuitive: bigger digits mean fewer
digits, so bandwidth *falls*, the buffer gets *slacker*, and `clocks_per_byte`
*rises*. Area is a few counter bits. 800×600@60 and 1024×768@60 are better
supported by monitors than 640×480@72 is.

---

## 4. 1024 pixels wide is where the current macro says to stop

64 columns × a 16 px cell = 1024. To use more than 64 columns legibly you need
≥ 128 × 12 = 1536 px of width, which is ~120 MHz.

So past 1024 horizontal pixels, extra width buys **zero additional digits** — only
fatter ones — unless memory grows too. 1280×1024 costs 108 MHz for nothing the
current buffer can feed.

That makes **1024×768@60, 65 MHz** the natural target (H total 1344, V total 806,
1,083,264 clocks/frame, 60.00 Hz), with ~35% margin under the ~100 MHz ceiling
rather than none.

### Design points on that one clock

| Cell | Grid | Digits | Seg thickness | B/row | clk/byte | Stream rate |
|---|---|---|---|---|---|---|
| 16×16 | 64×48 | 3072 | 2 px | 256 | 84 | 737 kB/s (5.9 Mbit/s) |
| 16×24 | 64×32 | 2048 | 3 px | 256 | 126 | 492 kB/s |
| 24×32 | 42×24 | 1008 | 4 px | 168 | 256 | 242 kB/s |

All three pace exactly and all tile the raster exactly (16×48 = 768; 24×32 = 768;
32×24 = 768; the 24 px cell leaves a 16 px horizontal margin, the others none).

The 16×16 row sits precisely on the 256 B buffer wall — the maximum-digit endpoint
of this architecture — and its 5.9 Mbit/s is ~74% of practical USB full speed. The
24×32 row is the mitring point: 4× today's pixels per digit, and *half* today's
traffic.

For reference, the intermediate step considered and set aside: **800×600@60,
40 MHz, 16×20 cell → 48×30 = 1440 digits**, 192 B/row, `20 × 1056 / 192 = 110`
exact, 346 kB/s. Nearly today's digit count with 67% more pixels each — but it is
on the wrong side of the UP5K's 39.14 MHz.

---

## 5. Mitring does not actually need a resolution change

A chamfer on a 2 px segment end removes one pixel; SPEC §1.1 already concluded it
has nowhere to show. It begins to read at ~4 px thickness, i.e. a 24×32 cell —
which **640×480 already provides** through the existing scale register: 26×15 =
390 digits, 104 B/row, `32 × 832 / 104 = 256` exact, 114 kB/s.

**The blocker is that scale is decimation.** At 2× the renderer decides membership
in the 12×16 cell and pixel-doubles, so a mitre drawn in that geometry comes out as
a 2 px staircase — chamfering nothing. Deriving segment membership from the
*un-decimated* pixel coordinate is what makes any shape work possible, and it costs
a few comparator bits, no clock, no bandwidth, no pins.

This is worth doing at any clock, and it answers whether mitring earns its keep
*before* spending a resolution jump to find out.

Ranked by cost, for the record:

- **Chamfer / taper / rounded ends** — constants only, once the above lands.
- **14/16-segment starburst** — real alphanumerics; diagonals are cheap (the
  `cx±cy` adders SPEC §1.1 already budgets for mitring), but 14 × 4 bits =
  7 B/digit caps you at 36 columns, and diagonals do not read below ~16×20 cells.
  A different chip, not a variant.

**Prerequisite for any shape sweep:** the rectangles live in both
`src/multi_seg_monitor.v` and `tools/segments.py`. Generate both from one table
first, or every variant is two hand-edits plus a gold rebuild.

---

## 6. A second SRAM block

It does **not** move the column wall, because at ≤100 MHz the clock caps columns
at 64 before memory does (§4). Three places to spend it instead, ranked:

### 6.1 More buffers — cheapest, and retires a failure already seen

8 buffers × 256 B = 2048 B exactly. Host lead goes from 1–3 rows to 1–7, adding
~1.69 ms (4 × 422.6 µs) to a vsync budget currently ~450 µs.

That is the margin that tore the picture on hardware. No format change, no host
change, no pin change, and `make -C test delay-sweep` already exists to measure the
improvement.

### 6.2 More depth — real, but downstream of the Pmod

6 bits/segment = 6 B/digit; at 64 cols that is 384 B/row × 4 buffers = 1536 B,
comfortable in 2 kB. The clean version: 6-bit storage matches the gamma output
exactly, so the LUT leaves the chip and becomes a host-side table, retunable
without a respin.

Two caveats. **PmodVGA still takes 4 bits per channel**, so 6-bit storage displays
nothing new until the pins change — the output pins, not the format, are the gate
on grey (and see §2.1, which is free today). And at 3072 digits, 6 B/digit at 60 Hz
is 8.85 Mbit/s, *over* practical USB full speed; 2048 digits stays at 5.9 Mbit/s.

Depth against the current single macro, for comparison: 4 B/digit → 64 cols,
5 B → 51 cols, 6 B → 42 cols. 5-bit is byte-aligned per digit but straddles
segments across byte boundaries — a messier host and write path for one bit.

### 6.3 Contention relief — a side effect worth having

Map even buffers to one macro and odd to the other: a read of buffer N collides
with a write only when the host leads by exactly 2 rows, since the other leads land
in the opposite macro. That relaxes the 4-reads-in-12 discipline most of the time.
Keep `wr_grant = !lb_re` as the fallback for the colliding case rather than
removing it.

This stays portable — two single-port macros with disjoint address ranges is
exactly the gf180 plan in SPEC §8 (4 × `sram256x8`), not a dual-port shortcut that
cannot follow to silicon.

### 6.4 Area (arithmetic, not a floorplan)

Each macro is 336.46 × 146.88 µm, and R90 is not optional (SPEC §8.2).

- Side by side: 672.92 µm wide — over the 636.96 of a 3×2, comfortable in a **4×2**
  (854.40 × 313.74), leaving ~181 µm of width and ~167 µm of height for logic.
- Stacked: 293.76 µm of 313.74 µm of die height — ~20 µm, too tight once power
  straps land.

So 4×2, which is the geometry SPEC §8.2 and `src/config.json` still reason about
anyway.

---

## 7. Update speed: the axis with least to gain

Recorded so it is not reopened. Refresh is already 72.8 Hz and chip latency is
zero — it races the beam. Per-digit updates are already O(1) in RP2350 SRAM. The
residual latency is USB (~1 ms) plus one frame of host double-buffering, ~15–30 ms,
and the only way to cut it is to DMA out of the USB buffer while chasing the beam —
which spends the ~450 µs margin that tore the picture.

If "faster" means more bytes per second, it is the same conversation as §4: chip
ingest scales with geometry automatically, and the ceiling is USB full speed at
~1 MB/s practical (45% utilised today).

§6.1 is the exception — it does not make updates faster, but it makes the timing
that governs them far harder to miss.

---

## 8. Suggested order

1. **Get TT's real IO/pad clock ceiling.** Gates everything in §3–§4 and cannot be
   pipelined away.
2. **Fix the gamma truncation (§2.1).** Free, isolated, and upstream of every grey
   decision.
3. **Native-resolution scaling (§5).** Unlocks mitring at 390 digits on the clock
   you already have, and tells you whether shape work is worth a resolution jump.
4. **Second macro spent on buffers (§6.1)**, if the tile budget allows.
5. Only then resolution (§4) and stored depth (§6.2) — the first gated on the
   FPGA target, the second on the output Pmod.

## 9. Open questions

- TT IO mux / pad ring maximum clock, and the video pins' usable edge rate.
- ASIC slack at 31.75 ns — what is the real margin?
- Move the FPGA target off the UP5K, or pipeline to fit it?
- Does mitring actually look better? Undecided since SPEC's first draft, and §5
  makes it answerable cheaply.
- Custom 6-bit ladder Pmod: SPEC §5.1's pinout assumes `uo_out[5:0]` = intensity,
  which **no longer exists** — video now spans `uo_out` *and* `uio[3:0]`, and
  `uio[7:6]` are stream control. Six intensity + two sync does fit `uo_out` alone,
  freeing `uio[5:0]`, but that moves pins in the RTL, `info.yaml`, `tb.v` and
  `seg_player.py` together.

## 10. Blast radius, if any of this is adopted

Any geometry or timing change moves, together: `VgaSyncGen.v` constants (losing
their "proven on ttihp0p4 silicon" provenance), `CELL_*`/`COLS`/`ROWS`/`ROW_BYTES`
in `multi_seg_monitor.v`, the pacing constant in `firmware/seg_player.py`, the
segment table in `tools/segments.py`, `tb.v`, and all three gold images. Rebuild
gold with `make -C test gold` and **look at what it produces before committing.**

## 11. 800x600 mode: implemented and confirmed on hardware (2026-08-11)

The §4 candidate is built, FPGA-only, on branch `800x600-mode` — full design in
`docs/superpowers/specs/2026-08-11-800x600-mode-design.md`. Notes here are what
that process turned up that changes or sharpens the above.

**Digit geometry: kept, not redesigned.** The question that opened this whole
document was making digits bigger so mitred ends have somewhere to show. Two
16x20 redesigns were mocked up at real pixel scale and compared against today's
digit side by side (a scaled-up 2px pen, and a bolder ~3px pen) — neither read
better by eye than the current 12x16 digit. So this mode is **more digits, not
bigger ones**: 2368 (64x37) against today's 1560, geometry byte-for-byte
unchanged. The mitring question §5 raised is still open and still wants its own
native-resolution-scaling fix independent of any resolution change.

**The Fmax question resolved empirically, in the FPGA's favour.** nextpnr's
post-route signoff on the unmodified 640x480 logic was 39.58 MHz against a 40.00
MHz target — a ~1% miss, essentially the same number §2.2 recorded (39.14 MHz).
Built anyway and flashed: the internal generator runs clean on the actual UP5K
at 40 MHz. nextpnr-ice40's timing closure was the conservative one here, not the
silicon — worth remembering before spending effort working around a *reported*
ceiling next time.

**A real RTL subtlety `CELL_H` being a power of two used to hide.** `cy`/`row`
were a plain bit-slice of `y_px` (`y_px[3:0]`, `y_px[8:4]`) — free, but only
correct because the old mode's vertical margin was exactly zero (30*16=480).
800x600 has a genuine, unavoidable 4px vertical margin (37*16=592 of 600, since
600 doesn't divide by 16), and slicing raw `y_px` under a nonzero margin puts
row 0 mid-cell. Fixed by offsetting first (`y_rel = y_px - MARGIN_Y`) and
slicing that — no clocked counter needed, since `CELL_H` is still a power of
two, but the *offset* is no longer optional the way it was at 640x480. Any
future mode where `ROWS * CELL_H != V_ACTIVE` needs this same offset; one where
`CELL_H` itself isn't a power of two would need the full counter `cx`/`col`
already uses.

**The vsync-to-first-byte margin got tighter, even though the tearing threshold
didn't move.** `make -C test delay-sweep` at the new timing: clean through 400 us
of delay, tearing from 500 us -- the same absolute threshold the 640x480 mode
had. But the *budget* shrank with it, from 819 us to 713 us, so the margin above
that threshold is what actually got smaller (roughly 319 us of slack before,
213-313 us now, depending which side of 500 us you compare against). The
`hard=True` vsync dispatch fix (~80 us, README "If the picture tears") stays
comfortably inside either budget, so nothing needs to change today -- but a
future regression back toward soft-IRQ dispatch latency (500-600 us, the
original failure) would have noticeably less room to hide in before tearing at
this resolution than it did at 640x480.

**Practical fallout for video, not just RTL.** Frame size grew with `ROW_BYTES`
(208 to 256 bytes/row), so a frame is 9472 bytes against 6240 before, +52%. Flash
budget on the demoboard didn't grow to match, so the same clip fits proportionally
less duration at a given fps than it used to — worth remembering when reusing old
`--fps` values from before this change. Existing `.seg` files predate the frame
size change and are silently the wrong shape now, not just a different picture:
they need regenerating from source, not copying over.
