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


def test_uniform_cells():
    """A cell filled with one value gives every segment that value."""
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
            want = (col * 7 + row * 3) % 16
            for seg in range(8):
                assert got[seg, row, col] == want, (
                    f"segment {segments.SEGMENTS[seg][0]} of ({col}, {row}): "
                    f"got {got[seg, row, col]}, want {want}"
                )


def test_segment_isolation():
    """Light one segment's rectangle and only that segment should respond."""
    for target in range(8):
        linear = np.zeros((segments.GRID_H, segments.GRID_W))
        x0, x1, y0, y1 = segments.segment_pixels(0, 0, target)
        # segment_pixels includes the left margin; the converter works on the
        # grid alone, so take it back off.
        linear[y0 : y1 + 1, x0 - segments.MARGIN_X : x1 - segments.MARGIN_X + 1] = 1.0

        got = video2seg.frame_to_segments(linear)
        for seg in range(8):
            want = 15 if seg == target else 0
            assert got[seg, 0, 0] == want, (
                f"lit {segments.SEGMENTS[target][0]}, "
                f"{segments.SEGMENTS[seg][0]} read {got[seg, 0, 0]}, want {want}"
            )


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
