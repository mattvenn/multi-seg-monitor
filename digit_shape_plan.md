# Branch `digit-shape`: the 4/5/4/4/2/2 glyph in RTL

## Starting cold

This file is the plan; it lives on the `digit-shape` branch and nowhere else.
`main` must stay untouched. Toolchain: `source ~/asic/oss-cad-suite/environment`.

The working tree has **uncommitted work that is part of this story** — the
palette builder's Digit tab, which is what produced these dimensions:

    M CLAUDE.md
    M tools/palette_builder/palette_builder.py
    M tools/palette_builder/test_palette_builder.py
    ?? tools/shapes.py          (new: the parametric 7-segment cell)

`tools/shapes.py` is the reference for everything below: `python3
tools/shapes.py` prints the cell, the grid, the band table and the cx
predicate for any settings, and `shapes.digit(thick_h=4, len_h=5, thick_v=4,
len_v=4, gap_x=2, gap_y=2)` is this glyph.

Decisions already made, so don't re-ask:
- zone plate source range keeps its cheap constants (see RTL section);
- the streaming path is done fully — firmware, testcard, delay sweep;
- if the ASIC build comes back over budget, apply the halo trim and push again.

## Context

The Digit tab work produced a glyph Matt wants to see on real hardware: fat
4 px bars and rails with short segments. He wants it on the FPGA tomorrow, with
`main` untouched, and the ASIC build checked by CI before then.

`digit(thick_h=4, len_h=5, thick_v=4, len_v=4, gap_x=2, gap_y=2)` derives:

| | chip today | this branch |
|---|---|---|
| body / cell | 10x14 in 12x16 | 13x20 in **15x22** |
| grid | 64 x 37 | **53 x 27** (1431 digits) |
| margins | 16 / 4 | **2 / 3** |
| row / frame bytes | 256 / 9472 | **212 / 5724** |
| host byte period | 66 clocks exactly | **5808/53 = 109.58** clocks, 365 kB/s |

Segment rectangles (cell coordinates, nibble order a,b,c,d,e,f,g,DP):
`a (4,8,0,3) b (9,12,4,7) c (9,12,12,15) d (4,8,16,19) e (0,3,12,15)
f (0,3,4,7) g (4,8,8,11) DP (13,13,16,19)`; bands
`0-3:(a,a) 4-7:(f,b) 8-11:(g,g) 12-15:(e,c) 16-19:(d,DP) 20-21:(-,-)`;
slot 1 when `cx in 9..14`.

Two things are genuinely new, not just renumbered: **cell_h 22 is not a power
of two**, so `cy`/`row` can no longer be a slice of `y_px`, and the **byte
period is fractional**, which the firmware's PIO setup currently assumes away.
Neither is a blocker; both need real edits.

Two more turned up when this plan was checked against the RTL, and both are
now done:

- **MARGIN_X 2 broke column 0's prefetch.** `fetch_en` fetched column 0 at
  `x_px` 0 and 1, and the fetch is two cycles deep (the line buffer's
  registered read, then `fetch_en_d`), so `next_digit` was only complete at
  `x_px` 3 — after `cur_digit <= next_digit` at `x_px == MARGIN_X-1 == 1`.
  The design has always needed `MARGIN_X >= 4` and nothing said so. Column 0
  now fetches in the last two cycles of horizontal blanking (`&x_px[10:1]`).
  Confirmed both ways: with the old `fetch_en`, `test_stream_frame` reports
  205 of 11448 segments wrong, all of them in column 0.
- **The pacing budget shrank.** Vertical blanking is a fixed 28512 clocks but
  a digit row grew from 16 scanlines to 22, so the host's head start fell from
  1.69 digit rows to 1.23. The delay sweep measures the new threshold at
  **~250 µs** (clean at 250, 14 segments wrong at 300) against ~450 on `main`.
  `CLAUDE.md` and `README.md` carry the measured table.

## Branching

The branch exists and this file is committed on it. The working tree still
holds the uncommitted Digit-tab work (`tools/shapes.py`, `palette_builder.py`,
its tests, CLAUDE.md) — commit that first, as its own commit, since it is what
produced these dimensions. The geometry change is a second commit.

## RTL

**`src/multi_seg_monitor.v`**
- Constants: `CELL_W 15`, `CELL_H 22`, `COLS 53`, `ROWS 27`, `MARGIN_X 2`,
  `MARGIN_Y 3`. `ROW_BYTES = COLS*4` = 212 — still under the line buffer's
  256-byte stride, so `{gen_buf, gen_ptr}` addressing and the four-row split
  are untouched. `cx` stays 4 bits (0..14), `col` 6 bits (0..52),
  `fetch_col` 6 bits (col+1 ≤ 53).
- **`cy`/`row` become registered counters.** Today `cy = y_rel[3:0]`,
  `row = y_rel[9:4]` only works because 16 is a power of two. Replace the
  `y_rel` subtract with a counter pair updated once a line, at
  `x_px == 11'd2047` — the last cycle of horizontal blanking, by which point
  `y_px` already holds the line about to start (VgaSyncGen advances it at
  `x_px == 799`). Update rule: reset both when `y_px == MARGIN_Y`, else
  `cy == CELL_H-1` → `cy <= 0, row <= row + 1`, else `cy <= cy + 1`. It must
  land before `x_px == 0` because the prefetch at `x_px < 2` already reads the
  y zones. `cy` is 5 bits, `row` stays 6; `render_buf = row[1:0]` unchanged.
- Column 0's prefetch moves into blanking; see "Starting cold" above.
- Zones: `xz_left cx<4`, `xz_mid 4..8`, `xz_right 9..12`, `xz_dp cx==13`
  (14 is the gap); `yz_top cy<4`, `yz_up 4..7`, `yz_mid 8..11`,
  `yz_low 12..15`, `yz_bot 16..19` (20,21 the gap). Slot mapping and
  `seg_hit` keep their structure — the five bands are the same five.

**`src/zoneplate.v`**
- Sample table: `a (6,1) b (10,5) c (10,13) d (6,17) e (1,13) f (1,5)
  g (6,9)`; `oy` widens to 5 bits (DP is never sampled, so the `default`
  branch still carries g).
- `sx_px = col*15 + ox` → `{col,4'b0} - col`; `sy_px = row*22 + oy` →
  `{row,4'b0} + {row,2'b0} + {row,1'b0} + oy` (max 591, still 10 bits).
- Source wander keeps `tr*3>>3` and `tr*37>>7` per your answer: the points
  roam 768x592 of the 795x594 grid, which is invisible and keeps the longest
  arithmetic chain as it is. Comment it as deliberate.

Unchanged: `line_buffer.v`, `config_port.v`, `VgaSyncGen.v`, the wrapper.
`stream_in.v` is already parameterised on ROW_BYTES/ROWS, but one of its formal
asserts (`s_byte < ROW_BYTES`) was dead code at 256 bytes a row and is live at
212 — an 8 bit register can power up above 212 — so it is now gated on
`f_reset_done` exactly as the `s_row` bound beside it already was.

## Software mirrors

- **`tools/segments.py`** — `COLS/ROWS/CELL_W/CELL_H/MARGIN_X/MARGIN_Y` and
  the `SEGMENTS` rectangles. Everything else in `tools/` derives from these.
- **`tools/attract_proto.py`** — `_sources()` scales by `GRID_W/GRID_H`, which
  would now follow the new grid and break the bit-exact match. Add module-level
  `SRC_W, SRC_H = 768, 592` and use those, documented as the RTL's fixed
  scaling (a no-op on main's numbers).
- **`tools/shapes.py`** — `DEFAULTS` becomes the six numbers, so `shapes.CHIP`
  still means "what the RTL has". Its existing test compares `CHIP` against
  both `segments.SEGMENTS` and `zoneplate.v`'s `{ox, oy}` table, which is what
  will catch a typo in any of the three.
- **`firmware/seg_player.py`** — `COLS/ROWS/CELL_H`; `CLOCKS_PER_BYTE` becomes
  the exact ratio (`CELL_H * H_TOTAL / ROW_BYTES`, a float) and
  `pio_freq = round(pixel_hz * 2 / CLOCKS_PER_BYTE)` in place of
  `pixel_hz // (CLOCKS_PER_BYTE // 2)`, keeping two PIO instructions a byte.
  The divider is 16.8 fixed point, so the residual error is a few clocks a
  frame, and vsync resyncs anyway.

## Tests

- `test/test_multi_seg.py` lines 30-33 hold the geometry; `BYTE_PERIOD`
  (line 351) floors to 109, i.e. the test host runs ~0.15 of a row ahead over
  a frame — inside the one-to-three-row window, worth a comment.
- The cell-corner offsets in `test_render_frame` were written out for a 12x16
  cell (`(0,15)`, `(1,14)`); they come off `segments.SEGMENTS` now, so they
  follow the glyph. `(0,15)` is segment e in this cell, which is how this was
  found.
- `tools/palette_builder/test_palette_builder.py` asserts the chip's byte
  period is 66 and parses `4'd` out of `zoneplate.v`'s `{ox, oy}` table; both
  move. `warnings()` loses the power-of-two message altogether — the RTL has
  the row counter now, so every cell height costs the same.
- The delay sweep steps by 50 µs up to 300 rather than 100 all the way: the
  threshold moved down into the range where 100 µs steps can't see it.
- Regenerate `test/testcard.seg` (`tools/testcard.sh`, ffmpeg is present) — it
  is a committed 9472-byte asset and the frame size changes.
- Regenerate all three gold images: `SIM=verilator make -C test gold`, then
  **look at them** before committing.
- Run the suite both ways: `make -C test` and `make -C test IHP_SRAM=1`
  (deleting `test/sim_build/rtl` between, per CLAUDE.md).

## Docs

`CLAUDE.md` (geometry, pacing and area paragraphs), `README.md` numbers, and
`info.yaml`'s "64x37" description. Say plainly at the top of the CLAUDE.md
geometry section that this branch carries a different glyph from `main`.

## FPGA and CI

- `make bitstream` locally for the Fmax number; the 40 MHz check already fails
  on `main` (~35 MHz is the bar), so build the flashable `.bin` with
  nextpnr's `--timing-allow-fail` and report where this glyph lands across a
  couple of seeds. The new counters replace a 10-bit subtract, so it should be
  close to neutral.
- Push the branch; `gds.yaml` and `test.yaml` both run on any push.
- If the ASIC build comes back over budget, add `FP_MACRO_HORIZONTAL_HALO` and
  `FP_MACRO_VERTICAL_HALO` of 5 to `src/config.json` and push again (your
  call, already made). Report the utilisation either way.

## Verification

    make -C test                      # icarus, CI parity
    make -C test IHP_SRAM=1           # the macro path
    cd tools && python3 test_palettes.py && python3 test_video2seg.py \
        && python3 palette_builder/test_palette_builder.py
    make formal                       # the zone/we-re proofs still hold
    make bitstream                    # Fmax, then --timing-allow-fail for a .bin
    git push -u origin digit-shape    # then watch gds + test

Then on the branch: the gold PNGs should show a 53x27 grid of fat digits with
a 2 px gap, and `python3 tools/shapes.py` should print exactly the table at the
top of this plan.
