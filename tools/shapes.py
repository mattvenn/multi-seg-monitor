#!/usr/bin/env python3
"""
The 7 segment cell as six numbers, for trying other proportions.

The chip draws one digit and one only: the segment rectangles in
src/multi_seg_monitor.v, mirrored on the software side by SEGMENTS in
tools/segments.py, in a 15x22 cell on a 53x27 grid.  NOTHING HERE IS IN THE
RTL.  digit() builds that same digit from a thickness and a length for each
orientation plus the distance to the next digit, and lets the cell and the
grid follow, so the proportions can be pushed around and looked at under the
zone plate (the Digit tab of tools/palette_builder) before anyone touches
Verilog.  At the default parameters it reproduces segments.SEGMENTS rectangle
for rectangle, on the same 53x27 grid, and test_palette_builder.py says so --
the chip's geometry is still written down twice, not three times.

A segment's thickness is across it, so which axis that is depends on which way
the segment runs.  One "thickness" for both would tie the two axes together
for no reason -- and hide itself while doing it, since thickening the rails
also widens the cell.  So there are two of each, and the gaps are the distance
from one digit's ink to the next one's:

    thick_h     how thick the horizontal bars a, g, d are, in y
    len_h       how long they are, in x
    thick_v     how thick the vertical rails b, c, e, f are, in x
    len_v       how long they are, in y
    gap_x       columns between one digit and the next, across
    gap_y       rows between one digit and the next, down

The segments touch, as they do on the chip, so the body is

    w = 2 * thick_v + len_h          h = 3 * thick_h + 2 * len_v

and the cell is the body plus the gap.  All six are free: the cell grows to
hold them, and the grid is what fits on screen --

    cols = min(64, 800 // cell_w)    rows = 600 // cell_h

-- centred in the 800x600 active area.  Which means these sliders do not only
change how a digit looks, they change how many there are and how fast the host
has to feed them, so grid() reports that too:

  * cols stops at 64 because the line buffer holds four rows of 256 bytes in
    1 kB and a digit is 4 bytes; a wider grid than that has nowhere to live.
  * the host has to send a byte every cell_h * 1056 / (cols * 4) pixel clocks
    -- 5808/53, about 109.58, for the chip's own cell, and whatever it comes
    to for another. It does not have to be a whole number: the RP2350 clocks the
    strobe from PIO, whose divider is 16.8 fixed point, and both ends realign
    on vsync every frame, so a fractional period neither accumulates nor
    needs tracking. What the host must do is stay inside the line buffer's
    window -- at least a row ahead of the raster and at most three -- which is
    a question of where it starts, not of the ratio being round. grid()
    reports the period and the byte rate so the PIO divider can be set.

The gaps are load bearing too: at gap_x 0 a digit's right rail touches its
neighbour's left rail and the grid reads as a mesh rather than as digits, and
the decimal point lives in the first gap column, so a digit with no gap hasn't
got one.  warnings() says so.

What makes a variant *buildable* is a different question, and validate() is
that rule.  The renderer prefetches exactly two nibbles per digit per scanline,
into slot 0 and slot 1 (src/multi_seg_monitor.v, "Line buffer and prefetch"),
and picks between them with one function of cx shared by every row:
`seg_int = (xz_right | xz_dp) ? cur_digit[7:4] : cur_digit[3:0]`.  So a cell is
legal when:

  1. it has at most 8 segments (a digit is 4 bytes), each a rectangle inside
     the cell;
  2. no pixel is claimed by two segments -- formal/ proves this today
     (FORMAL_ZONE), because overlapping zones leave a priority encoder to pick
     silently;
  3. no scanline carries more than two segments;
  4. where a scanline carries two, one lies entirely inside the cx predicate
     and the other entirely outside, and that predicate is global -- it cannot
     change from band to band.  A scanline with a single segment is
     unconstrained: both slots fetch it, the way yz_top fetches segment a into
     both slots today.

The 7 segment topology holds all four at any proportions (a alone, then f and
b, then g, then e and c, then d and DP), so validate() is here to prove a
variant rather than to reject one -- and to hand back what the RTL would need
to draw it: the cx predicate, and the cy -> (slot0, slot1) band table that is
the case in slot0_seg/slot1_seg.

    python3 tools/shapes.py        # the chip's digit and a few variants
"""

import segments

SCREEN_W, SCREEN_H = 800, 600  # the active area VgaSyncGen draws
H_TOTAL = 1056                 # pixel clocks in a scanline, 800x600@60
PIXEL_CLOCK = 40_000_000       # what the demoboard clocks the chip at
MAX_COLS = 64                  # the line buffer's wall: 4 rows x 256 B in 1 kB
NIBBLES = 8                    # 4 bytes a digit: the ceiling on segments per cell
BYTES_PER_DIGIT = segments.BYTES_PER_DIGIT

# Slider name, range, default, help -- the same shape as attract_proto.PARAMS,
# so the builder can build the sliders from it.  The defaults are the chip's
# own geometry: the fat 13x20 body in a 15x22 cell.  All six are independent
# now that the cell follows them; the maxima are where the grid stops being a
# grid (the widest cell here leaves 14 columns, the tallest 8 rows).
PARAMS = [
    ("thick_h", 1, 8, 4, "thickness of the horizontal bars a, g, d -- in y, across them"),
    ("len_h", 1, 24, 5, "length of those bars, in x"),
    ("thick_v", 1, 8, 4, "thickness of the vertical rails b, c, e, f -- in x, across them"),
    ("len_v", 1, 16, 4, "length of those rails, in y"),
    ("gap_x", 0, 16, 2, "columns between one digit's ink and the next one's, across"),
    ("gap_y", 0, 16, 2, "rows between one digit's ink and the next one's, down"),
]
DEFAULTS = {key: default for key, _lo, _hi, default, _help in PARAMS}
RANGES = {key: (lo, hi) for key, lo, hi, _d, _h in PARAMS}


class Shape:
    """One cell: up to 8 segments, each an inclusive (x0, x1, y0, y1)
    rectangle in cell coordinates, in the nibble order a, b, c, d, e, f, g, DP.
    A cell with no gap to put the decimal point in simply has 7.

    The cell size, and with it the whole grid, comes from the parameters -- a
    thicker or more widely spaced digit needs a bigger cell, and fewer of them
    fit on screen."""

    def __init__(self, segs, params=None):
        self.segs = tuple(tuple(r) for r in segs)
        self.params = dict(params or {})
        # The body is the seven bars; the decimal point sits in the gap, so it
        # must not count towards the size.
        body = self.segs[:7]
        self.w = max(r[1] for r in body) + 1
        self.h = max(r[3] for r in body) + 1
        self.gap_x = self.params.get("gap_x", 0)
        self.gap_y = self.params.get("gap_y", 0)
        self.cell_w = self.w + self.gap_x
        self.cell_h = self.h + self.gap_y
        self.cols = max(1, min(MAX_COLS, SCREEN_W // self.cell_w))
        self.rows = max(1, SCREEN_H // self.cell_h)
        self.margin_x = (SCREEN_W - self.cols * self.cell_w) // 2
        self.margin_y = (SCREEN_H - self.rows * self.cell_h) // 2

    def __len__(self):
        return len(self.segs)

    @property
    def key(self):
        """Hashable identity, for caching a render of this exact cell."""
        return self.segs + ((self.cell_w, self.cell_h),)

    def name(self, seg):
        return segments.SEGMENTS[seg][0]

    def rect(self, seg):
        return self.segs[seg]

    def sample(self, seg):
        """Where the generator samples this segment, as a (ox, oy) offset from
        the cell's top left: the centre of its rectangle, rounded down.  That
        is what attract_proto.SEG_CX/SEG_CY and src/zoneplate.v's {ox, oy}
        table hold today, and in RTL it stays col * cell_w plus a constant."""
        x0, x1, y0, y1 = self.segs[seg]
        return (x0 + x1) // 2, (y0 + y1) // 2

    def is_dark(self, seg):
        """The generator lights every segment but the decimal point, which has
        no sensible place in a picture -- and a cell with no room for one has
        nothing to light at all."""
        return seg >= len(self) or seg == 7

    def pixels(self, col, row, seg):
        """Screen rectangle covered by one segment of one cell: the same thing
        segments.segment_pixels() returns for the chip's own digit."""
        x0, x1, y0, y1 = self.segs[seg]
        x = self.margin_x + col * self.cell_w
        y = self.margin_y + row * self.cell_h
        return x + x0, x + x1, y + y0, y + y1

    @property
    def digits(self):
        return self.cols * self.rows

    @property
    def row_bytes(self):
        return self.cols * BYTES_PER_DIGIT

    @property
    def frame_bytes(self):
        return self.rows * self.row_bytes

    @property
    def byte_rate(self):
        """Bytes a second the host sends while a row is being drawn, at the
        40 MHz pixel clock: what the PIO divider is set from.  It need not be
        a round number of clocks -- see the module docstring."""
        return float(PIXEL_CLOCK / self.clocks_per_byte)

    @property
    def clocks_per_byte(self):
        """Pixel clocks the host gets per byte, as an exact fraction: a digit
        row is cell_h scanlines of H_TOTAL clocks, and row_bytes bytes."""
        from fractions import Fraction

        return Fraction(self.cell_h * H_TOTAL, self.row_bytes)

    def owner(self):
        """{(cx, cy): segment} for every lit-able pixel of the cell.  Raises
        ValueError on a rectangle outside the cell or a pixel claimed twice
        (rules 1 and 2)."""
        out = {}
        for seg, (x0, x1, y0, y1) in enumerate(self.segs):
            if not (0 <= x0 <= x1 < self.cell_w and 0 <= y0 <= y1 < self.cell_h):
                raise ValueError(
                    f"segment {self.name(seg)} rectangle {(x0, x1, y0, y1)} is not "
                    f"inside the {self.cell_w}x{self.cell_h} cell"
                )
            for cx in range(x0, x1 + 1):
                for cy in range(y0, y1 + 1):
                    if (cx, cy) in out:
                        raise ValueError(
                            f"segments {self.name(out[(cx, cy)])} and {self.name(seg)} both "
                            f"claim pixel {(cx, cy)} -- FORMAL_ZONE would fail"
                        )
                    out[(cx, cy)] = seg
        return out


def settings(**params):
    """Fill in whatever a caller left out from the chip's own geometry, and
    refuse anything off the sliders."""
    out = dict(DEFAULTS, **params)
    for key, value in out.items():
        if key not in RANGES:
            raise ValueError(f"no such parameter {key!r}")
        lo, hi = RANGES[key]
        if not (isinstance(value, int) and lo <= value <= hi):
            raise ValueError(f"{key}={value!r} is outside {lo}..{hi}")
    return out


def digit(**params):
    """The 7 segment cell at these proportions.  Every combination of the six
    is drawable -- the cell grows to hold them -- so this raises only for a
    parameter off its slider."""
    p = settings(**params)
    th, lh = p["thick_h"], p["len_h"]
    tv, lv = p["thick_v"], p["len_v"]
    w = 2 * tv + lh
    xl0, xl1 = 0, tv - 1             # f, e: the left rail
    xm0, xm1 = tv, tv + lh - 1       # a, g, d: the bars
    xr0, xr1 = w - tv, w - 1         # b, c: the right rail
    ya0, ya1 = 0, th - 1             # a
    yu0, yu1 = th, th + lv - 1       # f, b
    yg0, yg1 = yu1 + 1, yu1 + th     # g
    yl0, yl1 = yg1 + 1, yg1 + lv     # e, c
    yd0, yd1 = yl1 + 1, yl1 + th     # d, DP
    segs = [
        (xm0, xm1, ya0, ya1),  # a
        (xr0, xr1, yu0, yu1),  # b
        (xr0, xr1, yl0, yl1),  # c
        (xm0, xm1, yd0, yd1),  # d
        (xl0, xl1, yl0, yl1),  # e
        (xl0, xl1, yu0, yu1),  # f
        (xm0, xm1, yg0, yg1),  # g
    ]
    # The decimal point is a dot in the first gap column, which is where a real
    # display puts it -- so a digit with no gap to its neighbour simply hasn't
    # got one, and its nibble goes unused. One pixel wide whatever the rails
    # are: it is a point, not a bar, and that is what the chip draws.
    if p["gap_x"] >= 1:
        segs.append((w, w, yd0, yd1))
    return Shape(segs, p)


CHIP = digit()  # the geometry the RTL actually has


def grid(shape):
    """What this cell does to the picture and to the host, as text lines. The
    sliders change how many digits there are and how fast they have to be fed,
    not just how one looks."""
    cpb = shape.clocks_per_byte
    would_fit = SCREEN_W // shape.cell_w
    capped = f", {would_fit} would fit but the line buffer holds {MAX_COLS}" \
        if would_fit > MAX_COLS else ""
    lines = [
        f"cell {shape.cell_w}x{shape.cell_h} "
        f"(body {shape.w}x{shape.h} + gap {shape.gap_x}x{shape.gap_y})",
        f"grid {shape.cols}x{shape.rows} = {shape.digits} digits{capped}, "
        f"{shape.frame_bytes} bytes a frame, margins {shape.margin_x}/{shape.margin_y}",
        f"host: a byte every {float(cpb):.2f} pixel clocks"
        + ("" if cpb.denominator == 1 else f" ({cpb.numerator}/{cpb.denominator})")
        + f" = {shape.byte_rate:,.0f} B/s, {shape.frame_bytes * 60:,} B/s a frame",
    ]
    return lines


def warnings(shape):
    """What is about to go wrong with these proportions; [] means clean.
    Legal, and the picture may even be what's wanted, but worth knowing."""
    msgs = []
    if shape.gap_x <= 0:
        msgs.append(
            "gap_x is 0: a digit's right rail touches its neighbour's left rail, so the "
            "grid reads as a mesh rather than as digits -- and there is nowhere to put "
            "the decimal point, so nibble 7 goes unused"
        )
    elif shape.gap_x == 1:
        msgs.append("the decimal point fills the only gap column, so it touches the next digit")
    if shape.gap_y <= 0:
        msgs.append(
            "gap_y is 0: a bottom bar touches the top bar of the row below, so the rows "
            "run together"
        )
    # A cell_h that isn't a power of two used to be listed here: the renderer
    # sliced cy and row straight out of y_px, which only worked at 16.  It
    # doesn't any more -- src/multi_seg_monitor.v counts both, because the
    # chip's own cell is 22 tall -- so every height costs the same now and
    # there is nothing left to warn about.
    return msgs


def _row_segments(own, cell_h, cell_w):
    """cy -> {segment: the cx it covers on that scanline}."""
    rows = {}
    for (cx, cy), seg in own.items():
        rows.setdefault(cy, {}).setdefault(seg, set()).add(cx)
    return [rows.get(cy, {}) for cy in range(cell_h)]


def _predicate(shape, rows):
    """The cx predicate, as a bitmask over the cell's columns: set means that
    column selects slot 1 (the high nibble), i.e. this cell's
    `xz_right | xz_dp`.

    A pair on one scanline has to land on opposite sides of it, and the
    predicate is one function of cx for the whole cell, so every segment in
    such a pair must be all on one side.  That is a 2-colouring: segments that
    share a column must agree, segments that share a scanline must differ.  An
    odd cycle means no predicate exists.

    (This used to be a search over all 2^12 masks, which was fine while the
    cell was fixed at 12 wide.  It isn't now.)"""
    cols_of = {}
    for (cx, cy), seg in shape.owner().items():
        cols_of.setdefault(seg, set()).add(cx)
    pairs = [(cy, sorted(segs)) for cy, segs in enumerate(rows) if len(segs) == 2]
    constrained = {s for _cy, pair in pairs for s in pair}

    # Segments sharing a column must take the same side: merge them first.
    parent = {s: s for s in constrained}

    def find(s):
        while parent[s] != s:
            parent[s] = parent[parent[s]]
            s = parent[s]
        return s

    owner_of_col = {}
    for seg in sorted(constrained):
        for cx in cols_of[seg]:
            if cx in owner_of_col:
                parent[find(seg)] = find(owner_of_col[cx])
            owner_of_col[cx] = seg

    adjacency = {}
    for _cy, (a, b) in pairs:
        ra, rb = find(a), find(b)
        if ra == rb:
            raise ValueError(
                f"segments {shape.name(a)} and {shape.name(b)} share a scanline but also a "
                "column, so no cx predicate can tell them apart"
            )
        adjacency.setdefault(ra, set()).add(rb)
        adjacency.setdefault(rb, set()).add(ra)

    side = {}
    for root in sorted(adjacency):
        if root in side:
            continue
        side[root] = 0
        stack, group = [root], [root]
        while stack:
            cur = stack.pop()
            for nxt in sorted(adjacency[cur]):
                if nxt not in side:
                    side[nxt] = 1 - side[cur]
                    stack.append(nxt)
                    group.append(nxt)
                elif side[nxt] == side[cur]:
                    raise ValueError(
                        "no single cx predicate separates the two segments on every "
                        "scanline, and the slot select is shared by all rows"
                    )
        # Orient the component so the right-hand side is slot 1, as the chip's
        # own xz_right | xz_dp is.
        first = {0: [], 1: []}
        for root2 in group:
            for seg in constrained:
                if find(seg) == root2:
                    first[side[root2]] += sorted(cols_of[seg])[:1]
        if first[0] and first[1] and min(first[0]) > min(first[1]):
            for root2 in group:
                side[root2] = 1 - side[root2]

    mask = 0
    assigned = {}
    for seg in constrained:
        for cx in cols_of[seg]:
            assigned[cx] = side[find(seg)]
    # Columns no constrained segment covers are free -- a scanline with one
    # segment fetches it into both slots -- so give them their nearest
    # assigned neighbour's side, which keeps the predicate to as few runs of
    # cx as possible, i.e. as few comparators.
    for cx in range(shape.cell_w):
        bit = assigned.get(cx)
        if bit is None and assigned:
            bit = assigned[min(assigned, key=lambda c: (abs(c - cx), c))]
        mask |= (bit or 0) << cx
    return mask


_VALIDATED = {}


def validate(shape):
    """Check rules 1-4 and return (mask, bands) -- what the RTL needs.

    `mask` is the cx predicate (see _predicate).  `bands` is a list of
    (y0, y1, slot0, slot1) covering every scanline, which is the case in
    slot0_seg/slot1_seg: a band with one segment names it in both slots, an
    empty one has (None, None).

    Raises ValueError, naming the offending segments, for anything illegal.
    Memoised on the cell, because the builder wants it on every change."""
    if shape.key in _VALIDATED:
        return _VALIDATED[shape.key]
    if len(shape) > NIBBLES:
        raise ValueError(f"{len(shape)} segments, but a digit is only {NIBBLES} nibbles")
    own = shape.owner()  # rules 1 and 2
    rows = _row_segments(own, shape.cell_h, shape.cell_w)

    crowded = [cy for cy, segs in enumerate(rows) if len(segs) > 2]
    if crowded:
        detail = "; ".join(
            f"cy {cy}: {', '.join(shape.name(s) for s in sorted(rows[cy]))}" for cy in crowded
        )
        raise ValueError(
            f"more than two segments on a scanline, and the prefetch fetches two ({detail})"
        )

    mask = _predicate(shape, rows)  # rule 4

    per_row = []
    for cy, segs in enumerate(rows):
        if not segs:
            per_row.append((None, None))
        elif len(segs) == 1:
            seg = next(iter(segs))
            per_row.append((seg, seg))  # both slots fetch it
        else:
            a, b = sorted(segs)
            hi = a if all((mask >> cx) & 1 for cx in rows[cy][a]) else b
            lo = b if hi == a else a
            if any((mask >> cx) & 1 for cx in rows[cy][lo]):
                raise ValueError(
                    f"cy {cy}: {shape.name(lo)} straddles the cx predicate, so part of the "
                    "row would read the other nibble"
                )
            per_row.append((lo, hi))
    bands = []
    for cy, slots in enumerate(per_row):
        if bands and bands[-1][2:] == list(slots):
            bands[-1][1] = cy
        else:
            bands.append([cy, cy, *slots])
    _VALIDATED[shape.key] = (mask, [tuple(b) for b in bands])
    return _VALIDATED[shape.key]


def mask_text(shape, mask):
    """The cx predicate as a person would read it: the columns that select the
    high nibble."""
    cols = [cx for cx in range(shape.cell_w) if (mask >> cx) & 1]
    if not cols:
        return "never (no scanline needs both slots)"
    runs = []
    for cx in cols:
        if runs and runs[-1][1] == cx - 1:
            runs[-1][1] = cx
        else:
            runs.append([cx, cx])
    return "cx in " + ", ".join(f"{a}" if a == b else f"{a}..{b}" for a, b in runs)


def band_text(shape, bands):
    """The band table as one line, in cy order, naming segments the way the
    RTL comments do."""
    def slot(v):
        return "-" if v is None else shape.name(v)

    return " ".join(
        (f"{y0}" if y0 == y1 else f"{y0}-{y1}") + f":({slot(s0)},{slot(s1)})"
        for y0, y1, s0, s1 in bands
    )


def call_text(params):
    """How to get this cell back, for copying out of the builder."""
    return "digit(" + ", ".join(f"{k}={params[k]}" for k, *_ in PARAMS) + ")"


def ascii_cell(shape, cells=1):
    """The cell as text, one character per pixel, `cells` of them across and
    down so the gaps between neighbours show."""
    own = shape.owner()
    out = []
    for _ in range(cells):
        for cy in range(shape.cell_h):
            row = "".join(
                shape.name(own[(cx, cy)])[0] if (cx, cy) in own else "."
                for cx in range(shape.cell_w)
            )
            out.append(row * cells)
    return "\n".join(out)


def _show(label, shape):
    mask, bands = validate(shape)
    print(f"== {label}: {call_text(shape.params)}")
    for line in grid(shape):
        print(f"   {line}")
    print(f"   {len(shape)}/{NIBBLES} nibbles, slot 1 when {mask_text(shape, mask)}")
    print(f"   bands cy:(slot0,slot1)  {band_text(shape, bands)}")
    for msg in warnings(shape):
        print(f"   ! {msg}")
    for line in ascii_cell(shape, cells=2).splitlines():
        print("   " + line)
    print()


def main():
    _show("the chip", CHIP)
    for params in (dict(gap_x=4, gap_y=4),
                   dict(thick_h=1, thick_v=1, len_h=8, len_v=6),
                   dict(thick_h=4, thick_v=4, len_h=8, len_v=6, gap_x=3, gap_y=3)):
        _show("variant", digit(**params))


if __name__ == "__main__":
    main()
