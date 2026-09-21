# Remove the decimal point

Date: 2026-09-21. Branch: `digit-shape`. Status: design approved in chat, spec awaiting review.

## Goal

The digit has seven segments, a-g. The decimal point is gone from the picture
the chip draws and from every model, converter and test that knew about it.

The stream format does **not** change. A digit stays four bytes, low nibble
first, `a, b, c, d, e, f, g, <reserved>`. Byte 3's high nibble is reserved: the
chip ignores it, the generator and converter write it as 0, and a host may put
anything there. So existing 53x27 `.seg` files still play, rows stay 212 bytes,
pacing stays ~109.58 pixel clocks per byte, and the line buffer keeps its
256-byte stride.

## Why

- On this glyph DP is a single pixel column (`cx == 13`) between a digit's right
  rail and the next digit's left rail. Lit, it reads as a stray dot at the cell
  edge rather than as picture.
- Nothing in the picture wants it: the zone plate never sampled it and the
  converter now leaves it dark, so it has been dead weight in the renderer.
- On hardware, faint dots at the DP position appeared under streaming even from a
  file whose DP nibbles were all zero. RTL simulation draws that file with no
  DP pixel lit, so the cause is not in the RTL. Removing DP from the renderer
  removes those pixels regardless of the bytes arriving. It does **not** explain
  them, and this work does not try to.

## Non-goals

- No repacking to 7 nibbles (3.5 bytes per digit). It would make a row 185.5
  bytes, put digits across byte boundaries, and need a new prefetch, new pacing
  and a new host format.
- No change to the prefetch or its arbitration. In the bottom band (`yz_bot`)
  the chip keeps fetching slot 1, now a read of the reserved nibble that is
  never displayed, so `we`/`re` timing and the proofs on it stay as they are.
  Skipping that read is a later, separate change.
- No change to the cell (15x22) or grid (53x27). Columns 13 and 14 are both gap.
- No attempt to root-cause the on-board dots.
- `SPEC.md` is left as is; it is already stale for this glyph.

## Changes

### RTL (`src/`)

`multi_seg_monitor.v`
- Delete `xz_dp` and the `xz_dp & yz_bot` term of `seg_hit`.
- `seg_int` becomes `xz_right ? cur_digit[7:4] : cur_digit[3:0]`.
- `yz_bot` keeps `{slot0_seg, slot1_seg} = {3'd3, 3'd7}`; its comment says slot 1
  is the reserved nibble and is never displayed.
- `FORMAL_ZONE` overlap proof: drop the `xz_dp & yz_bot` term (the
  `$countones` list goes from 8 to 7) and change "the 8 segment zones" to 7.
- Comments that describe the spare column as holding the decimal point (the
  digit-body block, the generator header near line 261) are reworded.

`zoneplate.v`
- Behaviour unchanged: byte 3 still ends with `hi <= 4'd0`, so a generated stream
  is byte-identical and the generator gold does not move.
- The comments at the sample table and the FSM say "reserved nibble" where they
  said "decimal point".

### Host tools (`tools/`)

- `segments.py`: `SEGMENTS` has 7 entries. `pack_digit`/`unpack_digit` still take
  and return 8 nibbles, with the last documented as reserved. The comment on
  the cell layout drops the DP sentence.
- `video2seg.py`: remove the `name == "DP"` special case; the loop covers 7
  segments and the output array is zero-initialised, so nibble 7 is 0. The
  docstring's stale "18944 segments" and "9472 bytes" are corrected in the same
  pass, to this glyph's 10,017 drawn segments and 5,724 bytes per frame.
- `seg2png.py`: render 7 segments.
- `attract_proto.py`: drop the DP mention.
- `shapes.py`: a cell always has 7 segments. Remove the "gap column holds a DP"
  branches, `is_dark(7)`, the eighth rectangle and the message about a cell with
  no gap for one. `shapes.CHIP` must still reproduce `segments.SEGMENTS`
  rectangle for rectangle and `zoneplate.v`'s sample table point for point.
- `palette_builder/palette_builder.py` and its tests: the `s < len(cell)` guards
  for "a cell with no room for the decimal point" go, since every cell has 7.

### Firmware (`firmware/`)

No change. `seg_player.py`'s constants (53x27, 5724 bytes/frame) are already
correct, and the `range(8)` hits there are GPIO data pins, not segments.

### Tests

- `tools/test_video2seg.py`: remove the DP exceptions; the isolation test loops
  over 7 segments. The pack round-trip keeps all 8 nibbles.
- `test/test_multi_seg.py`: the stream round-trip and the generator-model check
  loop over 7 segments; the `seg == 7` special case goes. `make_test_frame`
  still fills all 8 nibbles, so the suite also shows the chip ignores nibble 7.
- Gold: `stream.png` and `delay_0000us.png` change (their streamed frames carry
  a nonzero nibble 7: `make_test_frame` fills all 8, and `test/testcard.seg`
  has DP set), so they are regenerated with `make -C test gold` and **looked at
  before anything is committed**. `test_custom_palette_survives_streaming` reads
  `stream.png` too and fails until it is regenerated. The generator golds must
  not change; if they do, that is a bug in this work, not an update to accept.
- A new stream test: a frame whose only lit nibble is nibble 7 renders fully
  black. This pins "reserved and ignored", which nothing else asserts.
- Palette builder tests that expect an 8th nibble or a 7-segment cell for a
  no-gap glyph are rewritten for the always-7 model.

### Docs

`CLAUDE.md`: remove DP from the description of the stream and say nibble 7 is
reserved. Anywhere that says "11448 segments" (`CLAUDE.md`, `README.md`) is
corrected: 11,448 (1431 x 8) is the number of nibbles addressed, and the picture
has 10,017 (1431 x 7) drawn segments. The one measurement that quotes 11448
(the delay sweep's "14 of 11448") is left alone: it was taken before this
change. `digit_shape_plan.md` is a record of the earlier glyph work, so it gets
a dated note pointing here rather than a rewrite.

### Additions found while planning

- `segments.NUM_SEGMENTS` (= 7) is the count every tool and test loops over,
  instead of a bare `range(7)`.
- `shapes.Shape.name(seg)` falls back to `"#<n>"` past `g`. `validate()` exists to
  reject hand-built cells with more than seven rectangles, and its error
  messages name segments; without the fallback they would raise `IndexError`
  rather than the `ValueError` the caller (and `test_validate_rejects_...`)
  expects.
- The model's cx predicate for the chip's cell comes out as `cx in 7..14`, not
  the RTL's `xz_right` (9..12): columns that never carry two segments on a
  scanline are free, and the model picks the cheapest. Both draw the same
  picture and neither is to be "corrected" to the other.

## Verification

Run in this order, each before the next, and no two sims at once (they share
`frame.ppm`):

1. `python3 tools/test_video2seg.py`, `python3 tools/test_palettes.py`, the
   palette builder tests, `python3 tools/shapes.py`.
2. `SIM=verilator make -C test` (whole suite, inferred array), then the same with
   `IHP_SRAM=1` after deleting `test/sim_build/rtl`.
3. `make formal`, in particular the `FORMAL_ZONE` proof.
4. `make -C test gold`, then view the changed PNGs and the `git diff --stat` on
   `test/gold/`: only `stream.png` and `delay_0000us.png` may have changed.
5. Re-run the scratch probe that streams a `.seg` through the RTL and compares
   every pixel with the file, on the current `waterdropbw_24_newdigit.seg` and on
   a frame with only nibble 7 set.

Not covered here: gate-level simulation and the ASIC re-harden happen in CI, and
the FPGA needs a re-flash to carry the change. Expected area and timing effect
is a comparator and a mux term removed, nothing more; CI will say.

## Risks

- **`shapes.CHIP` drifting from `segments.SEGMENTS`.** Mitigated by the existing
  test that compares them.
- **Something else reading nibble 7.** A repo-wide grep before the change found
  no such reader beyond the files listed above; step 1 and 2 above would catch a
  miss.
- **The on-board dot survives.** If it does, it is not DP. Diagnostic frames
  (`diag_zero`, `diag_nodp`, `diag_dponly`) already exist in the scratchpad for
  telling the remaining causes apart.
