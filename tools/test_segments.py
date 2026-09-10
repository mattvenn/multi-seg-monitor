#!/usr/bin/env python3
"""
Checks for segments.py's shared tables. Run directly: ./test_segments.py
"""

import segments


def test_gamma_is_true_6bit_precision():
    """
    GAMMA stores the gamma curve rounded directly to 6-bit precision, not
    pre-rounded to 4-bit and scaled up. On hardware (2026-09-10 bring-up) even
    the 12 distinct values the old 4-bit-rounded table produced were hard to
    tell apart near the bright end, so tt_um_multi_seg_monitor.v now dithers
    the bottom 2 bits instead of discarding them -- which only helps if those
    bits carry a real fraction rather than always being zero.
    """
    for idx in range(16):
        want = round((idx / 15) ** (1 / 2.2) * 63)
        got = segments.GAMMA[idx]
        assert got == want, f"idx {idx}: GAMMA[idx] = {got}, want {want}"


def test_dither_fraction_matches_remainder():
    """
    Across one 2x2 pixel block (one full period of x and y parity), the
    number of pixels that round up to base+1 must equal the level's
    remainder exactly -- that's what makes dithering reproduce the true
    fractional brightness on average rather than approximate it.
    """
    for level in range(64):
        base = level >> 2
        rem = level & 0x3
        got = [segments.dither(level, x, y) for y in (0, 1) for x in (0, 1)]
        ups = sum(1 for v in got if v == base + 1)
        downs = sum(1 for v in got if v == base)
        assert ups + downs == 4, f"level {level}: unexpected codes {got}"
        expected_ups = 0 if base == 0xF else rem
        assert ups == expected_ups, (
            f"level {level}: {ups} of 4 rounded up, want {expected_ups}"
        )


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"{name} ok")
