"""
Checks for the palette table (tools/segments.py's PALETTES), which mirrors
src/palette.v the same way SEGMENTS mirrors the RTL's segment zones -- if one
changes the other must too, or test_palette_matches_python_table in
test/test_multi_seg.py will say so.

The rules, from the gamma-collision lesson (src/palette.v's header comment):
intensity 0 must be black (it is also every non-segment background pixel), a
brighter stored level must never look dimmer, and entries 1-15 must stay
distinct. Palettes 1-3 (blue, green, purple, from tools/palette_builder) set
entry 1 to black on purpose, so they have 15 distinct levels rather than 16.
Surviving Tiny VGA's 2-bit/channel truncation is deliberately not a rule.
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


def test_brightness_never_falls_as_the_index_rises():
    for n, palette in enumerate(segments.PALETTES):
        lum = [sum(c) for c in palette]
        assert all(a <= b for a, b in zip(lum, lum[1:])), f"palette {n}: {lum}"


def test_entries_one_to_fifteen_are_distinct():
    for n, palette in enumerate(segments.PALETTES):
        assert len(set(palette[1:])) == 15, f"palette {n} has collisions: {palette}"


def test_grey_keeps_all_sixteen_levels_and_tints_have_fifteen():
    assert len(set(segments.PALETTES[0])) == 16
    for n in (1, 2, 3):
        assert len(set(segments.PALETTES[n])) == 15, f"palette {n}"
        assert segments.PALETTES[n][1] == (0, 0, 0), f"palette {n} entry 1"


def test_tinted_palettes_match_palette_builder_files():
    """The palettes are designed in tools/palette_builder/palettes/*.json and
    copied into segments.py; this catches a re-export that wasn't pasted."""
    import json
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    for n, name in ((1, "blue"), (2, "green"), (3, "purple")):
        path = os.path.join(here, "palette_builder", "palettes", f"{name}.json")
        with open(path) as f:
            entries = [tuple(e) for e in json.load(f)["entries"]]
        assert segments.PALETTES[n] == entries, f"palette {n} differs from {name}.json"


def test_verify_palettes_accepts_the_real_table():
    segments.verify_palettes()  # raises on any violation


def test_verify_palettes_rejects_a_collision():
    bad = [list(p) for p in segments.PALETTES]
    bad[1][5] = bad[1][7]  # collision among entries 1-15 (also breaks the ramp order)
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


def test_verify_palettes_rejects_a_dimmer_higher_index():
    bad = [list(p) for p in segments.PALETTES]
    bad[3][9], bad[3][10] = bad[3][10], bad[3][9]
    try:
        segments.verify_palettes(bad)
    except AssertionError:
        pass
    else:
        raise AssertionError("verify_palettes() accepted a non-monotonic palette")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"{name} ok")
