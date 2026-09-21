"""
Tests for palette_builder's pure logic (no Tk, no display needed).

The important one is the cross-check against seg2png.render: that is the
repo's existing software model of the renderer, so if the builder's fast
LUT-based render ever disagrees with it, the builder is showing a picture the
chip would not draw.  Everything here is read-only against the rest of the
repo -- no simulator is run.

    python3 test_palette_builder.py
"""

import json
import os
import random
import sys
import tempfile

import numpy as np
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
# tools/ (for segments / png / seg2png) goes in first so HERE ends up ahead of it:
# with tools/ in front, `import palette_builder` would resolve to this directory
# as a namespace package instead of palette_builder.py.
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import palette_builder as pb
from pathlib import Path
import png
import seg2png
import segments


HERE_P = Path(HERE)


def _random_frame(seed=1):
    rng = random.Random(seed)
    return bytes(rng.randrange(256) for _ in range(segments.FRAME_BYTES))


def test_render_matches_seg2png_for_every_palette_and_depth():
    frame = _random_frame()
    idx = pb.index_image_from_frame(frame)
    for n, palette in enumerate(segments.PALETTES):
        for bits, levels in ((12, 16), (6, 4)):
            want = bytes(seg2png.render(frame, levels, n))
            got = pb.render(idx, palette, bits).tobytes()
            assert got == want, f"palette {n}, {bits}-bit differs from seg2png"


def test_index_image_puts_each_nibble_on_its_segment():
    frame = bytearray(segments.FRAME_BYTES)
    # digit (col 3, row 5), segment "c" (index 2) = intensity 9: byte 1's low nibble
    off = segments.digit_offset(3, 5)
    frame[off + 1] = 0x09
    idx = pb.index_image_from_frame(bytes(frame))
    x0, x1, y0, y1 = segments.segment_pixels(3, 5, 2)
    assert (idx[y0 : y1 + 1, x0 : x1 + 1] == 9).all()
    assert int((idx != 0).sum()) == (x1 - x0 + 1) * (y1 - y0 + 1)


def test_index_image_from_grey_png_round_trips_through_palette_zero():
    frame = _random_frame(2)
    grey = seg2png.render(frame, 16, 0)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "g.png")
        png.write_png(path, 800, 600, grey)
        idx = pb.index_image_from_png(path)
    assert (idx == pb.index_image_from_frame(frame)).all()


def test_index_image_from_png_rejects_a_non_ramp_image():
    px = bytearray(800 * 600 * 3)
    px[0:3] = bytes((200, 10, 10))  # coloured pixel: not a stored-intensity capture
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "c.png")
        png.write_png(path, 800, 600, px)
        try:
            pb.index_image_from_png(path)
        except ValueError:
            return
    raise AssertionError("accepted an image that is not a 16-level grey ramp")


def test_lut_arithmetic_matches_the_two_pmods():
    pal = [(0, 0, 0)] + [(15, 8, 3)] * 15
    assert pb.palette_to_lut(pal, 12)[1].tolist() == [255, 136, 51]
    assert pb.palette_to_lut(pal, 6)[1].tolist() == [255, 170, 0]  # 15>>2=3, 8>>2=2, 3>>2=0


def test_real_palettes_pass_and_grey_flags_only_six_bit():
    # The tints are smooth ramps, so they collapse on the 6-bit Pmod too. A
    # tint may also keep a dark floor (entry 1 black), which is the one 12-bit
    # warning allowed: exactly "0, 1", and nothing else.
    for n in range(1, segments.N_PRESETS):
        warns = pb.check_palette(segments.PALETTES[n])
        assert warns and warns[-1].startswith("6-bit"), (n, warns)
        for w in warns[:-1]:
            assert w.startswith("12-bit") and w.endswith("0, 1"), (n, warns)
        assert len(warns) <= 2, (n, warns)
    warns = pb.check_palette(segments.PALETTES[0])
    assert len(warns) == 1 and "6-bit" in warns[0]  # the documented exemption


def test_check_palette_flags_collisions_non_black_zero_and_dimming():
    pal = [list(c) for c in segments.PALETTES[1]]
    pal[3] = list(pal[5])
    msgs = " | ".join(pb.check_palette(pal))
    assert "12-bit" in msgs and "3" in msgs and "5" in msgs
    pal = [list(c) for c in segments.PALETTES[1]]
    pal[0] = [1, 0, 0]
    assert any("entry 0" in m for m in pb.check_palette(pal))
    pal = [list(c) for c in segments.PALETTES[3]]
    pal[9], pal[10] = pal[10], pal[9]
    assert any("brightness falls at entries: 10" in m for m in pb.check_palette(pal))


def test_six_bit_only_collision_is_reported_as_six_bit_only():
    pal = [list(c) for c in segments.PALETTES[1]]
    pal[1] = [0, 0, 0 + 1]  # distinct from black at 12-bit, same after >>2
    msgs = pb.check_palette(pal)
    assert any("6-bit" in m for m in msgs)
    assert not any("12-bit" in m for m in msgs)


def test_curve_table_is_the_chips_quantised_curve():
    for preset, table in zip(segments.PRESETS, segments.PALETTES):
        assert pb.curve_table(preset) == table


def test_fit_curve_recovers_every_preset_exactly():
    """Any table a curve can draw, fit_curve finds a curve that draws it --
    not necessarily the same points, but the same 16 entries."""
    for table in segments.PALETTES:
        assert pb.curve_table(pb.fit_curve(table)) == table


def test_fit_curve_on_the_old_tables_is_close():
    """The pre-curve blue/green/purple tables (kept in testdata/, since
    palettes/ now holds curves) still open, fitted. Every channel lands
    within 1 of the old value at every entry."""
    for name in ("blue", "green", "purple"):
        path = HERE_P / "testdata" / f"legacy_{name}.json"
        curve, fitted = pb.from_json(path.read_text())
        assert fitted
        old = json.loads(path.read_text())["entries"]
        got = pb.curve_table(curve)
        worst = max(abs(a - b) for e, g in zip(old, got) for a, b in zip(e, g))
        assert worst <= 1, (name, worst)


def test_json_round_trip_and_validation():
    curve = {ch: list(segments.PRESETS[7][ch]) for ch in "rgb"}
    assert pb.from_json(pb.to_json(curve, "x")) == (curve, False)
    bad_table = '{"entries": ' + json.dumps([[0, 0, 16]] * 16) + "}"
    bad_curve = '{"r": [0, 3, 5, 5], "g": [8, 8, 15, 15], "b": [8, 8, 15, 15]}'
    for bad in ('{"entries": []}', '{"entries": [[0,0,0]]}', bad_table, bad_curve, '{"r": [1, 2]}'):
        try:
            pb.from_json(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")


def test_save_preset_and_write_rtl_round_trip():
    """Saving into a slot keeps the file loadable and one-preset-per-line,
    and the RTL generated from it matches what segments.py would generate."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "presets.json")
        with open(segments.PRESETS_JSON) as f:
            original = f.read()
        with open(path, "w") as f:
            f.write(original)
        curve = {"r": [4, 2, 15, 15], "g": [14, 0, 15, 0], "b": [14, 0, 15, 0]}
        pb.save_preset(curve, 5, "test", path=path)
        presets = segments.load_presets(path)
        assert presets[5] == {"name": "test", **curve}
        assert [p for i, p in enumerate(presets) if i != 5] == [
            p for i, p in enumerate(segments.PRESETS) if i != 5
        ]
        with open(path) as f:
            assert len(f.read().splitlines()) == len(original.splitlines())
        # The checked-in file round-trips byte for byte through save_preset.
        pb.save_preset(segments.PRESETS[5], 5, segments.PRESETS[5]["name"], path=path)
        with open(path) as f:
            assert f.read() == original
        try:
            pb.save_preset({"r": [0, 3, 5, 5], "g": curve["g"], "b": curve["b"]}, 0, "bad", path=path)
        except ValueError:
            pass
        else:
            raise AssertionError("save_preset accepted a curve the chip can't draw")


def test_export_carries_a_working_packet():
    curve = {ch: list(segments.PRESETS[4][ch]) for ch in "rgb"}
    text = pb.export_text(curve, "amber")
    packet = segments.config_packet(segments.points_to_params(curve))
    assert packet.hex(" ") in text
    assert "main(curve=((1, 0, 8, 15), (1, 0, 15, 14), (10, 0, 15, 5)))" in text


def test_list_clips_keeps_only_current_geometry():
    with tempfile.TemporaryDirectory() as d:
        for name, frames in (("good.seg", 3), ("also.seg", 1)):
            with open(os.path.join(d, name), "wb") as f:
                f.write(bytes(segments.FRAME_BYTES * frames))
        with open(os.path.join(d, "stale.seg"), "wb") as f:  # 640x480-era size
            f.write(bytes(936000))
        open(os.path.join(d, "note.txt"), "w").close()
        clips = pb.list_clips(d)
    assert [(os.path.basename(p), n) for p, n in clips] == [("also.seg", 1), ("good.seg", 3)]


def test_read_frame_bounds():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "a.seg")
        with open(path, "wb") as f:
            f.write(bytes([1]) * segments.FRAME_BYTES + bytes([2]) * segments.FRAME_BYTES)
        assert pb.read_frame(path, 1)[0] == 2
        try:
            pb.read_frame(path, 2)
        except IndexError:
            return
    raise AssertionError("read past the end of a clip")


def test_curve_end_sits_on_the_edge_and_every_slope_is_reachable():
    """The end handle is drawn on the plot's edge, and dragging it can reach
    every second slope the chip can store -- including the ~11% (steep ones)
    with no whole-number point on the edge."""
    for x1 in range(15):
        for y1 in range(16):
            if x1 == 0 and y1:
                continue
            for m2, p in pb.slope_choices(x1, y1):
                assert segments.curve_params(x1, y1, *p)[3] == m2
                ex, ey = pb.curve_end(x1, y1, *p)
                assert ex == 15 or ey == 15, (x1, y1, p, ex, ey)
                # Aiming the drag straight at a slope's own end lands on it.
                got = pb.end_toward(x1, y1, ex, ey)
                assert segments.curve_params(x1, y1, *got)[3] == m2, (x1, y1, p, got)
    # And every built-in preset keeps its colours when its end is re-picked.
    for preset in segments.PRESETS:
        for ch in "rgb":
            x1, y1, x2, y2 = preset[ch]
            again = pb.end_nearest(x1, y1, *pb.curve_end(x1, y1, x2, y2))
            assert segments.curve_params(x1, y1, *again) == segments.curve_params(x1, y1, x2, y2)


def test_rgb_drag_moves_every_channel_by_the_same_delta():
    """With RGB selected, dragging one channel's handle moves all three and
    keeps the offsets between them: the channel under the cursor lands exactly
    where a single-channel drag would, the others by the same delta."""
    start = {"r": [4, 4, 10, 12], "g": [6, 2, 11, 9], "b": [2, 7, 7, 15]}
    for pts in start.values():
        segments.curve_params(*pts)  # the fixture itself has to be drawable
    for to in ((5, 5), (3, 3), (7, 7), (4, 4)):
        out = pb.drag_handles(start, 0, "r", to)
        dx, dy = to[0] - start["r"][0], to[1] - start["r"][1]
        assert tuple(out["r"][:2]) == to  # the grabbed channel follows the cursor
        for ch in "rgb":
            assert out[ch][:2] == [start[ch][0] + dx, start[ch][1] + dy], (ch, to)
            segments.curve_params(*out[ch])  # and each is still one the chip can draw
    # The end handle: the grabbed channel lands where the aim says, which for
    # a single channel is the drag the editor has always done.
    x1, y1 = start["g"][:2]
    for to in ((15.0, 4.0), (9.5, 15.0), (15.0, 15.0)):
        out = pb.drag_handles(start, 1, "g", to)
        assert out["g"] == pb.legal_points([x1, y1, *pb.end_toward(x1, y1, *to)])
        for ch in "rgb":
            assert out[ch][:2] == start[ch][:2]  # an end drag leaves the knees alone
            segments.curve_params(*out[ch])


def test_a_channel_against_the_edge_springs_back():
    """Dragging RGB into a corner clips the channel that gets there first, but
    the offsets come back when the cursor does -- because every step is
    measured from where the drag began, not from the step before."""
    start = {"r": [2, 3, 8, 12], "g": [5, 6, 9, 13], "b": [8, 9, 12, 15]}
    far = pb.drag_handles(start, 0, "r", (11, 6))  # b's knee would be at x=17
    assert far["b"][0] == 14  # clipped, not 17
    back = pb.drag_handles(start, 0, "r", (2, 3))  # all the way back
    for ch in "rgb":
        assert back[ch][:2] == start[ch][:2], ch


def test_effect_index_image_matches_attract_proto():
    """The builder evaluates an effect over the whole frame as numpy arrays;
    attract_proto.sample() is the scalar reference. They must agree segment
    for segment, at the defaults and at an arbitrary legal setting."""
    import attract_proto

    rng = random.Random(3)
    for name, spec in attract_proto.PARAMS.items():
        settings = [attract_proto.default_params(name),
                    {key: rng.randint(lo, hi) for key, lo, hi, _d, _h in spec}]
        for params in settings:
            frame = rng.randrange(pb.EFFECT_FRAMES)
            idx = pb.index_image_from_effect(name, frame, params)
            ref = attract_proto.sample(attract_proto.EFFECTS[name](frame, **params), per_digit=False)
            for _ in range(200):
                row, col, seg = rng.randrange(segments.ROWS), rng.randrange(segments.COLS), rng.randrange(segments.NUM_SEGMENTS)
                x, y = segments.segment_centre(col, row, seg)
                assert idx[y, x] == ref[row][col][seg], (name, params, row, col, seg)
            assert idx.max() <= 15
            mask, _ = pb._segment_tables()
            off = np.ones(pb.HEIGHT * pb.WIDTH, dtype=bool)
            off[mask] = False
            assert not idx.ravel()[off].any(), f"{name} lit a background pixel"


def test_effect_change_fades_through_black():
    import attract_proto as ap

    half = ap.FADE_FRAMES // 2
    fades = [ap.fade_factor(k) for k in range(ap.FADE_FRAMES + 1)]
    assert fades[0] == 15 and fades[-1] == 15
    assert fades[half - 1] == 0 and fades[half] == 0  # black at the switch
    assert all(a >= b for a, b in zip(fades[:half], fades[1:half]))  # only ever down...
    assert all(a <= b for a, b in zip(fades[half:], fades[half + 1 :]))  # ...then only up
    assert set(fades[:half]) == set(range(16))  # every level on the way
    assert not ap.fade_showing_new(half - 1) and ap.fade_showing_new(half)
    for level in range(16):
        assert ap.apply_fade(level, 15) == level and ap.apply_fade(level, 0) == 0
    params = ap.default_params("plasma")
    full = pb.index_image_from_effect("plasma", 100, params)
    assert (pb.index_image_from_effect("plasma", 100, params, fade=15) == full).all()
    assert not pb.index_image_from_effect("plasma", 100, params, fade=0).any()
    half_lit = pb.index_image_from_effect("plasma", 100, params, fade=7)
    assert (half_lit <= full).all() and half_lit.max() < full.max()


def test_effect_param_defaults_are_in_range():
    import attract_proto

    for name, spec in attract_proto.PARAMS.items():
        for key, lo, hi, default, help_text in spec:
            assert lo <= default <= hi, (name, key)
            assert help_text
        # The function's own defaults are the PARAMS defaults, so the CLI,
        # the builder's Defaults button and a bare call all agree.
        import inspect

        sig = inspect.signature(attract_proto.EFFECTS[name]).parameters
        assert {k: v.default for k, v in sig.items() if k != "frame"} == attract_proto.default_params(name), name


def test_chip_effect_drives_its_own_palette():
    """The chip effect is the whole chip, colour included: the preview
    follows ui_in[3:1] and the automatic change rather than the curve being
    edited. The change must land while the screen is black, which is the
    whole reason the fade is there."""
    import attract_proto
    import segments

    p = attract_proto.default_params("chip")
    # The sliders are the pins, in the pins' polarity: a board with every
    # switch off is all zeroes, and that is the full attract mode.
    assert p == {"palette": 0, "manual": 0, "steady_phase": 0, "steady_drift": 0}

    # Automatic: preset 0 for the first 1024 frames, then 1, then 2 -- counted
    # from frame_ctr's reset value of 64, not from 0.
    assert [attract_proto.chip_preset(f, **p) for f in (0, 959, 960, 1983, 1984)] == [0, 0, 1, 1, 2]
    # ...and both frames either side of each change are fully black.
    for f in (959, 960, 1983, 1984):
        assert attract_proto.chip_fade(attract_proto.CHIP_RESET_FRAME + f) == 0, f

    # A start other than 0 shifts the whole cycle, so a board strapped and
    # switched to the same palette agrees with itself.
    assert [attract_proto.chip_preset(f, palette=5) for f in (0, 960, 3008)] == [5, 6, 0]

    # Manual holds one palette and never fades: nothing is changing, so there
    # is nothing to hide.
    held = dict(p, manual=1, palette=3)
    assert {attract_proto.chip_preset(f, **held) for f in (0, 959, 960, 5000)} == {3}
    for f in (0, 959, 960, 5000):
        ctr = attract_proto.CHIP_RESET_FRAME + f
        assert attract_proto.chip_fade(ctr, auto=not held["manual"]) == 15, f

    # Every preset the effect can name must be one the chip really has.
    for n in range(8):
        pb.palette_to_lut(segments.curve_palette(segments.PRESET_PARAMS[n]), 12)


# --------------------------------------------------------------------------
# Digit proportions (tools/shapes.py).  Nothing here is in the RTL -- the chip
# has one set of segment rectangles on one 64x37 grid -- so what these guard
# is that a variant is buildable at all, that the cell and the grid really
# follow the sliders, that the builder draws what it is asked for, and that
# the defaults are still the chip's own geometry.
# --------------------------------------------------------------------------


def _sample_params(rng, n):
    """`n` random settings, plus each slider at both ends with the rest at the
    chip's values.  Every combination is drawable now that the cell follows
    the numbers, so this is a sample for cost, not a filter."""
    import shapes

    out = [dict(shapes.DEFAULTS)]
    for _ in range(n):
        out.append({key: rng.randint(lo, hi) for key, lo, hi, _d, _h in shapes.PARAMS})
    for key, lo, hi, _d, _h in shapes.PARAMS:
        for v in (lo, hi):
            out.append(dict(shapes.DEFAULTS, **{key: v}))
    return out


def test_every_setting_is_one_the_prefetch_can_draw():
    """validate() is the hardware's rules, not a style check: two nibbles per
    scanline, split by one cx predicate shared by every row. The 7 segment
    topology holds them at any proportions, so this says so at a few dozen --
    and says it by checking the bands, which is what the RTL would encode."""
    import shapes

    rng = random.Random(11)
    names = [n for n, *_ in segments.SEGMENTS]
    for params in _sample_params(rng, 40):
        cell = shapes.digit(**params)
        mask, bands = shapes.validate(cell)
        own = cell.owner()  # raises on an overlap or a rectangle outside the cell
        assert bands[0][0] == 0 and bands[-1][1] == cell.cell_h - 1, (params, bands)
        for a, b in zip(bands, bands[1:]):
            assert b[0] == a[1] + 1, (params, bands)
        # Every pixel must read the slot its own segment was fetched into, or
        # the renderer shows the other nibble for part of the row.
        for y0, y1, s0, s1 in bands:
            for cy in range(y0, y1 + 1):
                for cx in range(cell.cell_w):
                    seg = own.get((cx, cy))
                    if seg is not None:
                        assert seg == (s1 if (mask >> cx) & 1 else s0), (params, cx, cy)
        # ...and the bands are the classic five, whatever the proportions.
        # Unordered: which of a pair lands in slot 1 is the predicate's
        # choice, and on a narrow body the cheapest predicate is the one that
        # names the left rail rather than the right.
        lit = {frozenset((names[s0], names[s1])) for _y0, _y1, s0, s1 in bands if s0 is not None}
        want = {frozenset(p) for p in (("a",), ("f", "b"), ("g",), ("e", "c"), ("d",))}
        assert lit == want, (params, lit)
        # One sample point each, or two nibbles would track the same level.
        pts = [cell.sample(s) for s in range(len(cell)) if not cell.is_dark(s)]
        assert len(set(pts)) == len(pts), (params, pts)
        for ox, oy in pts:
            assert 0 <= ox < cell.cell_w and 0 <= oy < cell.cell_h, params


def test_no_slider_position_is_one_the_cell_cannot_hold():
    """The complaint that started this: settings that didn't work. Now that
    the cell follows the numbers, every position of every slider has to be a
    digit -- each one swept over its whole range, from the chip's settings and
    from a few random ones."""
    import shapes

    rng = random.Random(12)
    starts = [dict(shapes.DEFAULTS)] + [
        {key: rng.randint(lo, hi) for key, lo, hi, _d, _h in shapes.PARAMS} for _ in range(5)
    ]
    for start in starts:
        for key, lo, hi, _d, _h in shapes.PARAMS:
            for v in range(lo, hi + 1):
                cell = shapes.digit(**dict(start, **{key: v}))
                assert cell.params[key] == v, (start, key, v)
                assert cell.cols >= 1 and cell.rows >= 1, (start, key, v)
                assert cell.cols * cell.cell_w <= shapes.SCREEN_W
                assert cell.rows * cell.cell_h <= shapes.SCREEN_H
                shapes.validate(cell)


def test_a_parameter_off_its_slider_is_refused():
    import shapes

    for params in (dict(thick_h=0), dict(len_h=99), dict(gap_x=-1), dict(nonsense=1)):
        try:
            shapes.digit(**params)
        except ValueError:
            continue
        raise AssertionError(f"{params} was accepted, but it is off the slider")


def test_the_cell_and_the_grid_follow_the_settings():
    """The cell is the body plus the gap, and the grid is what fits on screen
    -- which is why these sliders change how many digits there are and how
    fast the host has to feed them, not just how one looks."""
    import shapes

    rng = random.Random(13)
    for params in _sample_params(rng, 30):
        cell = shapes.digit(**params)
        assert cell.w == 2 * params["thick_v"] + params["len_h"], params
        assert cell.h == 3 * params["thick_h"] + 2 * params["len_v"], params
        assert (cell.cell_w, cell.cell_h) == (cell.w + params["gap_x"], cell.h + params["gap_y"])
        assert cell.cols == max(1, min(shapes.MAX_COLS, shapes.SCREEN_W // cell.cell_w)), params
        assert cell.rows == max(1, shapes.SCREEN_H // cell.cell_h), params
        assert cell.margin_x == (shapes.SCREEN_W - cell.cols * cell.cell_w) // 2
        assert cell.margin_y == (shapes.SCREEN_H - cell.rows * cell.cell_h) // 2
        assert cell.row_bytes == cell.cols * 4 and cell.row_bytes <= 256  # the line buffer's wall
        assert cell.frame_bytes == cell.rows * cell.cols * 4
        assert cell.clocks_per_byte == Fraction(cell.cell_h * 1056, cell.row_bytes)

    # The chip's own numbers, which are where all of those came from.
    chip = shapes.CHIP
    assert (chip.cell_w, chip.cell_h) == (segments.CELL_W, segments.CELL_H)
    assert (chip.cols, chip.rows) == (segments.COLS, segments.ROWS)
    assert (chip.margin_x, chip.margin_y) == (segments.MARGIN_X, segments.MARGIN_Y)
    assert chip.frame_bytes == segments.FRAME_BYTES
    # The pacing ratio, exact as a fraction and not as an integer: 22 * 1056
    # over 212.  The host's PIO divider is 16.8 fixed point and both ends
    # realign on vsync, so the remainder never accumulates.
    assert chip.clocks_per_byte == Fraction(5808, 53)
    assert shapes.warnings(chip) == []


def test_the_gaps_are_the_distance_between_digits():
    """gap_x and gap_y are what a person looks at -- how far apart the digits
    sit -- so measure them where it counts: dark pixels between one digit's
    ink and the next one's, on a frame with every segment lit."""
    import shapes

    rng = random.Random(14)
    for params in _sample_params(rng, 8):
        cell = shapes.digit(**params)
        # Every nibble lit, the reserved one included: it has no segment to draw.
        frame = b"\xff" * cell.frame_bytes
        idx = pb.index_image_from_frame(frame, cell)
        if cell.cols < 3 or cell.rows < 3:
            continue
        # A horizontal cut through the upper rails, a vertical one through the
        # left rail: both cross every cell.
        x0 = cell.margin_x + cell.cell_w
        y0 = cell.margin_y + cell.cell_h
        # Across a rail: rail, the bars' width of nothing, rail, then the gap.
        row_cut = idx[y0 + cell.sample(5)[1], x0 : x0 + cell.cell_w]
        assert int((row_cut == 0).sum()) == params["len_h"] + params["gap_x"], \
            (params, list(row_cut))
        # Down the bars: bar, rail height of nothing, bar, likewise, bar, gap.
        col_cut = idx[y0 : y0 + cell.cell_h, x0 + cell.sample(0)[0]]
        assert int((col_cut == 0).sum()) == 2 * params["len_v"] + params["gap_y"], \
            (params, list(col_cut))
        # ...and specifically, the dark run between this digit's ink and the
        # next one's is the gap, in both directions. That is what the slider
        # means, and it is measured here on the pixels rather than on the
        # arithmetic that produced them.
        across = idx[y0 + cell.sample(1)[1], x0 + cell.w : x0 + cell.cell_w]
        down = idx[y0 + cell.h : y0 + cell.cell_h, x0 + cell.sample(0)[0]]
        assert len(across) == params["gap_x"] and not across.any(), (params, list(across))
        assert len(down) == params["gap_y"] and not down.any(), (params, list(down))


def test_the_predicate_is_the_one_a_brute_force_search_would_find():
    """_predicate() 2-colours the segments instead of searching every mask,
    because the cell isn't 12 wide any more and 2^cell_w stopped being a
    number one can enumerate. On the cells where it still is, the two have to
    agree about what is legal."""
    import shapes

    rng = random.Random(15)
    checked = 0
    for params in _sample_params(rng, 30):
        cell = shapes.digit(**params)
        if cell.cell_w > 14:
            continue
        checked += 1
        mask, _bands = shapes.validate(cell)
        own = cell.owner()
        rows = shapes._row_segments(own, cell.cell_h, cell.cell_w)
        pairs = [(cy, sorted(segs)) for cy, segs in enumerate(rows) if len(segs) == 2]

        def ok(m):
            for cy, (a, b) in pairs:
                ina = {(m >> cx) & 1 for cx in rows[cy][a]}
                inb = {(m >> cx) & 1 for cx in rows[cy][b]}
                if len(ina) > 1 or len(inb) > 1 or ina == inb:
                    return False
            return True

        assert ok(mask), (params, bin(mask))
        assert any(ok(m) for m in range(1 << cell.cell_w))  # sanity: the search agrees one exists
    assert checked >= 5, checked


def test_validate_rejects_a_cell_the_prefetch_could_not_draw():
    """The 7 segment topology can't produce these, but validate() is what
    would have to catch them if the layout were ever changed."""
    import shapes

    bad = {
        "overlap": [(0, 5, 0, 5), (4, 8, 4, 8)],
        # Three regions on one scanline, and the prefetch fetches two.
        "three_on_a_row": [(0, 3, 6, 9), (4, 7, 6, 9), (8, 11, 6, 9)],
        # Legal row by row, but the two rows want opposite predicates, and the
        # slot select is shared by every row.
        "no_one_predicate": [(0, 3, 0, 1), (4, 11, 0, 1), (0, 7, 4, 5), (8, 11, 4, 5)],
        # Two scanline pairs, (a, b) on rows 0-1 and (c, d) on rows 2-3, where c
        # spans columns a and b both occupy (3 is a's, 4-5 are b's). Segments
        # sharing a column must sit on the same side of the cx predicate, and a
        # and b are a pair that must sit on opposite sides: no predicate can do
        # both, whatever the cell size.
        "same_columns": [(0, 3, 0, 1), (4, 7, 0, 1), (3, 5, 2, 3), (8, 9, 2, 3)],
        "outside_the_cell": [(-1, 3, 0, 1)],
        "too_many": [(0, 0, r, r) for r in range(9)],
    }
    for name, segs in bad.items():
        try:
            shapes.validate(shapes.Shape(segs))
        except ValueError:
            continue
        raise AssertionError(f"validate() accepted {name}, which the prefetch cannot draw")


def test_the_default_proportions_are_the_chips_own_geometry():
    """shapes.CHIP has to be segments.SEGMENTS rectangle for rectangle, and
    its sample points the table in src/zoneplate.v. If either drifts the
    builder is previewing a digit the chip doesn't draw."""
    import re

    import attract_proto
    import shapes

    cell = shapes.CHIP
    assert cell.params == shapes.DEFAULTS
    assert len(cell) == segments.NUM_SEGMENTS == 7
    for seg, (name, x0, x1, y0, y1) in enumerate(segments.SEGMENTS):
        assert cell.rect(seg) == (x0, x1, y0, y1), name
        assert cell.pixels(3, 5, seg) == segments.segment_pixels(3, 5, seg), name
        assert cell.sample(seg) == (attract_proto.SEG_CX[seg], attract_proto.SEG_CY[seg]), name
    # Nibble 7 is reserved: nothing to light. Every real segment is lit.
    assert cell.is_dark(7) and not any(cell.is_dark(s) for s in range(7))

    rtl = (Path(__file__).resolve().parents[2] / "src" / "zoneplate.v").read_text()
    table = {int(n): (int(ox), int(oy)) for n, ox, oy in
             re.findall(r"3'd(\d):\s+\{ox, oy\} = \{4'd(\d+),\s+5'd(\d+)\}", rtl)}
    default = re.search(r"default: \{ox, oy\} = \{4'd(\d+),\s+5'd(\d+)\}", rtl)
    table[6] = (int(default.group(1)), int(default.group(2)))  # g; 7 is never sampled
    assert len(table) == 7, table
    for seg, point in table.items():
        assert cell.sample(seg) == point, (seg, point, cell.sample(seg))


def test_warnings_name_the_costs_and_not_the_ratios():
    """warnings() is for what a setting costs -- gaps that merge the grid --
    and not for a byte period that happens to be fractional, nor for a cell
    height that isn't a power of two. The host clocks the strobe from a PIO
    divider and both ends realign on vsync, so a period like 5808/53 costs
    nothing; and the renderer counts cy and row rather than slicing them out
    of y_px, so every height costs the same. Saying otherwise would rule out
    most glyphs for no reason."""
    import shapes

    touching = shapes.digit(gap_x=0)
    assert len(touching) == 7
    assert any("mesh" in m for m in shapes.warnings(touching))
    assert shapes.warnings(shapes.digit(gap_x=1)) == []  # one gap column is enough now
    assert any("run together" in m for m in shapes.warnings(shapes.digit(gap_y=0)))

    # Neither a fractional byte period nor an odd cell height is a complaint.
    for params in (dict(gap_y=3), dict(thick_h=2, len_h=6, thick_v=2, len_v=4),
                   shapes.DEFAULTS):
        assert shapes.warnings(shapes.digit(**params)) == [], params

    fractional = shapes.digit(**shapes.DEFAULTS)
    assert fractional.clocks_per_byte.denominator != 1
    assert fractional.byte_rate > 0
    # ...and the one wall that is real: the line buffer holds 64 digits a row.
    assert fractional.cols <= shapes.MAX_COLS and fractional.row_bytes <= 256


def test_index_image_draws_the_proportions_it_is_asked_for():
    """Each nibble lands on its own segment's pixels, at any proportions, and
    nothing lands anywhere else. The default stays byte-identical to what it
    was before the sliders existed, which is what keeps the seg2png
    cross-check and the gold images honest."""
    import shapes

    frame = _random_frame(4)
    assert (pb.index_image_from_frame(frame, shapes.CHIP) == pb.index_image_from_frame(frame)).all()
    rng = random.Random(16)
    for params in _sample_params(rng, 6):
        cell = shapes.digit(**params)
        data = bytes(rng.randrange(256) for _ in range(cell.frame_bytes))
        nibbles = np.frombuffer(data, dtype=np.uint8)
        idx = pb.index_image_from_frame(data, cell)
        spots = {(0, 0), (cell.cols - 1, cell.rows - 1), (cell.cols // 2, cell.rows // 2)}
        for col, row in spots:
            for seg in range(len(cell)):
                byte = nibbles[(row * cell.cols + col) * 4 + seg // 2]
                want = (byte >> 4) if seg % 2 else (byte & 0xF)
                x0, x1, y0, y1 = cell.pixels(col, row, seg)
                assert (idx[y0 : y1 + 1, x0 : x1 + 1] == want).all(), (params, col, row, seg)
        lit = sum((x1 - x0 + 1) * (y1 - y0 + 1) for x0, x1, y0, y1 in cell.segs)
        mask, _ = pb._segment_tables(cell)
        assert len(mask) == lit * cell.digits, params


def test_a_frame_for_another_grid_is_refused_rather_than_drawn_as_noise():
    import shapes

    wide = shapes.digit(gap_x=8, gap_y=8)
    assert wide.frame_bytes != segments.FRAME_BYTES
    try:
        pb.index_image_from_frame(_random_frame(5), wide)
    except ValueError as e:
        assert "bytes" in str(e), e
        return
    raise AssertionError("a chip-sized frame was accepted for a different grid")


def test_effect_index_image_follows_the_sample_points():
    """The level a segment shows is the effect at that segment's sample point
    -- the one pixel of it the zone plate decides -- and the reserved nibble
    stays dark."""
    import attract_proto
    import shapes

    rng = random.Random(7)
    params = attract_proto.default_params("zoneplate")
    frame = 137
    f = attract_proto.EFFECTS["zoneplate"](frame, **params)
    for proportions in _sample_params(rng, 4):
        cell = shapes.digit(**proportions)
        idx = pb.index_image_from_effect("zoneplate", frame, params, shape=cell)
        for _ in range(60):
            col, row = rng.randrange(cell.cols), rng.randrange(cell.rows)
            seg = rng.randrange(len(cell))
            ox, oy = cell.sample(seg)
            want = 0 if cell.is_dark(seg) else f(col * cell.cell_w + ox, row * cell.cell_h + oy)
            x0, x1, y0, y1 = cell.pixels(col, row, seg)
            assert (idx[y0 : y1 + 1, x0 : x1 + 1] == want).all(), (proportions, col, row, seg)
        mask, _ = pb._segment_tables(cell)
        off = np.ones(pb.HEIGHT * pb.WIDTH, dtype=bool)
        off[mask] = False
        assert not idx.ravel()[off].any(), f"{proportions} lit a background pixel"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"{name} ok")
