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
