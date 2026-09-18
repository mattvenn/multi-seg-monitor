"""
Tests for palette_builder's pure logic (no Tk, no display needed).

The important one is the cross-check against seg2png.render: that is the
repo's existing software model of the renderer, so if the builder's fast
LUT-based render ever disagrees with it, the builder is showing a picture the
chip would not draw.  Everything here is read-only against the rest of the
repo -- no simulator is run.

    python3 test_palette_builder.py
"""

import ast
import json
import os
import random
import re
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
import png
import seg2png
import segments


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
    # The tinted palettes (blue/green/purple) put black at entries 0 and 1 on
    # purpose and are smooth ramps, so they collapse on the 6-bit Pmod too:
    # expect exactly the 12-bit "0, 1" warning plus a 6-bit one, nothing else.
    for n in (1, 2, 3):
        warns = pb.check_palette(segments.PALETTES[n])
        assert len(warns) == 2, warns
        assert warns[0].startswith("12-bit") and warns[0].endswith("0, 1"), warns
        assert warns[1].startswith("6-bit"), warns
    warns = pb.check_palette(segments.PALETTES[0])
    assert len(warns) == 1 and "6-bit" in warns[0]  # the documented exemption


def test_check_palette_flags_collisions_and_non_black_zero():
    pal = [list(c) for c in segments.PALETTES[1]]
    pal[3] = list(pal[5])
    msgs = " | ".join(pb.check_palette(pal))
    assert "12-bit" in msgs and "3" in msgs and "5" in msgs
    pal = [list(c) for c in segments.PALETTES[1]]
    pal[0] = [1, 0, 0]
    assert any("entry 0" in m for m in pb.check_palette(pal))


def test_six_bit_only_collision_is_reported_as_six_bit_only():
    pal = [list(c) for c in segments.PALETTES[1]]
    pal[1] = [0, 0, 0 + 1]  # distinct from black at 12-bit, same after >>2
    msgs = pb.check_palette(pal)
    assert any("6-bit" in m for m in msgs)
    assert not any("12-bit" in m for m in msgs)


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


def test_export_python_round_trips():
    pal = segments.PALETTES[2]
    text = pb.to_python(pal, "mine")
    value = ast.literal_eval(text.split("=", 1)[1].strip())
    assert [tuple(c) for c in value] == [tuple(c) for c in pal]


def test_export_verilog_matches_palette_v_style_and_round_trips():
    pal = segments.PALETTES[1]
    text = pb.to_verilog(pal, 1)
    assert text.startswith("// palette 1\n2'd1: case (idx)\n")
    assert "    4'd0 : {r, g, b} = {4'd0, 4'd0, 4'd0};" in text  # padding as in src/palette.v
    assert "    4'd10: {r, g, b} =" in text
    assert text.rstrip().endswith("endcase")
    got = {}
    for m in re.finditer(r"4'd(\d+)\s*: \{r, g, b\} = \{4'd(\d+), 4'd(\d+), 4'd(\d+)\};", text):
        got[int(m[1])] = tuple(int(m[i]) for i in (2, 3, 4))
    assert [got[i] for i in range(16)] == [tuple(c) for c in pal]


def test_json_round_trip_and_validation():
    pal = segments.PALETTES[3]
    assert pb.from_json(pb.to_json(pal, "x")) == [tuple(c) for c in pal]
    for bad in ('{"entries": []}', '{"entries": [[0,0,0]]}', '{"entries": ' + json.dumps([[0, 0, 16]] * 16) + "}"):
        try:
            pb.from_json(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")


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
