"""
Display geometry and frame packing, shared by the video converter and the tests.

This is the one place the segment rectangles are written down on the software
side. They mirror `src/multi_seg_monitor.v` and SPEC.md section 1.1 -- if the RTL
layout changes, change it here too or the round-trip test will say so.
"""

# Grid -- 800x600 mode, docs/superpowers/specs/2026-08-11-800x600-mode-design.md
COLS, ROWS = 64, 37
CELL_W, CELL_H = 12, 16
MARGIN_X = 16  # (800 - COLS*CELL_W) / 2
MARGIN_Y = 4  # (600 - ROWS*CELL_H) / 2 -- 600 doesn't divide evenly by CELL_H
GRID_W = COLS * CELL_W  # 768
GRID_H = ROWS * CELL_H  # 592

BYTES_PER_DIGIT = 4
ROW_BYTES = COLS * BYTES_PER_DIGIT  # 256 -- the line buffer's wall
FRAME_BYTES = ROWS * ROW_BYTES  # 9472

# Colour palette -- mirrors src/palette.v the same way SEGMENTS mirrors the
# RTL's segment zones. If one changes the other must too, or
# test_multi_seg.py's RTL-vs-Python comparison (test_palette_matches_python_table)
# will say so.
#
# Palette 0 is the plain grey ramp (R=G=B=index), byte-identical to the old
# direct-to-DAC mapping, so existing gold images and round-trip tests are
# unchanged. Palettes 1-3 are tinted brightness ramps designed in
# tools/palette_builder (blue, green, purple), copied here from its
# palettes/*.json.
#
# There used to be a single-channel gamma LUT here (src/gamma.v, removed):
# the PmodVGA output is a hard 4-bit DAC, 16 codes in and 16 codes out, and
# any monotonic curve reshaping 16 values into 16 values is forced by
# pigeonhole into being the identity -- a non-trivial curve can only ever
# collide two stored indices onto the same code, which is what made indices
# 14 and 15 genuinely indistinguishable on hardware (dithering_investigation.md
# on the gamma-dithering branch). That is the failure to keep out of the
# palettes, and it bounds what these ones can do:
#
# Rules checked by verify_palettes() below and tools/test_palettes.py:
#   1. Entry 0 is (0, 0, 0) -- index 0 is used both for an explicitly-zeroed
#      segment and every non-segment background/margin pixel, so a palette
#      that colours it would tint the whole background, not just dim a
#      segment.
#   2. Brightness (r+g+b) never decreases with the index, so a brighter
#      stored level never looks dimmer.
#   3. Entries 1-15 are pairwise distinct. Palette 0 must also differ from
#      entry 0 everywhere; palettes 1-3 deliberately set entry 1 to black
#      too, so stored level 1 is indistinguishable from off in those three
#      (15 distinct levels, not 16) -- a designed dark floor, not a collision
#      to fix.
#
# Deliberately NOT a rule any more: surviving Tiny VGA's 2-bit/channel
# truncation (src/tt_um_multi_seg_monitor.v keeps each channel's top 2 bits).
# A smooth single-hue ramp cannot, so on the Tiny VGA Pmod the tinted
# palettes show 7-9 distinct levels (blue 8, green 9, purple 7) and grey 4.
PALETTES = [
    [(i, i, i) for i in range(16)],  # 0: grey
    [  # 1: blue
        (0, 0, 0),
        (0, 0, 0),
        (0, 1, 2),
        (0, 2, 4),
        (0, 3, 6),
        (0, 4, 9),
        (0, 5, 11),
        (0, 6, 13),
        (0, 8, 15),
        (2, 9, 15),
        (4, 10, 15),
        (6, 11, 15),
        (9, 12, 15),
        (11, 13, 15),
        (13, 14, 15),
        (15, 15, 15),
    ],
    [  # 2: green
        (0, 0, 0),
        (0, 0, 0),
        (0, 2, 1),
        (0, 4, 3),
        (0, 6, 4),
        (0, 8, 5),
        (0, 10, 7),
        (0, 12, 8),
        (0, 14, 9),
        (2, 14, 10),
        (4, 14, 11),
        (6, 14, 12),
        (9, 15, 13),
        (11, 15, 13),
        (13, 15, 14),
        (15, 15, 15),
    ],
    [  # 3: purple
        (0, 0, 0),
        (0, 0, 0),
        (2, 0, 2),
        (4, 0, 4),
        (6, 0, 6),
        (9, 0, 9),
        (11, 0, 11),
        (13, 0, 13),
        (15, 0, 15),
        (15, 2, 15),
        (15, 4, 15),
        (15, 6, 15),
        (15, 9, 15),
        (15, 11, 15),
        (15, 13, 15),
        (15, 15, 15),
    ],
]


def verify_palettes(palettes=None):
    """Raises AssertionError if any palette violates the rules above."""
    if palettes is None:
        palettes = PALETTES
    assert len(palettes) == 4, f"expected 4 palettes, got {len(palettes)}"
    for n, palette in enumerate(palettes):
        assert len(palette) == 16, f"palette {n} has {len(palette)} entries, not 16"
        for r, g, b in palette:
            assert 0 <= r <= 15 and 0 <= g <= 15 and 0 <= b <= 15, (
                f"palette {n} has an out-of-range channel value: {(r, g, b)}"
            )
        assert palette[0] == (0, 0, 0), f"palette {n} index 0 is {palette[0]}, not black"
        lum = [sum(c) for c in palette]
        assert all(a <= b for a, b in zip(lum, lum[1:])), (
            f"palette {n} gets dimmer as the index rises: {palette}"
        )
        assert len(set(palette[1:])) == 15, f"palette {n} has collisions among entries 1-15"
        if n == 0:
            assert len(set(palette)) == 16, "palette 0 must keep all 16 levels distinct"


# Segment rectangles within a cell, as (name, x0, x1, y0, y1) inclusive.
# Index order is the nibble order of a digit word: a, b, c, d, e, f, g, DP.
# The digit body is 10x14; column 11 and rows 14-15 are the gaps that keep
# neighbouring digits from merging.
SEGMENTS = [
    ("a", 2, 7, 0, 1),
    ("b", 8, 9, 2, 5),
    ("c", 8, 9, 8, 11),
    ("d", 2, 7, 12, 13),
    ("e", 0, 1, 8, 11),
    ("f", 0, 1, 2, 5),
    ("g", 2, 7, 6, 7),
    ("DP", 10, 10, 12, 13),
]

def pack_digit(intensities):
    """Eight 4-bit segment intensities -> 4 bytes, low nibble first."""
    assert len(intensities) == 8
    return bytes(
        (intensities[2 * k + 1] << 4) | (intensities[2 * k] & 0xF) for k in range(4)
    )


def unpack_digit(data):
    """4 bytes -> eight 4-bit segment intensities."""
    assert len(data) == 4
    out = []
    for byte in data:
        out.append(byte & 0xF)
        out.append(byte >> 4)
    return out


def digit_offset(col, row):
    """Byte offset of a digit within a frame."""
    return (row * COLS + col) * BYTES_PER_DIGIT


def segment_pixels(col, row, seg_index):
    """Screen pixel rectangle covered by one segment, as (x0, x1, y0, y1)."""
    _, sx0, sx1, sy0, sy1 = SEGMENTS[seg_index]
    x = MARGIN_X + col * CELL_W
    y = MARGIN_Y + row * CELL_H
    return x + sx0, x + sx1, y + sy0, y + sy1


def segment_centre(col, row, seg_index):
    """A pixel guaranteed to be inside the segment -- used for sampling."""
    x0, x1, y0, y1 = segment_pixels(col, row, seg_index)
    return (x0 + x1) // 2, (y0 + y1) // 2
