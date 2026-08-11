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

# Only the top 4 bits (level[5:2] in tt_um_multi_seg_monitor.v) ever reach the
# panel, so each entry is round((i/15) ** (1/2.2) * 15) scaled back up by 4 --
# not round((i/15) ** (1/2.2) * 63) independently rounded at 6-bit precision and
# then truncated, which double-rounds and silently loses extra levels on top of
# the ones the gamma curve's own compression at the bright end already costs.
# Must match src/gamma.v.
GAMMA = [0, 16, 24, 28, 32, 36, 40, 44, 44, 48, 48, 52, 56, 56, 60, 60]


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
