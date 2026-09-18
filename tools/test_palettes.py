"""
Checks for the palette table (tools/segments.py's PALETTES), which mirrors
src/palette.v the same way SEGMENTS mirrors the RTL's segment zones -- if one
changes the other must too, or test_palette.py's RTL-vs-Python comparison
will say so.

These are the two hard invariants src/palette.v's header comment documents:
every palette must keep all 16 stored intensities distinct in the combined
RGB output, both at full precision and after Tiny VGA's 2-bit/channel
truncation, and must map intensity 0 to black (index 0 is also every
non-segment background/margin pixel, not just an explicitly dark segment).
"""

import segments


def test_four_palettes_of_sixteen_entries():
    assert len(segments.PALETTES) == 4
    for palette in segments.PALETTES:
        assert len(palette) == 16
        for r, g, b in palette:
            assert 0 <= r <= 15 and 0 <= g <= 15 and 0 <= b <= 15


def test_palette_zero_is_todays_grey_identity():
    """Palette 0 must reproduce the existing direct-to-DAC mapping exactly,
    so the pre-existing gold images and round-trip tests need no changes."""
    for i in range(16):
        assert segments.PALETTES[0][i] == (i, i, i)


def test_every_palette_maps_zero_to_black():
    for n, palette in enumerate(segments.PALETTES):
        assert palette[0] == (0, 0, 0), f"palette {n} index 0: {palette[0]}"


def test_every_palette_is_sixteen_way_distinct_at_full_precision():
    for n, palette in enumerate(segments.PALETTES):
        assert len(set(palette)) == 16, f"palette {n} has collisions: {palette}"


def test_colour_palettes_are_sixteen_way_distinct_after_tiny_vga_truncation():
    """Tiny VGA mode truncates each channel to its top 2 bits in the wrapper
    (src/tt_um_multi_seg_monitor.v). A palette only distinct before that
    truncation would silently collapse levels under Tiny VGA, the same class
    of bug the removed src/gamma.v had -- so palettes 1-3 (which spend hue,
    not just brightness, and exist partly to recover this) must survive it.

    Palette 0 is deliberately exempt: it is the plain grey ramp kept
    byte-identical to today's direct-to-DAC mapping for backward
    compatibility (see test_palette_zero_is_todays_grey_identity), and a
    pure grey ramp truncated to 2 bits/channel inherently collapses to 4
    levels -- that is the original historical Tiny VGA behaviour this
    project shipped with, not a regression, and no amount of table-choosing
    can avoid it for a single-channel-varying ramp. Selecting palette 0
    under Tiny VGA reproduces that historical 4-level result on purpose.
    """
    for n, palette in enumerate(segments.PALETTES):
        if n == 0:
            continue
        truncated = {(r >> 2, g >> 2, b >> 2) for r, g, b in palette}
        assert len(truncated) == 16, (
            f"palette {n} collides under 2-bit/channel truncation: "
            f"{[(r >> 2, g >> 2, b >> 2) for r, g, b in palette]}"
        )


def test_verify_palettes_accepts_the_real_table():
    segments.verify_palettes()  # raises on any violation


def test_verify_palettes_rejects_a_collision():
    bad = [list(p) for p in segments.PALETTES]
    bad[1][3] = bad[1][5]  # force a full-precision collision
    try:
        segments.verify_palettes(bad)
    except AssertionError:
        pass
    else:
        raise AssertionError("verify_palettes() accepted a colliding palette")


def test_verify_palettes_rejects_nonblack_zero():
    bad = [list(p) for p in segments.PALETTES]
    bad[2][0] = (1, 0, 0)
    try:
        segments.verify_palettes(bad)
    except AssertionError:
        pass
    else:
        raise AssertionError("verify_palettes() accepted a non-black index 0")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"{name} ok")
