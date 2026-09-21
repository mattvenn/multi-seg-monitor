# Remove the decimal point Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The chip draws seven segments per digit; the decimal point is gone from the RTL, the converter, every model and every test, while the stream format (4 bytes a digit, nibble 7 reserved and ignored) stays exactly as it is.

**Architecture:** Two passes, each ending green. Pass 1 (Task 1) removes DP from everything host-side (`segments.py`, converter, `shapes.py`, the palette builder, the tests) while the RTL is untouched, so the sim suite still passes against the old RTL. Pass 2 (Task 2) adds a test that the chip ignores nibble 7 (it fails on the old RTL), then removes DP from the renderer and regenerates the one gold image that lit it. Task 3 is docs and the final sweep.

**Tech Stack:** Verilog (Icarus/Verilator via cocotb), SymbiYosys, Python 3 + numpy for tools.

**Spec:** `docs/superpowers/specs/2026-09-21-remove-decimal-point-design.md`

## Global Constraints

- The stream format does not change: digit = 4 bytes, low nibble first, `a, b, c, d, e, f, g, <reserved>`; row = 212 bytes; frame = 5724 bytes; pacing ~109.58 pixel clocks per byte.
- Cell stays 15x22, grid 53x27. No change to the prefetch or its arbitration; `yz_bot` keeps `slot1_seg = 7`.
- Toolchain is **`~/oss-cad-suite`**, not `~/asic/oss-cad-suite` as CLAUDE.md says: `source ~/oss-cad-suite/environment` before any `make`.
- Never run two sims at once (they share `test/frame.ppm`). `make` exits 0 even when cocotb tests fail: after every sim run, check `! grep -q "<failure" test/results.xml`, and `rm -f test/results.xml` before a rerun.
- Prefer `SIM=verilator` for local runs.
- Comments explain *why* and match the surrounding density (CLAUDE.md "Conventions").
- **Do not commit unless the user has said to.** Each task ends with a commit step; if the user has not asked for commits, stop before it and report. `firmware/gen_mode.py` and `firmware/seg_player.py` carry the user's own uncommitted edits: never stage them. Stage files by name, never `git add -A`.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
- `test/gold/` may change only in `stream.png` and `delay_0000us.png`: the two goldens whose streamed frame carries a nonzero nibble 7 (`make_test_frame` fills all 8 nibbles; the committed `test/testcard.seg` has DP set). `generator.png`, `generator_tinyvga.png` and `generator_palette2.png` must not change; if one moves, that is a bug in this work, not an update to accept.

---

### Task 1: Host-side model without DP

**Files:**
- Modify: `tools/segments.py:255-285`
- Modify: `tools/video2seg.py` (docstring, `frame_to_segments`, argparse help)
- Modify: `tools/seg2png.py:32`
- Modify: `tools/shapes.py` (docstring, `Shape`, `digit`, `warnings`, `_predicate`)
- Modify: `tools/palette_builder/palette_builder.py` (three comments)
- Modify: `tools/attract_proto.py:20-24` (docstring)
- Modify: `tools/test_video2seg.py` (rewrite)
- Modify: `tools/palette_builder/test_palette_builder.py` (five spots)
- Modify: `test/test_multi_seg.py` (loop bounds only)

**Interfaces:**
- Produces: `segments.NUM_SEGMENTS == 7`; `segments.SEGMENTS` has 7 entries (`a`..`g`); `segments.pack_digit` / `unpack_digit` still take and return 8 nibbles, nibble 7 reserved. `shapes.Shape.name(seg)` returns `"#<n>"` for `seg >= NUM_SEGMENTS`. Task 2 relies on `NUM_SEGMENTS`.

- [ ] **Step 1: Rewrite `tools/test_video2seg.py` for a 7-segment digit**

Replace the whole file with:

```python
#!/usr/bin/env python3
"""
Checks for the converter's cell mapping.  Run directly: ./test_video2seg.py

The reshape in frame_to_segments folds a 480x624 image into (row, cy, col, cx).
Getting that axis order wrong still produces a plausible looking picture -- just
one that is transposed or sheared -- so it is worth pinning down.
"""

import numpy as np

import segments
import video2seg

RESERVED = segments.NUM_SEGMENTS  # nibble 7: no segment, so always 0


def test_uniform_cells():
    """A cell filled with one value gives every segment that value, and the
    reserved nibble stays 0."""
    linear = np.zeros((segments.GRID_H, segments.GRID_W))
    for row in range(segments.ROWS):
        for col in range(segments.COLS):
            value = ((col * 7 + row * 3) % 16) / 15.0
            y = row * segments.CELL_H
            x = col * segments.CELL_W
            linear[y : y + segments.CELL_H, x : x + segments.CELL_W] = value

    got = video2seg.frame_to_segments(linear)
    for row in range(segments.ROWS):
        for col in range(segments.COLS):
            for seg in range(8):
                want = 0 if seg == RESERVED else (col * 7 + row * 3) % 16
                assert got[seg, row, col] == want, (
                    f"nibble {seg} of ({col}, {row}): got {got[seg, row, col]}, want {want}"
                )


def test_segment_isolation():
    """Light one segment's rectangle and only that segment should respond."""
    for target in range(segments.NUM_SEGMENTS):
        linear = np.zeros((segments.GRID_H, segments.GRID_W))
        x0, x1, y0, y1 = segments.segment_pixels(0, 0, target)
        # segment_pixels includes the left/top margin; the converter works on
        # the grid alone, so take it back off.
        linear[
            y0 - segments.MARGIN_Y : y1 - segments.MARGIN_Y + 1,
            x0 - segments.MARGIN_X : x1 - segments.MARGIN_X + 1,
        ] = 1.0

        got = video2seg.frame_to_segments(linear)
        for seg in range(8):
            want = 15 if seg == target else 0
            assert got[seg, 0, 0] == want, (
                f"lit {segments.SEGMENTS[target][0]}, nibble {seg} read "
                f"{got[seg, 0, 0]}, want {want}"
            )


def test_the_old_decimal_point_position_reads_nowhere():
    """The pixels where the decimal point used to be -- a dot in the first gap
    column, cx 13, cy 16-19 -- belong to no segment now, so lighting them must
    move nothing."""
    linear = np.zeros((segments.GRID_H, segments.GRID_W))
    linear[16:20, 13] = 1.0
    got = video2seg.frame_to_segments(linear)
    assert not got.any(), "something still samples the old decimal point position"


def test_pack_round_trip():
    intensity = np.arange(8 * segments.ROWS * segments.COLS, dtype=np.uint8) % 16
    intensity = intensity.reshape(8, segments.ROWS, segments.COLS)
    data = video2seg.pack(intensity)
    assert len(data) == segments.FRAME_BYTES

    for row in (0, 7, segments.ROWS - 1):
        for col in (0, 13, segments.COLS - 1):
            off = segments.digit_offset(col, row)
            got = segments.unpack_digit(data[off : off + 4])
            want = [int(intensity[s, row, col]) for s in range(8)]
            assert got == want, f"digit ({col}, {row}): got {got}, want {want}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"{name} ok")
```

- [ ] **Step 2: Update `tools/palette_builder/test_palette_builder.py`**

Five edits.

(a) Band table, the "classic five" (around line 441). Replace

```python
        want = {frozenset(p) for p in (("a",), ("f", "b"), ("g",), ("e", "c"),
                                       ("d", "DP") if len(cell) == 8 else ("d",))}
```

with

```python
        want = {frozenset(p) for p in (("a",), ("f", "b"), ("g",), ("e", "c"), ("d",))}
```

(b) The gaps test (around line 523). Replace

```python
        # Every nibble lit except the decimal point, which sits in the gap.
        frame = bytearray(b"\xff" * cell.frame_bytes)
        for digit in range(cell.digits):
            frame[digit * 4 + 3] = 0x0F  # byte 3 is {DP, g}: keep g, drop DP
        idx = pb.index_image_from_frame(bytes(frame), cell)
```

with

```python
        # Every nibble lit, the reserved one included: it has no segment to draw.
        frame = b"\xff" * cell.frame_bytes
        idx = pb.index_image_from_frame(frame, cell)
```

(c) `test_the_default_proportions_are_the_chips_own_geometry` (around lines 626-628). Replace

```python
    assert len(cell) == 8
```

with

```python
    assert len(cell) == segments.NUM_SEGMENTS == 7
```

and replace

```python
    assert cell.is_dark(7) and not any(cell.is_dark(s) for s in range(7))  # DP
```

with

```python
    # Nibble 7 is reserved: nothing to light. Every real segment is lit.
    assert cell.is_dark(7) and not any(cell.is_dark(s) for s in range(7))
```

(d) `test_warnings_name_the_costs_and_not_the_ratios` (around lines 651-654). Replace

```python
    assert len(touching) == 7  # no gap column, so no decimal point
    assert any("mesh" in m for m in shapes.warnings(touching))
    assert any("nibble 7" in m for m in shapes.warnings(touching))
    assert any("touches the next digit" in m for m in shapes.warnings(shapes.digit(gap_x=1)))
```

with

```python
    assert len(touching) == 7
    assert any("mesh" in m for m in shapes.warnings(touching))
    assert shapes.warnings(shapes.digit(gap_x=1)) == []  # one gap column is enough now
```

(e) `test_effect_index_image_follows_the_sample_points` docstring (around line 711). Replace

```python
    -- the one pixel of it the zone plate decides -- and the decimal point
    stays dark whether or not the cell has room for one."""
```

with

```python
    -- the one pixel of it the zone plate decides -- and the reserved nibble
    stays dark."""
```

- [ ] **Step 3: Run the tools tests and see them fail**

```bash
cd /home/matt/work/asic-workshop/shuttle-ttihp26a/multi-seg-monitor/tools
python3 test_video2seg.py; python3 palette_builder/test_palette_builder.py
```

Expected: `test_video2seg.py` dies with `AttributeError: module 'segments' has no attribute 'NUM_SEGMENTS'`. The palette builder run fails in `test_the_default_proportions_are_the_chips_own_geometry` (`len(cell) == 8` while the test now wants 7) or an earlier test that reads the new expectations.

- [ ] **Step 4: `tools/segments.py`: seven segments and `NUM_SEGMENTS`**

Replace

```python
# Segment rectangles within a cell, as (name, x0, x1, y0, y1) inclusive.
# Index order is the nibble order of a digit word: a, b, c, d, e, f, g, DP.
# The digit body is 13x20; columns 13-14 and rows 20-21 are the gaps that keep
# neighbouring digits from merging, and the decimal point lives in the first
# gap column.
SEGMENTS = [
    ("a", 4, 8, 0, 3),
    ("b", 9, 12, 4, 7),
    ("c", 9, 12, 12, 15),
    ("d", 4, 8, 16, 19),
    ("e", 0, 3, 12, 15),
    ("f", 0, 3, 4, 7),
    ("g", 4, 8, 8, 11),
    ("DP", 13, 13, 16, 19),
]
```

with

```python
# Segment rectangles within a cell, as (name, x0, x1, y0, y1) inclusive.
# Index order is the nibble order of a digit word: a, b, c, d, e, f, g, and
# then nibble 7, which is reserved: it used to be a decimal point, and the chip
# now ignores it. The digit body is 13x20; columns 13-14 and rows 20-21 are the
# gaps that keep neighbouring digits from merging.
SEGMENTS = [
    ("a", 4, 8, 0, 3),
    ("b", 9, 12, 4, 7),
    ("c", 9, 12, 12, 15),
    ("d", 4, 8, 16, 19),
    ("e", 0, 3, 12, 15),
    ("f", 0, 3, 4, 7),
    ("g", 4, 8, 8, 11),
]
NUM_SEGMENTS = len(SEGMENTS)  # 7 drawn; the eighth nibble of a digit is reserved
```

Replace the two docstrings:

```python
    """Eight 4-bit segment intensities -> 4 bytes, low nibble first."""
```

with

```python
    """Eight 4-bit nibbles -> 4 bytes, low nibble first: the seven segments,
    then the reserved nibble (byte 3's high half), which the chip ignores."""
```

and

```python
    """4 bytes -> eight 4-bit segment intensities."""
```

with

```python
    """4 bytes -> eight 4-bit nibbles; the last is the reserved one."""
```

- [ ] **Step 5: `tools/video2seg.py`: no special case, and correct the stale numbers**

In the module docstring replace

```
Each of the 18944 segments gets its own 4 bit brightness, averaged from the
```

with

```
Each of the 10017 segments (1431 digits of 7) gets its own 4 bit brightness, averaged from the
```

and replace `Output is raw frames of 9472 bytes, ready to copy to the demoboard.` with `Output is raw frames of 5724 bytes, ready to copy to the demoboard.`

In `frame_to_segments`, replace `rather than 18944 separate lookups` with `rather than 10017 separate lookups`, and replace the loop head and its special case:

```python
    out = np.zeros((8, segments.ROWS, segments.COLS), dtype=np.uint8)
    for i, (name, x0, x1, y0, y1) in enumerate(segments.SEGMENTS):
        # The decimal point stays dark, as it does under the generator
        # (zoneplate.v never samples it). It is one pixel wide in the gap
        # column, so lit it reads as a stray dot against the next digit's f
        # segment rather than as picture. The nibble stays in the stream
        # because the chip still reads it.
        if name == "DP":
            continue
        mean
```

with

```python
    # Nibble 7 is reserved and stays 0: the chip ignores it, and the generator
    # writes it as 0 too.
    out = np.zeros((8, segments.ROWS, segments.COLS), dtype=np.uint8)
    for i, (_, x0, x1, y0, y1) in enumerate(segments.SEGMENTS):
        mean
```

In `main()` replace `help="raw segment stream, 9472 bytes per frame"` with `help="raw segment stream, 5724 bytes per frame"`.

- [ ] **Step 6: `tools/seg2png.py`: render the seven segments**

Replace `            for seg in range(8):` (line 32) with

```python
            for seg in range(segments.NUM_SEGMENTS):  # nibble 7 is reserved
```

- [ ] **Step 7: `tools/shapes.py`: cells always have seven segments**

Apply these edits.

(a) Module docstring, replace

```
neighbour's left rail and the grid reads as a mesh rather than as digits, and
the decimal point lives in the first gap column, so a digit with no gap hasn't
got one.  warnings() says so.
```

with

```
neighbour's left rail and the grid reads as a mesh rather than as digits.
warnings() says so.
```

(b) Replace `` `seg_int = (xz_right | xz_dp) ? cur_digit[7:4] : cur_digit[3:0]`. `` with `` `seg_int = xz_right ? cur_digit[7:4] : cur_digit[3:0]`. ``

(c) Replace `(a alone, then f and\nb, then g, then e and c, then d and DP)` (two lines in the docstring) with

```
(a alone, then f and
b, then g, then e and c, then d alone)
```

(d) `Shape` docstring, replace

```python
    """One cell: up to 8 segments, each an inclusive (x0, x1, y0, y1)
    rectangle in cell coordinates, in the nibble order a, b, c, d, e, f, g, DP.
    A cell with no gap to put the decimal point in simply has 7.
```

with

```python
    """One cell: the seven segments, each an inclusive (x0, x1, y0, y1)
    rectangle in cell coordinates, in the nibble order a, b, c, d, e, f, g.
    Nibble 7 of a digit is reserved and has no rectangle.
```

(e) `Shape.__init__`, replace

```python
        # The body is the seven bars; the decimal point sits in the gap, so it
        # must not count towards the size.
        body = self.segs[:7]
        self.w = max(r[1] for r in body) + 1
        self.h = max(r[3] for r in body) + 1
```

with

```python
        self.w = max(r[1] for r in self.segs) + 1
        self.h = max(r[3] for r in self.segs) + 1
```

(f) `Shape.name`, replace

```python
    def name(self, seg):
        return segments.SEGMENTS[seg][0]
```

with

```python
    def name(self, seg):
        """a..g for the chip's segments. A hand-built cell that validate() is
        about to reject can have more, and its error messages still need a name
        for them."""
        return segments.SEGMENTS[seg][0] if seg < segments.NUM_SEGMENTS else f"#{seg}"
```

(g) `Shape.is_dark`, replace

```python
        """The generator lights every segment but the decimal point, which has
        no sensible place in a picture -- and a cell with no room for one has
        nothing to light at all."""
        return seg >= len(self) or seg == 7
```

with

```python
        """The generator lights every segment. The nibbles it leaves at 0 are
        the ones with no segment: the reserved one, nibble 7."""
        return seg >= len(self)
```

(h) `digit()`: replace `yd0, yd1 = yl1 + 1, yl1 + th     # d, DP` with `yd0, yd1 = yl1 + 1, yl1 + th     # d`, and delete this block from the end of `digit()`:

```python
    # The decimal point is a dot in the first gap column, which is where a real
    # display puts it -- so a digit with no gap to its neighbour simply hasn't
    # got one, and its nibble goes unused. One pixel wide whatever the rails
    # are: it is a point, not a bar, and that is what the chip draws.
    if p["gap_x"] >= 1:
        segs.append((w, w, yd0, yd1))
```

(so `return Shape(segs, p)` follows the `]` closing the `segs` list).

(i) `warnings()`: replace

```python
    if shape.gap_x <= 0:
        msgs.append(
            "gap_x is 0: a digit's right rail touches its neighbour's left rail, so the "
            "grid reads as a mesh rather than as digits -- and there is nowhere to put "
            "the decimal point, so nibble 7 goes unused"
        )
    elif shape.gap_x == 1:
        msgs.append("the decimal point fills the only gap column, so it touches the next digit")
```

with

```python
    if shape.gap_x <= 0:
        msgs.append(
            "gap_x is 0: a digit's right rail touches its neighbour's left rail, so the "
            "grid reads as a mesh rather than as digits"
        )
```

(j) `_predicate`: replace ``i.e. this cell's\n    `xz_right | xz_dp`.`` (docstring) with ``i.e. this cell's\n    `xz_right`.`` and replace the comment `# own xz_right | xz_dp is.` with `# own xz_right is.` (the line above it reads `# Orient the component so the right-hand side is slot 1, as the chip's`).

- [ ] **Step 8: Reword the three palette-builder comments and the `attract_proto` docstring**

`tools/palette_builder/palette_builder.py`:

Replace

```python
        # A cell with no room for the decimal point has no nibble 7 to draw,
        # so it indexes offset 0 and is masked off by is_dark below.
```

with

```python
        # Nibble 7 is reserved and has no segment to draw, so it indexes
        # offset 0 and is masked off by is_dark below.
```

Replace

```python
    or arrays), then scattered like index_image_from_frame. DP stays dark, as
    in attract_proto.sample() -- and so does its nibble on a cell too wide to
    have a decimal point at all.
```

with

```python
    or arrays), then scattered like index_image_from_frame. The reserved
    nibble 7 stays dark, as in attract_proto.sample().
```

Replace `# between them -- slot0_seg/slot1_seg and xz_right | xz_dp.` with `# between them -- slot0_seg/slot1_seg and xz_right.`

`tools/attract_proto.py`, replace

```
sampled at its centre, so there are 6 distinct x positions per digit (f/e, a/g/d,
b/c, DP) -- 18944 "pixels" rather than 2368. In RTL those are col*12 plus a
```

with

```
sampled at its centre, so there are 3 distinct x positions per digit (f/e, a/g/d,
b/c) -- 10017 "pixels" rather than 1431. In RTL those are col*15 plus a
```

- [ ] **Step 9: Run the tools tests and see them pass**

```bash
cd /home/matt/work/asic-workshop/shuttle-ttihp26a/multi-seg-monitor/tools
python3 test_video2seg.py && python3 test_palettes.py && python3 palette_builder/test_palette_builder.py && python3 shapes.py | sed -n 1,8p
```

Expected: every `test_... ok`; the `shapes.py` header for the chip reads `7/8 nibbles, slot 1 when cx in 7..14` and the bands line is `0-3:(a,a) 4-7:(f,b) 8-11:(g,g) 12-15:(e,c) 16-19:(d,d) 20-21:(-,-)`.

The predicate is the model's cheapest one, not a copy of the RTL's: columns 4-8 and 13-14 never carry two segments on a scanline, so which slot they select is free, and `_predicate` gives them their nearest neighbour's side. The RTL's `xz_right` (cx 9..12) is another valid choice. Both draw the same picture, which is what the sim suite checks. Don't "fix" either one to match the other.

- [ ] **Step 10: `test/test_multi_seg.py`: loop over the seven segments**

Apply four edits.

(a) In `check_against_model`, replace

```python
    """Every segment of a captured frame against an attract_proto sample
    function. Segment 7 is the decimal point, which the generator leaves
    dark."""
```

with

```python
    """Every segment of a captured frame against an attract_proto sample
    function."""
```

and replace

```python
            for seg in range(8):
                x, y = segments.segment_centre(col, row, seg)
                got = px[(y * width + x) * 3] // 17
                want = 0 if seg == 7 else model(col * CELL_W + attract_proto.SEG_CX[seg],
                                                row * CELL_H + attract_proto.SEG_CY[seg])
```

with

```python
            for seg in range(segments.NUM_SEGMENTS):
                x, y = segments.segment_centre(col, row, seg)
                got = px[(y * width + x) * 3] // 17
                want = model(col * CELL_W + attract_proto.SEG_CX[seg],
                             row * CELL_H + attract_proto.SEG_CY[seg])
```

(b) In `test_stream_frame`, replace

```python
            sent = segments.unpack_digit(frame[off : off + 4])
            for seg in range(8):
                x, y = segments.segment_centre(col, row, seg)
                # No gamma stage: the stored intensity is the DAC code.
```

with

```python
            sent = segments.unpack_digit(frame[off : off + 4])
            # Nibble 7 is sent (make_test_frame fills it) but reserved: there is
            # no pixel to read it back from. test_reserved_nibble_is_ignored
            # checks that it draws nothing.
            for seg in range(segments.NUM_SEGMENTS):
                x, y = segments.segment_centre(col, row, seg)
                # No gamma stage: the stored intensity is the DAC code.
```

(c) In `analyse()` (delay sweep), replace

```python
            for seg in range(8):
                x, y = segments.segment_centre(col, row, seg)
                got = px[(y * width + x) * 3] // 17
                if got == want(col, row, seg):
```

with

```python
            for seg in range(segments.NUM_SEGMENTS):
                x, y = segments.segment_centre(col, row, seg)
                got = px[(y * width + x) * 3] // 17
                if got == want(col, row, seg):
```

(d) The three segment counts:

```bash
cd /home/matt/work/asic-workshop/shuttle-ttihp26a/multi-seg-monitor
sed -i 's/segments\.ROWS \* segments\.COLS \* 8/segments.ROWS * segments.COLS * segments.NUM_SEGMENTS/' test/test_multi_seg.py
grep -n "NUM_SEGMENTS" test/test_multi_seg.py
```

Expected: 3 lines from the `sed` (the two in `test_stream_frame` and `total = ...` in `run_delay_case`) plus the 3 loop bounds from (a)-(c), 6 in all.

- [ ] **Step 11: Run the whole sim suite against the still-unchanged RTL**

```bash
cd /home/matt/work/asic-workshop/shuttle-ttihp26a/multi-seg-monitor
source ~/oss-cad-suite/environment
rm -f test/results.xml
SIM=verilator make -C test 2>&1 | tail -5
! grep -q "<failure" test/results.xml && echo "NO FAILURES"
git status --short test/gold
```

Expected: `NO FAILURES`, and `git status --short test/gold` prints nothing (the RTL still draws DP, but no test reads it and no gold changes).

- [ ] **Step 12: Commit (only if the user has asked for commits)**

```bash
git add tools/segments.py tools/video2seg.py tools/seg2png.py tools/shapes.py \
        tools/attract_proto.py tools/test_video2seg.py \
        tools/palette_builder/palette_builder.py tools/palette_builder/test_palette_builder.py \
        test/test_multi_seg.py docs/superpowers/specs/2026-09-21-remove-decimal-point-design.md \
        docs/superpowers/plans/2026-09-21-remove-decimal-point.md
git commit -m "Host tools: the digit has seven segments, nibble 7 is reserved

segments.SEGMENTS drops the decimal point; NUM_SEGMENTS is the count the
tools and tests loop over. The stream format is unchanged. The RTL still
draws DP until the next commit; nothing reads it back.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: The chip ignores nibble 7

**Files:**
- Modify: `test/test_multi_seg.py` (add one test after `test_stream_frame`)
- Modify: `src/multi_seg_monitor.v` (zones, slot table comment, `seg_hit`, `seg_int`, generator comment, `FORMAL_ZONE`)
- Modify: `src/zoneplate.v` (two comments)
- Regenerate: `test/gold/stream.png`, `test/gold/delay_0000us.png`

**Interfaces:**
- Consumes: `segments.NUM_SEGMENTS`, `segments.ROWS`, `segments.COLS` (Task 1).
- Produces: RTL with no `xz_dp`; `seg_int = xz_right ? cur_digit[7:4] : cur_digit[3:0]`.

- [ ] **Step 1: Write the failing test**

In `test/test_multi_seg.py`, insert this immediately after the line `    check_gold(dut, "stream.png", width, height, px)` (the last line of `test_stream_frame`, which is unique in the file):

```python


@cocotb.test()
async def test_reserved_nibble_is_ignored(dut):
    """Nibble 7 -- byte 3's high half -- is reserved: a frame that lights only
    that nibble must draw nothing. It used to be the decimal point, so a stream
    written for the old chip (or a host that leaves junk there) still has data
    in it, and none of it may reach the screen."""
    cocotb.start_soon(Clock(dut.clk, CLK_PS, unit="ps").start())
    await reset(dut)
    dut.uio_in.value = UIO_IDLE

    frame = bytes([0x00, 0x00, 0x00, 0xF0]) * (segments.ROWS * segments.COLS)
    assert len(frame) == segments.FRAME_BYTES
    cocotb.start_soon(host_stream(dut, frame, frames=7))
    await capture_frame(dut)

    width, height, px = read_ppm("frame.ppm")
    lit = [i // 3 for i in range(0, len(px), 3) if px[i] or px[i + 1] or px[i + 2]]
    assert not lit, (
        f"{len(lit)} pixels lit by a frame whose only set nibble is the reserved one; "
        f"first at ({lit[0] % width}, {lit[0] // width})"
    )
```

- [ ] **Step 2: Run it against the old RTL and see it fail**

```bash
cd /home/matt/work/asic-workshop/shuttle-ttihp26a/multi-seg-monitor
source ~/oss-cad-suite/environment
rm -f test/results.xml
SIM=verilator make -C test COCOTB_TEST_FILTER='"test_reserved_nibble_is_ignored"' 2>&1 | grep -i "pixels lit\|FAIL=\|PASS="
```

Expected: `FAIL=1` with `5724 pixels lit by a frame whose only set nibble is the reserved one; first at (...)`. (5724 = 1431 digits x 4 DP pixels.)

- [ ] **Step 3: `src/multi_seg_monitor.v`: take the decimal point out of the renderer**

(a) Replace

```verilog
    // digits.  The decimal point lives in the first spare column, which is
    // where a real display puts it.
    wire xz_left  = (cx < 4);                   // f, e
    wire xz_mid   = (cx >= 4)  && (cx < 9);     // a, g, d
    wire xz_right = (cx >= 9)  && (cx < 13);    // b, c
    wire xz_dp    = (cx == 13);                 // DP, cx == 14 is the gap
```

with

```verilog
    // digits.
    wire xz_left  = (cx < 4);                   // f, e
    wire xz_mid   = (cx >= 4)  && (cx < 9);     // a, g, d
    wire xz_right = (cx >= 9)  && (cx < 13);    // b, c; cx 13, 14 is the gap
```

(b) Replace `    wire yz_bot   = (cy >= 16) && (cy < 20);    // d, DP; cy 20,21 is the gap` with `    wire yz_bot   = (cy >= 16) && (cy < 20);    // d; cy 20,21 is the gap`.

(c) Replace

```verilog
    // two segments, so a scanline only ever shows two of a digit's eight
    // nibbles. The prefetch fetches just those two, into two slots: slot 0 for
    // the segment in the left or middle x zone, slot 1 for the one in the
    // right or DP zone. Segment numbers are the nibble order (a = 0 ... DP =
    // 7), so segment k is byte k[2:1], high nibble if k[0].
```

with

```verilog
    // two segments, so a scanline only ever shows two of a digit's seven
    // segments. The prefetch fetches just those two, into two slots: slot 0 for
    // the segment in the left or middle x zone, slot 1 for the one in the
    // right zone. Segment numbers are the nibble order (a = 0 ... g = 6;
    // nibble 7 is reserved and never displayed), so segment k is byte k[2:1],
    // high nibble if k[0].
```

(d) Replace `            yz_bot:  {slot0_seg, slot1_seg} = {3'd3, 3'd7};  // d, DP` with

```verilog
            // d, and slot 1 reads the reserved nibble: nothing sits at cx 9..12
            // down here, so it is fetched (the prefetch timing doesn't change)
            // and never shown.
            yz_bot:  {slot0_seg, slot1_seg} = {3'd3, 3'd7};
```

(e) Replace

```verilog
                   (xz_mid   & yz_mid) |   // g
                   (xz_dp    & yz_bot);    // DP

    wire [3:0] seg_int = (xz_right | xz_dp) ? cur_digit[7:4] : cur_digit[3:0];
```

with

```verilog
                   (xz_mid   & yz_mid);    // g

    wire [3:0] seg_int = xz_right ? cur_digit[7:4] : cur_digit[3:0];
```

(f) Replace

```verilog
    // the zone plate still sweeps all 15 lit codes across the screen, but the
    // decimal point is always dark in this mode.
```

with

```verilog
    // the zone plate still sweeps all 15 lit codes across the screen, and the
    // reserved nibble is always 0 in this mode.
```

(g) `FORMAL_ZONE`: replace `    // At most one of the 8 segment zones may claim a given (cx, cy): if two` with `    // At most one of the 7 segment zones may claim a given (cx, cy): if two`, and replace

```verilog
                             xz_mid  & yz_mid,  xz_dp   & yz_bot}) <= 1);
```

with

```verilog
                             xz_mid  & yz_mid}) <= 1);
```

- [ ] **Step 4: `src/zoneplate.v`: two comments, no behaviour change**

Replace `            default: {ox, oy} = {4'd6,  5'd9};  // g; DP (7) is never sampled` with `            default: {ox, oy} = {4'd6,  5'd9};  // g; nibble 7 is reserved, never sampled`.

Replace

```verilog
                        // Segment 7 is the decimal point, which the zone
                        // plate leaves dark: no need to sample it.
```

with

```verilog
                        // Nibble 7 is reserved and always 0: there is no
                        // segment to sample.
```

- [ ] **Step 5: Run the new test and see it pass**

```bash
cd /home/matt/work/asic-workshop/shuttle-ttihp26a/multi-seg-monitor
source ~/oss-cad-suite/environment
rm -f test/results.xml
SIM=verilator make -C test COCOTB_TEST_FILTER='"test_reserved_nibble_is_ignored"' 2>&1 | grep -i "FAIL=\|PASS="
```

Expected: `PASS=1 FAIL=0`.

- [ ] **Step 6: Run the whole suite and see only the stream gold complain**

```bash
rm -f test/results.xml
SIM=verilator make -C test 2>&1 | grep -i "gold\|mismatch\|FAIL=\|PASS=" | head
grep -c "<failure" test/results.xml
ls test/*_diff.png
```

Expected: exactly two failing tests, `test_stream_frame` and `test_custom_palette_survives_streaming` (both read the `stream.png` gold, which still has DP dots), and `test/stream_diff.png` written; nothing else, in particular no `generator*_diff.png`. (`delay_0000us.png` is only checked under `DELAY_SWEEP=1`, so it isn't in this run; step 7 regenerates it.) If any generator test fails, stop: the RTL change altered generated output, which it must not.

- [ ] **Step 7: Regenerate the gold and look at it**

```bash
rm -f test/*_diff.png test/results.xml
SIM=verilator make -C test gold 2>&1 | tail -3
git status --short test/gold
git diff --stat test/gold
```

Expected: only `test/gold/stream.png` and `test/gold/delay_0000us.png` are modified. Then **view** both new PNGs next to the old ones (`git show HEAD:test/gold/stream.png > "$SCRATCH/old_stream.png"`, same for `delay_0000us.png`) and confirm the only difference is the missing 1x4 dots at the bottom-right of each digit. Nothing else may change. If anything else differs, or a generator gold shows as modified, stop and report.

- [ ] **Step 8: Formal proofs**

```bash
source ~/oss-cad-suite/environment
make formal 2>&1 | tail -15
```

Expected: every proof in `formal/Makefile` passes, `zone_exclusivity` included (it now counts 7 zone terms). Report the tail of the output verbatim.

- [ ] **Step 9: The macro build of the same suite**

```bash
rm -rf test/sim_build/rtl test/results.xml
SIM=verilator make -C test IHP_SRAM=1 2>&1 | tail -3
! grep -q "<failure" test/results.xml && echo "NO FAILURES (IHP macro)"
rm -rf test/sim_build/rtl test/results.xml
```

Expected: `NO FAILURES (IHP macro)`. (This run checks against the regenerated `stream.png` from step 7.) The last `rm` makes the next default run rebuild without the macro define (the build isn't rebuilt on a changed define).

- [ ] **Step 10: Probe a real file through the RTL**

The scratch module `dp_probe.py` from earlier in the session streams frame 0 of a `.seg` through the RTL and compares every pixel with the file. It lives in the session scratchpad and may be gone; if so, skip this step and say so.

```bash
S=/tmp/claude-1000/-home-matt-work-asic-workshop-shuttle-ttihp26a-multi-seg-monitor/a38e4348-abfd-46f7-87a9-430cc1ab7f0e/scratchpad
source ~/oss-cad-suite/environment
for f in $PWD/waterdropbw_24_newdigit.seg $PWD/video.seg; do
  DPFILE=$f PYTHONPATH=$S:$PWD/tools:$PWD/test SIM=verilator make -C test COCOTB_TEST_MODULES=dp_probe 2>&1 | grep "PROBE .*differing\|PROBE DP-rect"
  rm -f test/results.xml
done
```

Expected for **both** files: `pixels differing from file: 0` and `DP-rect pixels: 5724, lit on screen: 0`. The probe builds its expected picture from `segments.SEGMENTS`, which now has seven entries, so it expects nothing at the old DP position; `video.seg` is the *old* converter's output with DP nibbles set (about 10.7k of 11.4k nonzero), so a pass on it is the direct evidence that streamed DP data no longer reaches the screen.

- [ ] **Step 11: Commit (only if the user has asked for commits)**

```bash
git add src/multi_seg_monitor.v src/zoneplate.v test/test_multi_seg.py \
        test/gold/stream.png test/gold/delay_0000us.png
git commit -m "RTL: remove the decimal point; nibble 7 is reserved and ignored

The renderer no longer has a DP zone: seg_int picks the high nibble for the
right rail only, and the FORMAL_ZONE overlap proof counts seven zones. The
prefetch is untouched -- yz_bot still fetches slot 1, now the reserved
nibble, never shown -- so arbitration timing is unchanged. New test: a frame
whose only lit nibble is nibble 7 draws nothing. stream.png loses the DP
dots, as does delay_0000us.png; the generator golds are unchanged.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Docs and the final sweep

**Files:**
- Modify: `CLAUDE.md:8` and `CLAUDE.md:200-202`
- Modify: `README.md:12`, `README.md:298`
- Modify: `digit_shape_plan.md` (a dated note under the title)

- [ ] **Step 1: `CLAUDE.md`**

Replace `11448 segments, 4 bits of brightness each) as an 800x600@60Hz VGA signal.` with `10017 segments, 4 bits of brightness each) as an 800x600@60Hz VGA signal.`

Replace

```
low-to-high as `a, b, c, d, e, f, g, DP` — host software depends on it.
```

with

```
low-to-high as `a, b, c, d, e, f, g`, then a reserved nibble (byte 3's high half)
that the chip ignores — it was a decimal point until this branch removed it, so
`main`'s files still play here and merely lose their DP. Host software depends on
this order. The stream is still 4 bytes a digit on purpose: a row stays 212
bytes and the pacing arithmetic below stays exact.
```

Leave the sentence containing `14 of 11448` alone: it is a measurement taken before this change, and the sweep now reports its total as 10017.

- [ ] **Step 2: `README.md`**

Replace `1431 digits, 11448 segments` (line 12) with `1431 digits, 10017 segments`, and `Each of the 11448 segments averages` (line 298) with `Each of the 10017 segments averages`.

- [ ] **Step 3: Note the supersession in `digit_shape_plan.md`**

It is a record of the earlier glyph work, so it is annotated rather than rewritten:

```bash
python3 - <<'E'
p = 'digit_shape_plan.md'
first, rest = open(p).read().split('\n', 1)
note = ("\n> Note (2026-09-21): the decimal point described below was removed later; nibble 7 is now\n"
        "> reserved and ignored. See docs/superpowers/specs/2026-09-21-remove-decimal-point-design.md.\n")
open(p, 'w').write(first + '\n' + note + rest)
E
sed -n 1,5p digit_shape_plan.md
```

Expected: the title, then the blockquote note.

- [ ] **Step 4: Sweep for stragglers**

```bash
grep -rn -w "DP\|xz_dp" src tools test firmware formal README.md CLAUDE.md --include="*" 2>/dev/null | grep -v "sim_build\|/gold/\|\.png\|Binary"
```

Expected hits, and nothing else: `tools/test_video2seg.py` (the test that lights the old DP position, worded "decimal point"), `tools/segments.py`'s comment ("it used to be a decimal point"), `CLAUDE.md`'s note above, `test/test_multi_seg.py`'s `test_reserved_nibble_is_ignored` docstring. `-w DP` should match none of `src/`. Anything else is a missed reference: fix it, and say so.

- [ ] **Step 5: Full final run**

```bash
cd /home/matt/work/asic-workshop/shuttle-ttihp26a/multi-seg-monitor
source ~/oss-cad-suite/environment
cd tools && python3 test_video2seg.py && python3 test_palettes.py && python3 palette_builder/test_palette_builder.py && cd ..
rm -f test/results.xml
SIM=verilator make -C test 2>&1 | tail -3
! grep -q "<failure" test/results.xml && echo "SUITE CLEAN"
git status --short
```

Expected: all `ok`, `SUITE CLEAN`, and `git status --short` lists only the intended files plus the user's own `firmware/gen_mode.py` and `firmware/seg_player.py`.

- [ ] **Step 6: Commit (only if the user has asked for commits)**

```bash
git add CLAUDE.md README.md digit_shape_plan.md
git commit -m "Docs: seven segments, nibble 7 reserved

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

## What this plan does not do

- No CI re-harden or gate-level run (they happen on push), and no FPGA reflash: the board needs a fresh bitstream from this RTL to carry the change.
- No re-measurement of the delay sweep; `CLAUDE.md`'s figures are from before this change.
- No explanation of the faint DP-position dots seen on the board with a DP=0 file. Removing DP from the renderer removes those pixels; if dots survive the new bitstream, they are not DP.
