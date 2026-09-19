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
    # The tinted presets put black at entries 0 and 1 on purpose and are
    # smooth ramps, so they collapse on the 6-bit Pmod too: expect exactly the
    # 12-bit "0, 1" warning plus a 6-bit one, nothing else.
    for n in range(1, segments.N_PRESETS):
        warns = pb.check_palette(segments.PALETTES[n])
        assert len(warns) == 2, (n, warns)
        assert warns[0].startswith("12-bit") and warns[0].endswith("0, 1"), warns
        assert warns[1].startswith("6-bit"), warns
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
    """The pre-curve blue/green/purple tables in palettes/ still open, fitted.
    Every channel lands within 1 of the old value at every entry."""
    for name in ("blue", "green", "purple"):
        curve, fitted = pb.from_json((HERE_P / "palettes" / f"{name}.json").read_text())
        assert fitted
        old = json.loads((HERE_P / "palettes" / f"{name}.json").read_text())["entries"]
        got = pb.curve_table(curve)
        worst = max(abs(a - b) for e, g in zip(old, got) for a, b in zip(e, g))
        assert worst <= 1, (name, worst)


def test_gradient_endpoints_and_spacing():
    pal = pb.gradient_palette(["#000000", "#ffffff"])
    assert pal[0] == (0, 0, 0)  # entry 0 stays black no matter the stops
    assert pal[1] == (0, 0, 0) and pal[15] == (15, 15, 15)
    assert all(pal[i][0] <= pal[i + 1][0] for i in range(1, 15))  # monotonic ramp
    three = pb.gradient_palette(["#ff0000", "#00ff00", "#0000ff"])
    assert three[1] == (15, 0, 0) and three[8] == (0, 15, 0) and three[15] == (0, 0, 15)


def test_gradient_rejects_bad_input():
    for bad in ([], ["#fff"], ["red", "blue"], ["#ff0000"]):
        try:
            pb.gradient_palette(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")


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
    assert "main(curve=((1, 0, 8, 15), (1, 0, 15, 10), (10, 0, 15, 5)))" in text


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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"{name} ok")
