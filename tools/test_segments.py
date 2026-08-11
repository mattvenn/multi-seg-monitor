#!/usr/bin/env python3
"""
Checks for segments.py's shared tables. Run directly: ./test_segments.py
"""

import segments


def test_gamma_truncates_without_extra_rounding_error():
    """
    GAMMA is stored 6-bit (for a possible future 6-bit path -- see
    resolution_discussion.md section 6.2) but only the top 4 bits ever reach the
    panel: tt_um_multi_seg_monitor.v truncates with level[5:2]. Each entry must
    equal the gamma curve rounded once directly to 4-bit precision, not a 6-bit
    rounding that then gets chopped -- the double rounding is what silently
    collapsed 16 stored levels into 12 distinct ones on the panel.
    """
    for idx in range(16):
        want = round((idx / 15) ** (1 / 2.2) * 15)
        got = segments.GAMMA[idx] >> 2
        assert got == want, f"idx {idx}: GAMMA[idx] >> 2 = {got}, want {want}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"{name} ok")
