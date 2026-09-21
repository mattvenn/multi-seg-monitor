"""
Checks for the palette model in tools/segments.py, which mirrors src/palette.v
and src/palette_presets.v the same way SEGMENTS mirrors the RTL's segment
zones -- test_palette_matches_python_model in test/test_multi_seg.py checks the
RTL against it bit for bit, and this file checks the model and the presets.

The rules, from the gamma-collision lesson (src/palette.v's header comment):
intensity 0 must be black (it is also every non-segment background pixel), a
brighter stored level must never look dimmer, and entries 1-15 must stay
distinct. A tint may keep a dark floor (entry 1 black, 15 distinct levels) or
use all 16; that is a design choice per palette, not a rule. Surviving Tiny VGA's 2-bit/channel truncation
is deliberately not a rule.

    python3 test_palettes.py
"""

import ast
import os
import random

import segments

HERE = os.path.dirname(os.path.abspath(__file__))


def _all_legal_points():
    for x1 in range(15):
        for y1 in range(16):
            if x1 == 0 and y1:
                continue
            for x2 in range(x1 + 1, 16):
                for y2 in range(y1, 16):
                    yield x1, y1, x2, y2


def test_eight_presets_of_sixteen_entries():
    assert len(segments.PRESETS) == segments.N_PRESETS == 8
    assert len(segments.PALETTES) == 8
    for palette in segments.PALETTES:
        assert len(palette) == 16
        for r, g, b in palette:
            assert 0 <= r <= 15 and 0 <= g <= 15 and 0 <= b <= 15


def test_palette_zero_is_todays_grey_identity():
    """Preset 0 must reproduce the old direct-to-DAC mapping exactly, so the
    grey gold images and round-trip tests need no changes."""
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


def test_grey_keeps_all_sixteen_levels_and_tints_have_at_least_fifteen():
    # Entry 1 is not pinned to black in the tints: a palette may keep a dark
    # floor (15 distinct levels) or use all 16. Rules 1-3 are the ones above.
    assert len(set(segments.PALETTES[0])) == 16
    for n in range(1, segments.N_PRESETS):
        assert len(set(segments.PALETTES[n])) >= 15, f"palette {n}"


def test_rtl_presets_file_is_generated_from_presets_json():
    """src/palette_presets.v is generated; a hand edit, or a presets.json
    change without `palette_builder.py --write-rtl`, fails here."""
    with open(segments.PRESETS_V) as f:
        on_disk = f.read()
    assert on_disk == segments.presets_verilog(), (
        "src/palette_presets.v is stale: run tools/palette_builder/palette_builder.py --write-rtl"
    )


def test_curve_passes_through_the_knee_exactly():
    """The one promise the point editor makes: the knee a person places is
    where the chip's curve goes, whatever the slope rounding does elsewhere."""
    for x1, y1, x2, y2 in _all_legal_points():
        if x1 == 0:
            continue
        p = segments.curve_params(x1, y1, x2, y2)
        assert segments.curve_value(x1, *p) == y1, (x1, y1, x2, y2)


def test_curve_value_matches_the_formula_in_palette_v():
    """curve_value() re-derived independently from the RTL's comment (not
    its code): slopes in eighths, rounded half up, clipped at 15, index 0
    forced black. Over random raw parameters, including ones the points
    editor would never produce -- a config packet can send anything."""
    rng = random.Random(3)
    for _ in range(2000):
        kx, ky, m1, m2 = rng.randrange(16), rng.randrange(16), rng.randrange(32), rng.randrange(32)
        for i in range(16):
            if i == 0:
                want = 0
            elif i < kx:
                want = min(15, int(m1 * i / 8 + 0.5))
            else:
                want = min(15, int(ky + m2 * (i - kx) / 8 + 0.5))
            assert segments.curve_value(i, kx, ky, m1, m2) == want, (i, kx, ky, m1, m2)


def test_curve_params_rejects_what_the_chip_cannot_draw():
    for bad in ((0, 3, 5, 5), (5, 5, 5, 9), (5, 5, 4, 9), (5, 9, 10, 8), (16, 0, 15, 15), (1.5, 0, 8, 8)):
        try:
            segments.curve_params(*bad)
        except ValueError:
            continue
        raise AssertionError(f"curve_params accepted {bad}")


def test_pack_params_round_trips():
    rng = random.Random(4)
    for _ in range(200):
        params = tuple(
            (rng.randrange(16), rng.randrange(16), rng.randrange(32), rng.randrange(32)) for _ in range(3)
        )
        assert segments.unpack_params(segments.pack_params(params)) == params


def test_config_packet_layout():
    """The byte order src/config_port.v documents: header, then R, G, B,
    each {knee_x, knee_y}, m1, m2."""
    params = ((1, 2, 3, 4), (5, 6, 7, 8), (9, 10, 11, 12))
    pkt = segments.config_packet(params, pmod_type=1, cycle=True)
    assert pkt == bytes([0xA5, 0x12, 3, 4, 0x56, 7, 8, 0x9A, 11, 12])
    assert segments.config_packet(None) == bytes([0xA8])  # load_preset, header only


def _firmware_function(filename, name):
    """Pull one pure function out of a MicroPython file without importing it
    (the file imports machine/rp2, which CPython doesn't have)."""
    path = os.path.join(HERE, "..", "firmware", filename)
    with open(path) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            scope = {}
            exec(compile(ast.Module([node], []), path, "exec"), scope)
            return scope[name]
    raise AssertionError(f"{name} not found in firmware/{filename}")


def test_firmware_packet_encoder_matches_segments():
    """The firmware carries its own copy of the points -> packet encoder
    (`mpremote run` can't import tools/), so check the copy can't drift."""
    for filename in ("seg_player.py", "gen_mode.py"):
        encode = _firmware_function(filename, "config_packet")
        for curve in [tuple(p[ch] for ch in "rgb") for p in segments.PRESETS]:
            for pmod in (0, 1):
                want = segments.config_packet(
                    tuple(segments.curve_params(*pts) for pts in curve), pmod_type=pmod
                )
                assert bytes(encode(curve, pmod)) == want, (filename, curve, pmod)
        # and every legal single-channel curve, in all three positions
        grey = (8, 8, 15, 15)
        for pts in _all_legal_points():
            curve = (pts, grey, grey)
            want = segments.config_packet(tuple(segments.curve_params(*c) for c in curve))
            assert bytes(encode(curve, 0)) == want, (filename, pts)


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
