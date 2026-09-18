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
# test_palette.py's RTL-vs-Python comparison will say so.
#
# There used to be a single-channel gamma LUT here (src/gamma.v, removed):
# the PmodVGA output is a hard 4-bit DAC, 16 codes in and 16 codes out, and
# any monotonic curve reshaping 16 values into 16 values is forced by
# pigeonhole into being the identity -- a non-trivial curve can only ever
# collide two stored indices onto the same code, which is what made indices
# 14 and 15 genuinely indistinguishable on hardware (dithering_investigation.md
# on the gamma-dithering branch). A *multi-channel* palette isn't bound by
# that proof: varying hue, not just brightness, independently across R/G/B
# can place all 16 stored levels at 16 distinct points in combined-RGB space
# without any single channel being injective on its own.
#
# Two hard rules, checked by verify_palettes() below and test_palettes.py,
# apply to every palette:
#   1. All 16 entries are pairwise distinct as combined (r, g, b) triples.
#   2. Entry 0 is (0, 0, 0) -- index 0 is used both for an explicitly-zeroed
#      segment and every non-segment background/margin pixel, so a palette
#      that colours it would tint the whole background, not just dim a
#      segment.
#
# A third rule applies to palettes 1-3 only: they stay 16-way distinct even
# after Tiny VGA's 2-bit/channel truncation (src/tt_um_multi_seg_monitor.v
# keeps each channel's top 2 bits). Palette 0 is exempt on purpose -- it is
# a plain grey ramp (R=G=B=index) kept byte-identical to today's
# direct-to-DAC mapping so existing gold images and round-trip tests need no
# changes, and a single-channel-varying ramp truncated to 2 bits/channel
# inherently collapses to 4 levels; that's the original Tiny VGA prototype's
# documented behaviour (SPEC.md section 7), not a bug. Palettes 1-3 spend
# hue precisely to avoid that collapse.
#
# Palettes 1-3 are built from a bijection between the 4-bit index and a
# point in the 4x4x4 space of post-truncation (2-bit-per-channel) codes,
# using two of the three channels as a direct split of the index into its
# high/low 2-bit halves (which alone guarantees the bijection) and deriving
# the third from their XOR for a genuinely 3-channel appearance. Each
# post-truncation code C in {0,1,2,3} is then given a full-precision value
# of C*5 (0, 5, 10, 15): evenly spread across the 4-bit range, and
# (C*5) >> 2 == C for every C in {0,1,2,3}, so full-precision and
# truncated-precision agree on which entries are distinct.
def _spread(code):
    return code * 5  # 0,5,10,15 -- (code*5) >> 2 == code, see comment above


def _palette_from_split(hi_channel, lo_channel, derive_third, third_channel):
    """hi_channel/lo_channel get the index's high/low 2-bit halves directly
    (this alone makes the mapping a bijection); third_channel gets
    derive_third(hi, lo)."""
    table = [None] * 16
    for i in range(16):
        hi, lo = i >> 2, i & 3
        third = derive_third(hi, lo)
        channels = {hi_channel: hi, lo_channel: lo, third_channel: third}
        table[i] = (_spread(channels["r"]), _spread(channels["g"]), _spread(channels["b"]))
    return table


PALETTES = [
    [(i, i, i) for i in range(16)],  # 0: today's grey, unchanged (see above)
    _palette_from_split("r", "g", lambda hi, lo: hi ^ lo, "b"),  # 1
    _palette_from_split("g", "b", lambda hi, lo: hi ^ lo, "r"),  # 2
    _palette_from_split("b", "r", lambda hi, lo: hi ^ lo, "g"),  # 3
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
        assert len(set(palette)) == 16, f"palette {n} has full-precision collisions: {palette}"
        if n == 0:
            continue  # palette 0 is exempt from truncated distinctness, see above
        truncated = {(r >> 2, g >> 2, b >> 2) for r, g, b in palette}
        assert len(truncated) == 16, (
            f"palette {n} collides under 2-bit/channel truncation: {palette}"
        )

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
