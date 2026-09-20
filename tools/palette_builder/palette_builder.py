#!/usr/bin/env python3
"""
Interactive palette builder for the Multi Segment Monitor.

    ./palette_builder.py              # the editor
    ./palette_builder.py --write-rtl  # regenerate src/palette_presets.v

A palette is three per-channel curves (see "Colour palettes" in
tools/segments.py): each a line from (0, 0) to a knee, then on through a
second point until it clips at 15. The plot shows the start (fixed at black),
the knee and the end -- where the line meets the plot's edge -- and you drag
the knee and the end of the selected channel -- or, with RGB selected, of all
three channels at once, which moves them together and keeps the offsets
between them -- and see the result on a frame of the generator, of a droplet
clip, or of an attract-mode effect, as the 12-bit PmodVGA (4 bits/channel) or
the 6-bit Tiny VGA Pmod (2 bits/channel) would show it. Hovering the preview
magnifies the square under the cursor 3x, which is how a segment a pixel out
is spotted. What's drawn is always the curve after the chip's own quantisation
-- slopes in eighths, rounded as the RTL rounds -- never the idealised line.

Effects come from tools/attract_proto.py (plasma, zone plate, interference, and
`chip`, which is what the silicon actually draws -- its sliders are the chip's
four DIP switch fields, and it colours itself from ui_in[3:1] rather than from
the curve being edited).

The left column has three tabs, Colour (the palette curves), Pattern (the
effect and its own tuning sliders, rebuilt when you switch effect) and Digit
(the segment proportions themselves); all three act on the one preview. Play
runs an effect at the chip's real 60 fps. The parameter line under the sliders
is the matching attract_proto.py command-line, to reproduce a setting or carry
it into RTL.

The Digit tab is for a question the chip has not answered: its segment
rectangles are one set of proportions, and on a big screen the grid can read
as a tiling rather than as digits. The sliders are a thickness and a length
for each orientation plus the gap to the next digit, and the cell and the grid
follow them (tools/shapes.py) -- so a variant changes how many digits there
are and how fast the host has to feed them, which the tab reports alongside
the hardware's own rules. Only the chip's own proportions can be shown on the
Generator and Droplet sources -- a capture and a .seg file are both bytes for
the chip's 64x37 grid -- so a variant is judged under an effect, which is
computed rather than stored, and is what it would have to look good in
anyway.

What it writes:
  - palettes/<name>.json: your own curves (Save / Open). Old 16-entry table
    files still open: they're fitted to the nearest curve.
  - presets.json: the chip's 8 built-in palettes (Save to preset slot).
  - src/palette_presets.v: regenerated from presets.json (Write RTL, or
    --write-rtl). Unlike the old table-based version of this tool, it does
    edit src/ -- the presets are data, the generated file is not meant to be
    hand-edited, and tools/test_palettes.py fails if the two ever disagree.
  - exports/<name>.txt: the config packet bytes and the firmware call that
    loads the palette at runtime without touching the RTL (Export).

The render is a software model, the same maths as seg2png.py (which
test_palette_builder.py cross-checks it against), not a simulation of the RTL.
The 6-bit path truncates the *looked-up colour* to its top 2 bits, because that
is where src/tt_um_multi_seg_monitor.v does it -- so the same palette edits
show up differently on the two Pmods, which is the point of the toggle.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))  # tools/, for segments and png

import attract_proto  # noqa: E402
import png  # noqa: E402
import segments  # noqa: E402
import shapes  # noqa: E402

WIDTH, HEIGHT = 800, 600
GENERATOR_PNG = REPO / "test" / "gold" / "generator.png"
PALETTE_DIR = HERE / "palettes"
EXPORT_DIR = HERE / "exports"
CHANNELS = "rgb"


# --------------------------------------------------------------------------
# Frames -> index images.  An index image holds the stored intensity (0-15) of
# every screen pixel, with 0 for background/margin, so a palette edit is just
# a 16-entry lookup over it and stays fast enough to drag a slider.
# --------------------------------------------------------------------------

# (flat pixel indices covered by any segment, the nibble each one reads), keyed
# by a cell's rectangles: the segment proportions are a live choice now, so the
# tables can't be one pair of globals.
_SEG_TABLES = {}


def _segment_tables(shape=None):
    cell = shape or shapes.CHIP
    if cell.key not in _SEG_TABLES:
        seg_map = np.full((HEIGHT, WIDTH), -1, dtype=np.int32)
        for row in range(cell.rows):
            for col in range(cell.cols):
                for seg in range(len(cell)):
                    # nibble n of a frame is digit (n // 8), segment (n % 8):
                    # low nibble first within a byte, 4 bytes per digit. How
                    # many digits there are is the cell's business: a bigger
                    # cell means a smaller grid.
                    x0, x1, y0, y1 = cell.pixels(col, row, seg)
                    seg_map[y0 : y1 + 1, x0 : x1 + 1] = (row * cell.cols + col) * 8 + seg
        flat = seg_map.ravel()
        mask = np.flatnonzero(flat >= 0)
        _SEG_TABLES[cell.key] = (mask, flat[mask])
    return _SEG_TABLES[cell.key]


def index_image_from_frame(frame, shape=None):
    """One .seg frame -> (600, 800) uint8 of stored intensities.

    A .seg file is bytes for one particular grid, so it can only be shown on
    the cell that grid came from -- everything else would read the wrong
    nibbles and look like noise rather than like a mistake."""
    cell = shape or shapes.CHIP
    if len(frame) != cell.frame_bytes:
        raise ValueError(
            f"this frame is {len(frame)} bytes; a {cell.cols}x{cell.rows} grid of "
            f"{cell.cell_w}x{cell.cell_h} cells needs {cell.frame_bytes}"
        )
    mask, nibble_of = _segment_tables(shape)
    data = np.frombuffer(frame, dtype=np.uint8)
    nibbles = np.empty(len(data) * 2, dtype=np.uint8)
    nibbles[0::2] = data & 0xF
    nibbles[1::2] = data >> 4
    idx = np.zeros(HEIGHT * WIDTH, dtype=np.uint8)
    idx[mask] = nibbles[nibble_of]
    return idx.reshape(HEIGHT, WIDTH)


def index_image_from_png(path):
    """A palette-0 capture (grey = intensity * 17) -> stored intensities.

    Only valid for an identity-palette capture; anything else (a coloured
    palette, a Tiny VGA capture) would silently read as the wrong intensities,
    so refuse it rather than show a plausible-looking wrong frame.
    """
    w, h, px = png.read_png(str(path))
    if (w, h) != (WIDTH, HEIGHT):
        raise ValueError(f"{path} is {w}x{h}, expected {WIDTH}x{HEIGHT}")
    rgb = np.array(px, dtype=np.uint8).reshape(HEIGHT, WIDTH, 3)
    if not ((rgb[..., 0] == rgb[..., 1]).all() and (rgb[..., 1] == rgb[..., 2]).all()):
        raise ValueError(f"{path} is not grey, so it is not a palette-0 capture")
    if (rgb[..., 0] % 17).any():
        raise ValueError(f"{path} is not a 16-level ramp (levels of 17)")
    return (rgb[..., 0] // 17).astype(np.uint8)


_EFFECT_XY = {}  # per cell: (x, y, is_dark) of every frame nibble's sample point


def _effect_coords(shape=None):
    """Sample points in frame-nibble order, as attract_proto.sample() takes
    them: pixel coordinates from the grid's top-left, one per segment. At the
    chip's own proportions these are attract_proto.SEG_CX/SEG_CY, which is
    what the RTL's table holds; a variant moves them (tools/shapes.py)."""
    cell = shape or shapes.CHIP
    if cell.key not in _EFFECT_XY:
        n = np.arange(cell.rows * cell.cols * 8)
        digit, seg = n // 8, n % 8
        row, col = digit // cell.cols, digit % cell.cols
        # A cell with no room for the decimal point has no nibble 7 to draw,
        # so it indexes offset 0 and is masked off by is_dark below.
        ox = np.array([cell.sample(s)[0] if s < len(cell) else 0 for s in range(8)])
        oy = np.array([cell.sample(s)[1] if s < len(cell) else 0 for s in range(8)])
        dark = np.array([cell.is_dark(s) for s in range(8)])
        x = col * cell.cell_w + ox[seg]
        y = row * cell.cell_h + oy[seg]
        _EFFECT_XY[cell.key] = (x.astype(np.int64), y.astype(np.int64), dark[seg])
    return _EFFECT_XY[cell.key]


def index_image_from_effect(name, frame, params, fade=15, shape=None):
    """One frame of an attract_proto effect -> (600, 800) uint8 of intensities.

    The effect's sample function is evaluated once over the whole frame as
    numpy arrays (attract_proto writes its maths so the same code runs on ints
    or arrays), then scattered like index_image_from_frame. DP stays dark, as
    in attract_proto.sample() -- and so does its nibble on a cell too wide to
    have a decimal point at all.
    `fade` (0..15) is attract_proto.apply_fade's level, for the fade through
    black between effects.
    """
    x, y, is_dark = _effect_coords(shape)
    levels = np.asarray(attract_proto.EFFECTS[name](frame, **params)(x, y), dtype=np.int64)
    levels = attract_proto.apply_fade(levels, fade)
    levels = np.where(is_dark, 0, levels).astype(np.uint8)
    mask, nibble_of = _segment_tables(shape)
    idx = np.zeros(HEIGHT * WIDTH, dtype=np.uint8)
    idx[mask] = levels[nibble_of]
    return idx.reshape(HEIGHT, WIDTH)


def list_clips(directory):
    """(path, frame_count) for every .seg in `directory` that is a whole number
    of chip-geometry frames.  Clips from the 640x480 era are not, and would
    render as noise, so they are left out rather than offered."""
    out = []
    for p in sorted(Path(directory).glob("*.seg")):
        size = p.stat().st_size
        if size and size % segments.FRAME_BYTES == 0:
            out.append((str(p), size // segments.FRAME_BYTES))
    return out


def read_frame(path, n):
    with open(path, "rb") as f:
        f.seek(n * segments.FRAME_BYTES)
        frame = f.read(segments.FRAME_BYTES)
    if len(frame) < segments.FRAME_BYTES:
        raise IndexError(f"frame {n} is past the end of {path}")
    return frame


# --------------------------------------------------------------------------
# Palettes
#
# A "curve" here is the editable form: {"r": [x1, y1, x2, y2], "g": ..., "b":
# ...}, as presets.json stores it. segments.py turns it into hardware params
# and a 16-entry table; everything drawn or checked goes through that table.
# --------------------------------------------------------------------------


def curve_table(curve):
    """Editable curve -> the 16 (r, g, b) entries the chip will produce.
    Raises ValueError for a curve the hardware can't draw."""
    return segments.curve_palette(segments.points_to_params(curve))


# --------------------------------------------------------------------------
# The end handle.  A curve's second point isn't something the chip keeps -- it
# stores only the slope, and the line runs on past the point until it clips at
# 15 -- so a handle drawn at the point sits mid-curve with the line carrying on
# beyond it.  The editor instead shows the handle where the line actually
# ends: on the plot's right edge (index 15) or top edge (where it clips).
#
# That end is usually between whole numbers: ~11% of legal curves (the steep
# ones, including preset 2's green channel) have no whole-number point on the
# edge that draws the same colours.  So the handle's position is computed, and
# what's stored stays a whole-number point the line passes through --
# presets.json and saved palettes keep their format, and nothing below the UI
# changes.
# --------------------------------------------------------------------------


def curve_end(x1, y1, x2, y2):
    """Where the second line, at the slope the chip actually stores, meets the
    plot's edge -- as (x, y) floats with x == 15 or y == 15."""
    m2 = segments.curve_params(x1, y1, x2, y2)[3]
    reach = y1 * 8 + m2 * (15 - x1)  # eighths, at index 15
    if reach <= 15 * 8:
        return 15.0, reach / 8
    return x1 + (15 - y1) * 8 / m2, 15.0


_SLOPES = {}


def slope_choices(x1, y1):
    """Every second slope the chip can store after knee (x1, y1), as
    (m2, through-point) sorted by slope. The point is a whole-number one that
    produces that slope, on the edge when one exists so saved files read
    naturally."""
    if (x1, y1) not in _SLOPES:
        best = {}
        for x2 in range(x1 + 1, 16):
            for y2 in range(y1, 16):
                m2 = segments.curve_params(x1, y1, x2, y2)[3]
                on_edge = x2 == 15 or y2 == 15
                if m2 not in best or (on_edge and not best[m2][1]):
                    best[m2] = ((x2, y2), on_edge)
        _SLOPES[(x1, y1)] = sorted((m2, p) for m2, (p, _e) in best.items())
    return _SLOPES[(x1, y1)]


def end_nearest(x1, y1, fx, fy):
    """The through-point, after knee (x1, y1), whose line ends nearest the
    edge position (fx, fy)."""
    def dist(p):
        ex, ey = curve_end(x1, y1, *p)
        return (ex - fx) ** 2 + (ey - fy) ** 2

    return min((p for _m, p in slope_choices(x1, y1)), key=dist)


def end_toward(x1, y1, fx, fy):
    """Drag target for the end handle: the line from the knee aims at the
    mouse (fx, fy, plot units, anywhere on the plot), and the handle lands on
    the stored slope whose end is nearest where that aim meets the edge."""
    dx, dy = fx - x1, max(0.0, fy - y1)
    if dx <= 0 or y1 + dy / dx * (15 - x1) > 15:
        # Steep: aims at the top edge.
        ex = x1 + (15 - y1) * dx / dy if dy > 0 and dx > 0 else x1
        target = (min(15.0, max(float(x1), ex)), 15.0)
    else:
        target = (15.0, y1 + dy / dx * (15 - x1))
    return end_nearest(x1, y1, *target)


def legal_points(pts):
    """Nudge points into the hardware's legal range rather than refuse them,
    so a drag never sticks against an illegal combination."""
    x1, y1, x2, y2 = pts
    x1 = min(x1, 14)
    if x1 == 0:
        y1 = 0
    x2 = max(x2, x1 + 1)
    y2 = max(y2, y1)
    return [x1, y1, x2, y2]


def drag_handles(start, handle, ref, to):
    """Where the dragged channels land when `ref`'s `handle` (0 = knee,
    1 = end) is dragged to `to`.

    `start` maps each channel being dragged to the points it had when the drag
    began. `ref`'s handle follows the cursor exactly; any others move by the
    same delta from where *they* started, so dragging in RGB mode keeps the
    offsets between the channels instead of collapsing them onto one curve.
    The lines will generally not stay parallel: the slopes the chip can store
    depend on the knee, so each channel snaps to its own nearest one.

    Everything is measured from the start of the drag rather than from the
    last step, so a channel that clips against the plot's edge springs back
    when the cursor comes back instead of having lost the offset for good.
    """
    out = {}
    if handle == 0:
        rx, ry = start[ref][:2]
        dx, dy = to[0] - rx, to[1] - ry
        for ch, pts in start.items():
            end = curve_end(*pts)
            x = min(14, max(0, pts[0] + dx))
            # The knee moves; the end stays where it is on the edge, rather
            # than the line swinging about a hidden through-point.
            y = 0 if x == 0 else min(15, max(0, pts[1] + dy))
            out[ch] = legal_points([x, y, *end_nearest(x, y, *end)])
    else:
        ex, ey = curve_end(*start[ref])
        dx, dy = to[0] - ex, to[1] - ey
        for ch, pts in start.items():
            x1, y1 = pts[:2]
            cx, cy = curve_end(*pts)
            # Aimed, not clamped to the plot: an aim past the edge is how the
            # steepest and shallowest slopes are reached, and clamping it
            # would quietly change the delta for that channel alone.
            out[ch] = legal_points([x1, y1, *end_toward(x1, y1, cx + dx, cy + dy)])
    return out


def palette_to_lut(palette, bits):
    """16 x (r, g, b) 4-bit entries -> 16 x 3 uint8 as the given Pmod shows them."""
    p = np.array(palette, dtype=np.int32)
    if bits == 12:
        return (p * 17).astype(np.uint8)
    if bits == 6:
        return ((p >> 2) * 85).astype(np.uint8)
    raise ValueError(f"bits must be 12 or 6, not {bits}")


def render(idx_image, palette, bits):
    return palette_to_lut(palette, bits)[idx_image]


def _groups(colours):
    seen = {}
    for i, c in enumerate(colours):
        seen.setdefault(tuple(c), []).append(i)
    return [g for g in seen.values() if len(g) > 1]


def check_palette(palette):
    """Warnings for the rules tools/segments.py documents; [] means clean.

    Grey legitimately collapses on the 6-bit Pmod -- that is the documented
    exemption -- so a 6-bit warning is a heads-up, not necessarily a defect.
    """
    msgs = []
    if tuple(palette[0]) != (0, 0, 0):
        msgs.append(
            "entry 0 must be black: it is also every background and margin pixel, "
            "so a colour there tints the whole screen"
        )
    lum = [sum(c) for c in palette]
    dimmer = [i for i in range(1, 16) if lum[i] < lum[i - 1]]
    if dimmer:
        msgs.append(
            "brightness falls at entries: " + ", ".join(map(str, dimmer))
            + " -- a brighter stored level would look dimmer"
        )
    full = _groups(palette)
    if full:
        msgs.append(
            "12-bit: identical colours, so these levels cannot be told apart: "
            + "; ".join(", ".join(map(str, g)) for g in full)
        )
    trunc = _groups([(r >> 2, g >> 2, b >> 2) for r, g, b in palette])
    if trunc:
        msgs.append(
            "6-bit (Tiny VGA) collapses these levels: "
            + "; ".join(", ".join(map(str, g)) for g in trunc)
        )
    return msgs


_CANDIDATES = None


def _candidates():
    """Every legal (x1, y1, x2, y2) with the 16 values the chip draws for it.
    About 14500 of them -- small enough to search exhaustively, which beats a
    clever fit that can't see the hardware's rounding."""
    global _CANDIDATES
    if _CANDIDATES is None:
        _CANDIDATES = []
        for x1 in range(15):
            for y1 in range(16):
                if x1 == 0 and y1:
                    continue
                for x2 in range(x1 + 1, 16):
                    for y2 in range(y1, 16):
                        p = segments.curve_params(x1, y1, x2, y2)
                        vals = np.array([segments.curve_value(i, *p) for i in range(16)])
                        _CANDIDATES.append(((x1, y1, x2, y2), vals))
    return _CANDIDATES


def fit_curve(table):
    """16 (r, g, b) entries -> the editable curve that draws closest to them
    (least total absolute error per channel). Ties go to the first found,
    which favours knees further left."""
    target = np.array(table, dtype=np.int32)
    curve = {}
    for ch, name in enumerate(CHANNELS):
        col = target[:, ch]
        best = min(_candidates(), key=lambda c: int(np.abs(c[1] - col).sum()))
        curve[name] = list(best[0])
    return curve


def to_json(curve, name):
    return json.dumps({"name": name, **{ch: list(curve[ch]) for ch in CHANNELS}}, indent=1)


def from_json(text):
    """A saved curve, or an old 16-entry table file (fitted to a curve).
    Returns (curve, fitted) -- fitted says the file was a table."""
    data = json.loads(text)
    if "entries" in data:
        entries = data["entries"]
        if not isinstance(entries, list) or len(entries) != 16:
            raise ValueError("expected 16 entries")
        for e in entries:
            if (
                not isinstance(e, list)
                or len(e) != 3
                or not all(isinstance(v, int) and 0 <= v <= 15 for v in e)
            ):
                raise ValueError(f"bad entry {e!r}: need [r, g, b] with each 0-15")
        return fit_curve(entries), True
    curve = {}
    for ch in CHANNELS:
        pts = data.get(ch)
        if not isinstance(pts, list) or len(pts) != 4:
            raise ValueError(f"channel {ch!r} needs [x1, y1, x2, y2]")
        segments.curve_params(*pts)  # raises ValueError if the chip can't draw it
        curve[ch] = list(pts)
    return curve, False


def save_preset(curve, slot, name, path=segments.PRESETS_JSON):
    """Write `curve` into presets.json slot `slot` (0-7)."""
    for ch in CHANNELS:
        segments.curve_params(*curve[ch])
    with open(path) as f:
        data = json.load(f)
    data["presets"][slot] = {"name": name, **{ch: list(curve[ch]) for ch in CHANNELS}}
    # One preset per line, the layout the file is hand-readable in.
    body = ",\n".join("  " + json.dumps(p) for p in data["presets"])
    with open(path, "w") as f:
        f.write('{\n "comment": ' + json.dumps(data["comment"]) + ',\n "presets": [\n' + body + "\n ]\n}\n")


def write_rtl(path=segments.PRESETS_V):
    """Regenerate src/palette_presets.v from presets.json. Returns the path."""
    presets = segments.load_presets()
    for p in presets:
        segments.points_to_params(p)  # refuse to write anything the chip can't draw
    with open(path, "w") as f:
        f.write(segments.presets_verilog(presets))
    return path


def export_text(curve, name, pmod_type=0):
    """The config packet and firmware call that load `curve` at runtime."""
    params = segments.points_to_params(curve)
    packet = segments.config_packet(params, pmod_type=pmod_type)
    pts = ", ".join(f"({', '.join(map(str, curve[ch]))})" for ch in CHANNELS)
    lines = [f"# palette builder export: {name}"]
    lines += [f"# WARNING: {m}" for m in check_palette(segments.curve_palette(params))]
    lines += [
        "",
        "# presets.json entry:",
        json.dumps({"name": name, **{ch: list(curve[ch]) for ch in CHANNELS}}),
        "",
        "# firmware: load it at runtime (seg_player.py or gen_mode.py)",
        f"main(curve=({pts}))",
        "",
        "# raw config packet, sent with uio[7] low, one byte per strobe:",
        packet.hex(" "),
        "",
    ]
    return "\n".join(lines)


def safe_name(name):
    return re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or "custom"


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------

PLOT = 18  # pixels per index / level on the curve plot -- sized, with the
# rest of the left column, so the window fits a 1280x800 laptop screen
PLAY_MS = 1000 // 24  # clip playback frame period; video2seg.py's default fps
EFFECT_FPS = 60  # effects play at the chip's real frame rate
EFFECT_FRAMES = 60 * EFFECT_FPS  # the frame slider spans a minute
SOURCES = ("generator", "droplet", "effect")
MIN_GAP_MS = 10  # idle time guaranteed between playback ticks, for input
CELL_PX = 288  # width of the Digit tab's drawing; it fits the left column
CELL_TILES = 3  # cells across and down there: one alone can't show the gaps
PAD = 24
LEFT_W = 2 * PAD + 15 * PLOT  # the curve plot, and so the whole left column
LENS = 3  # preview magnifier: screen pixels per image pixel under the cursor
LENS_PX = 150  # and how big the magnified square is on screen
LENS_OFF = 18  # and it sits this far off the cursor, rather than on top of
# the very pixels it is showing
CH_COLOURS = {"r": "#d32f2f", "g": "#2e7d32", "b": "#1565c0"}
# The fourth "Edit channel" button. Deliberately not "rgb": CHANNELS *is* that
# string, so a value that indexed self.curve by accident would read as a
# channel rather than raising.
ALL_CHANNELS = "all"


def _faded(colour, amount=0.6):
    """`colour` mixed `amount` of the way to white -- Tk's canvas has no
    alpha, so this is how the unselected channels' handles fade back."""
    rgb = [int(colour[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(v + (255 - v) * amount):02x}" for v in rgb)


class App:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        self.root = root
        root.title("Palette builder")
        self.curve = {ch: list(segments.PRESETS[0][ch]) for ch in CHANNELS}
        self._img = None  # the last rendered frame, for the magnifier
        self._lens_at = None  # image coords the magnifier is centred on
        self._clip_idx = (None, None, None, None)  # (path, frame, cell key, index image)
        self._drag = None  # which handle (0 = knee, 1 = end) is held
        self._drag_ref = "r"  # the channel whose handle the cursor is on
        self._drag_from = {}  # each dragged channel's points when it started
        self._playing = None  # the pending after() id while a clip plays
        self._anchor = None  # (monotonic time, frame) effect playback counts from
        self._anchor_last = None  # the frame the last tick set, to spot a hand drag
        # Effects: which one, each one's parameters (kept per effect for the
        # session, so switching away and back keeps your tweaks), and where
        # each source's frame slider was, since clip and effect share it.
        self.effect = tk.StringVar(value="plasma")
        self.effect_params = {n: attract_proto.default_params(n) for n in attract_proto.PARAMS}
        self._effect_idx = (None, None)  # (key, index image) cache
        # Which built-in preset the chip effect is showing, or None when the
        # preview should use the curve being edited.
        self._chip_preset = None
        self._change = None  # {"old", "start"} while fading between effects
        self._shown_effect = None  # the effect actually on screen
        self._fade_job = None  # after() id of the paused-fade pump
        self._frame_pos = {"droplet": 0, "effect": 0}
        self._scale_source = None  # whose frames the slider currently counts
        self.param_vars = {}
        self.param_line = tk.StringVar()

        self.channel = tk.StringVar(value="r")
        # The segment proportions the levels are drawn into. The defaults are
        # the chip's own geometry, which is the only one the Generator capture
        # can be shown in.
        self.digit_params = dict(shapes.DEFAULTS)
        self.digit = shapes.CHIP
        self.digit_vars = {}
        self.digit_scales = {}
        self.digit_labels = {}
        self._digit_syncing = False  # while all five are being set at once
        self.digit_info = tk.StringVar()
        self.digit_bands = tk.StringVar()
        self.digit_line = tk.StringVar()
        self.source = tk.StringVar(value="generator")
        self.bits = tk.IntVar(value=12)
        self.name = tk.StringVar(value="custom")
        self.slot = tk.IntVar(value=0)

        try:
            self.gen_idx = index_image_from_png(GENERATOR_PNG)
            self.gen_error = None
        except (OSError, ValueError) as e:
            self.gen_idx, self.gen_error = None, str(e)
        self.clips = list_clips(REPO)

        self._build()
        if self.gen_idx is None:
            self.source.set("droplet" if self.clips else "effect")
        self._sync_scale()
        self.refresh()

    # -- layout --

    def _build(self):
        tk, ttk = self.tk, self.ttk
        # Left column: one tab per thing being tuned -- the palette curves, or
        # the pattern the palette is applied to. Both act on the one preview.
        self.tabs = ttk.Notebook(self.root)
        self.tabs.grid(row=0, column=0, sticky="ns", padx=(8, 0), pady=8)
        left = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(left, text="Colour")
        pattern = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(pattern, text="Pattern")
        self.pattern_tab = pattern
        digit = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(digit, text="Digit")
        self.digit_tab = digit
        right = ttk.Frame(self.root, padding=8)
        right.grid(row=0, column=1, sticky="nsew")
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        chans = ttk.Frame(left)
        chans.grid(row=0, column=0, sticky="w")
        ttk.Label(chans, text="Edit channel:").pack(side="left")
        for value, label in [(ch, ch.upper()) for ch in CHANNELS] + [(ALL_CHANNELS, "RGB")]:
            ttk.Radiobutton(
                chans, text=label, value=value, variable=self.channel, command=self.refresh
            ).pack(side="left")

        self.plot = tk.Canvas(left, width=LEFT_W, height=LEFT_W, bg="white", highlightthickness=1)
        self.plot.grid(row=1, column=0, pady=4)
        self.plot.bind("<Button-1>", self._on_press)
        self.plot.bind("<B1-Motion>", self._on_drag)
        self.plot.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_drag", None))

        self.swatch_row = tk.Frame(left)
        self.swatch_row.grid(row=2, column=0, pady=4)
        self.swatches = []
        for i in range(16):
            sw = tk.Label(self.swatch_row, width=2, height=1, relief="sunken", text=f"{i:X}",
                          font=("TkFixedFont", 7))
            sw.pack(side="left")
            self.swatches.append(sw)

        pre = ttk.LabelFrame(left, text="Start from a built-in preset", padding=4)
        pre.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        for n, p in enumerate(segments.PRESETS):
            # Three across, not four: the left column has to leave room for
            # the 1:1 preview on a 1280-wide laptop screen.
            ttk.Button(pre, text=f"{n} {p['name']}", width=8, command=lambda n=n: self._load_preset(n)).grid(
                row=n // 3, column=n % 3
            )

        io = ttk.LabelFrame(left, text="Save / export", padding=4)
        io.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        ttk.Entry(io, textvariable=self.name, width=14).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Button(io, text="Save", width=6, command=self._save).grid(row=1, column=0)
        ttk.Button(io, text="Open...", width=7, command=self._open).grid(row=1, column=1)
        ttk.Button(io, text="Export", width=7, command=self._export).grid(row=1, column=2)

        chip = ttk.LabelFrame(left, text="Chip presets (presets.json -> src/)", padding=4)
        chip.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(chip, text="slot").grid(row=0, column=0)
        ttk.Spinbox(chip, from_=0, to=segments.N_PRESETS - 1, width=3, textvariable=self.slot).grid(row=0, column=1)
        ttk.Button(chip, text="Load", width=5, command=self._load_slot).grid(row=0, column=2)
        ttk.Button(chip, text="Save", width=5, command=self._save_slot).grid(row=0, column=3)
        ttk.Button(chip, text="Write RTL", command=self._write_rtl).grid(row=0, column=4)

        top = ttk.Frame(right)
        top.grid(row=0, column=0, sticky="w")
        ttk.Label(top, text="Source:").pack(side="left")
        for val, label in (("generator", "Generator"), ("droplet", "Droplet"), ("effect", "Effect")):
            # No clips (a fresh checkout or worktree: .seg files are
            # untracked), no Droplet source to choose.
            state = "disabled" if val == "droplet" and not self.clips else "normal"
            rb = ttk.Radiobutton(top, text=label, value=val, variable=self.source,
                                 command=self._source_changed, state=state)
            rb.pack(side="left")
            if val == "generator":
                # Kept, because the Digit tab disables it: the capture it
                # reads is of the chip, so it is the chip's proportions or
                # nothing.
                self.gen_radio = rb
            if val == "droplet":
                self.clip_radio = rb  # likewise: a .seg file is chip-grid bytes
        ttk.Label(top, text="   Output:").pack(side="left")
        for val, label in ((12, "12-bit PmodVGA"), (6, "6-bit Tiny VGA")):
            ttk.Radiobutton(top, text=label, value=val, variable=self.bits, command=self.refresh).pack(side="left")
        ttk.Label(top, text="   (keys: s source, b bits, space play)").pack(side="left")

        clip = ttk.Frame(right)
        clip.grid(row=1, column=0, sticky="ew", pady=4)
        # Greyed out with no clips: an empty Combobox's popdown on macOS has
        # nothing to select, never closes, and holds the grab, so the whole
        # window went dead.
        self.clip_box = ttk.Combobox(
            clip, state="readonly" if self.clips else "disabled", width=20,
            values=[os.path.basename(p) for p, _ in self.clips]
        )
        if not self.clips:
            self.clip_box.set("(no .seg clips)")
        self.clip_box.pack(side="left")
        if self.clips:
            best = next((i for i, (p, _) in enumerate(self.clips) if "waterdrop" in p), 0)
            self.clip_box.current(best)
        self.clip_box.bind("<<ComboboxSelected>>", lambda _e: self._clip_changed())
        self.frame_scale = tk.Scale(clip, from_=0, to=0, orient="horizontal", length=360, command=self._on_frame_scale)
        self.frame_scale.pack(side="left", padx=8)
        self.play_button = ttk.Button(clip, text="Play", width=6, command=self._toggle_play)
        self.play_button.pack(side="left")

        # Pattern tab: which effect, then its tuning parameters, rebuilt from
        # attract_proto's PARAMS whenever the effect changes.
        pick = ttk.Frame(pattern)
        pick.grid(row=0, column=0, sticky="w")
        # Radio buttons, not a Combobox: on macOS a Combobox's popdown stops
        # taking clicks while playback redraws the preview underneath it, and
        # holds the grab, so the whole window went dead until the app was
        # killed.
        ttk.Label(pick, text="Effect:").pack(side="left")
        for name in attract_proto.PARAMS:
            ttk.Radiobutton(pick, text=name, value=name, variable=self.effect,
                            command=self._effect_changed).pack(side="left")
        self.param_frame = ttk.LabelFrame(pattern, text="Pattern parameters", padding=8)
        self.param_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self._build_params()

        # Digit tab: the segment proportions, drawn CELL_TILES cells across so
        # the gap to the neighbouring digit shows -- that gap is the whole
        # reason the grid reads as digits rather than as a mesh, and a single
        # cell can't show it.
        prop = ttk.LabelFrame(digit, text="Segment proportions (tools/shapes.py)", padding=4)
        prop.grid(row=0, column=0, sticky="ew")
        for i, (key, lo, hi, _default, help_text) in enumerate(shapes.PARAMS):
            var = tk.IntVar(value=self.digit_params[key])
            self.digit_vars[key] = var
            label = ttk.Label(prop, text=key)
            label.grid(row=2 * i, column=0, sticky="sw")
            self.digit_labels[key] = label
            scale = tk.Scale(prop, from_=lo, to=hi, orient="horizontal", length=170,
                             variable=var, command=lambda _v: self._digit_changed())
            scale.grid(row=2 * i, column=1, sticky="w")
            self.digit_scales[key] = scale
            tk.Label(prop, text=help_text, fg="#666666", font=("TkDefaultFont", 9),
                     wraplength=250, justify="left").grid(row=2 * i + 1, column=0, columnspan=2,
                                                          sticky="w", pady=(0, 4))
        ttk.Button(prop, text="The chip's", command=self._digit_defaults).grid(
            row=2 * len(shapes.PARAMS), column=0, sticky="w", pady=(4, 0))
        self.cell = tk.Canvas(digit, width=CELL_PX, height=CELL_PX * 4 // 3,
                              bg="black", highlightthickness=1)
        self.cell.grid(row=1, column=0, pady=(8, 0))
        # Wrapped to the left column's own width: the host line is 83
        # characters, and unwrapped it set the whole tab's width, which made
        # the notebook wider than the Colour tab and pushed the preview off a
        # 1280-wide screen.
        tk.Label(digit, textvariable=self.digit_info, anchor="w", justify="left",
                 wraplength=LEFT_W, font=("TkDefaultFont", 9)).grid(
                     row=2, column=0, sticky="ew", pady=(4, 0))
        # What the RTL would need for these proportions: the y bands naming
        # the two nibbles a scanline fetches, and the cx predicate that picks
        # between them -- slot0_seg/slot1_seg and xz_right | xz_dp.
        ttk.Label(digit, text="cy bands -> (slot 0, slot 1), for the RTL:").grid(
            row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(digit, textvariable=self.digit_bands, state="readonly", width=34).grid(
            row=4, column=0, sticky="ew")
        ttk.Entry(digit, textvariable=self.digit_line, state="readonly", width=34).grid(
            row=5, column=0, sticky="ew", pady=(2, 0))
        self._update_digit_info()
        # Opening the Pattern tab shows the pattern: tuning one you can't see
        # is no use. Going back to Colour leaves the source alone, so the
        # palette can be tuned on the effect too.
        self.tabs.bind("<<NotebookTabChanged>>", lambda _e: self._tab_changed())

        # A Canvas with the frame as an item, not a Label with an image: the
        # magnifier is a second item on the same canvas, and moving an item
        # repaints only the damage (2 ms). As a child *widget* over an image
        # Label it cost a repaint of the whole 800x600 picture (19 ms) on
        # every mouse move, which is what made it trail the cursor while a
        # pattern played. Both images are written in place with put() for the
        # same reason -- a new PhotoImage per frame was another 5 ms.
        # The sunken border goes on a wrapper rather than on the canvas: Tk
        # draws a canvas's own border over canvas coordinate 0, which would
        # hide the frame's outside row and column and put every lens
        # coordinate a pixel out.
        frame = tk.Frame(right, bd=1, relief="sunken")
        frame.grid(row=2, column=0)
        self.preview = tk.Canvas(frame, width=WIDTH, height=HEIGHT, bd=0,
                                 highlightthickness=0, bg="black")
        self.preview.pack()
        self._photo = tk.PhotoImage(width=WIDTH, height=HEIGHT)
        self.preview.create_image(0, 0, anchor="nw", image=self._photo)
        self._lens_photo = tk.PhotoImage(width=LENS_PX, height=LENS_PX)
        self._lens_item = self.preview.create_image(0, 0, anchor="nw", image=self._lens_photo,
                                                    state="hidden")
        self.preview.bind("<Motion>", self._lens_show)
        self.preview.bind("<Leave>", self._lens_hide)
        self.status = tk.Label(right, justify="left", anchor="w", wraplength=WIDTH)
        self.status.grid(row=3, column=0, sticky="ew", pady=4)
        # What the window's own theme uses, to go back to after a warning.
        self._status_fg = self.status.cget("fg")
        self._status_bg = self.status.cget("bg")

        self.root.bind("<Key>", self._on_key)

    # -- curve plot geometry --

    @staticmethod
    def _to_canvas(x, y):
        return PAD + x * PLOT, PAD + (15 - y) * PLOT

    @staticmethod
    def _from_canvas(cx, cy):
        x = round((cx - PAD) / PLOT)
        y = 15 - round((cy - PAD) / PLOT)
        return min(15, max(0, x)), min(15, max(0, y))

    @staticmethod
    def _from_canvas_f(cx, cy):
        """Unrounded plot position, for aiming the end handle."""
        x = (cx - PAD) / PLOT
        y = 15 - (cy - PAD) / PLOT
        return min(15.0, max(0.0, x)), min(15.0, max(0.0, y))

    # -- state changes --

    def _on_key(self, e):
        if isinstance(e.widget, (self.tk.Entry, self.ttk.Entry, self.ttk.Combobox, self.tk.Spinbox, self.ttk.Spinbox)):
            return
        if e.char == "s":
            order = [s for s in SOURCES if s != "droplet" or self.clips]
            cur = self.source.get()
            self.source.set(order[(order.index(cur) + 1) % len(order)] if cur in order else order[0])
            self._source_changed()
        elif e.char == "b":
            self.bits.set(6 if self.bits.get() == 12 else 12)
            self.refresh()
        elif e.char == " ":
            self._toggle_play()

    # -- clip playback --

    def _on_frame_scale(self, _v):
        # Tk fires a Scale's command for .set() too, deferred to idle. While
        # playing, _tick() has already drawn the frame, so skip the second
        # render; a drag during playback is picked up by the next tick.
        if self._playing is None:
            self.refresh()

    def _toggle_play(self):
        if self._playing is not None:
            self.root.after_cancel(self._playing)
            self._playing = None
            self.play_button.config(text="Play")
            return
        if self.source.get() == "generator":
            # The generator is a still, so Play has to mean something else:
            # the pattern when that's what's being tuned, or when there are no
            # clips to play (a fresh checkout or worktree has none -- they're
            # untracked). Otherwise the clip, as it always has.
            on_pattern = self.tabs.select() == str(self.pattern_tab)
            self.source.set("effect" if on_pattern or not self.clips else "droplet")
            self._sync_scale()
        self.play_button.config(text="Pause")
        self._anchor = None
        self._tick()

    def _tick(self):
        """Advance and loop at the end, less however long the frame took to
        draw, so a slow render never piles up callbacks.

        A clip steps one frame at a time at the converter's default 24 fps, so
        a slow render slows it down. An effect runs on wall-clock time at the
        chip's 60 fps instead, skipping frames if a render is slow -- its
        speed is one of the things being tuned, so it has to look right.
        """
        start = time.monotonic()
        cur = int(self.frame_scale.get())
        if self.source.get() == "effect":
            n, period = EFFECT_FRAMES, 1000 // EFFECT_FPS
            # Frames are counted from an anchor (when Play was pressed, or the
            # slider last moved by hand), not added up tick by tick: rounding
            # each tick's elapsed time to whole frames dropped the fraction
            # every time, and ran effects at ~0.7-0.9x real speed.
            if self._anchor is None or cur != self._anchor_last:
                self._anchor = (start, cur)
            t0, f0 = self._anchor
            nxt = (f0 + int((start - t0) * EFFECT_FPS)) % n
            self._anchor_last = nxt
        else:
            period = PLAY_MS
            n = self.clips[self.clip_box.current()][1] if self.clips else 1
            nxt = (cur + 1) % max(n, 1)
        self.frame_scale.set(nxt)
        self.refresh()
        spent = int((time.monotonic() - start) * 1000)
        # Always leave a gap before the next tick. A render plus its redraw
        # takes longer than a 60 fps frame, and re-arming at 1 ms kept a timer
        # permanently due -- which on macOS starves Tk's native event queue, so
        # clicks (the effect dropdown, Pause) were never read and the app
        # looked hung. Effects follow wall-clock time, so a lower frame rate
        # still runs at the right speed.
        self._playing = self.root.after(max(MIN_GAP_MS, period - spent), self._tick)

    def _frame_count(self, source):
        if source == "effect":
            return EFFECT_FRAMES
        return self.clips[self.clip_box.current()][1] if self.clips else 1

    def _sync_scale(self):
        """Point the shared frame slider at the current source's frames,
        remembering where the other source's slider was."""
        src = self.source.get()
        src = src if src in self._frame_pos else "droplet"
        if self._scale_source is not None:
            self._frame_pos[self._scale_source] = int(self.frame_scale.get())
        n = self._frame_count(src)
        self.frame_scale.config(to=max(n - 1, 0))
        self.frame_scale.set(min(self._frame_pos[src], n - 1))
        self._scale_source = src

    def _source_changed(self):
        self._sync_scale()
        self.refresh()

    def _clip_changed(self, refresh=True):
        if self.source.get() != "droplet":
            self.source.set("droplet")
        self._sync_scale()
        if refresh:
            self.refresh()

    # -- effect parameters --

    def _build_params(self):
        tk, ttk = self.tk, self.ttk
        for w in self.param_frame.winfo_children():
            w.destroy()
        name = self.effect.get()
        self.param_frame.config(text=f"Pattern parameters: {name}")
        values = self.effect_params[name]
        self.param_vars = {}
        for i, (key, lo, hi, _default, help_text) in enumerate(attract_proto.PARAMS[name]):
            var = tk.IntVar(value=values[key])
            self.param_vars[key] = var
            ttk.Label(self.param_frame, text=key).grid(row=2 * i, column=0, sticky="sw")
            tk.Scale(self.param_frame, from_=lo, to=hi, orient="horizontal", length=170,
                     variable=var, command=lambda _v: self._on_param()).grid(row=2 * i, column=1, sticky="w")
            tk.Label(self.param_frame, text=help_text, fg="#666666", font=("TkDefaultFont", 9),
                     wraplength=250, justify="left").grid(row=2 * i + 1, column=0, columnspan=2,
                                                           sticky="w", pady=(0, 6))
        n = len(attract_proto.PARAMS[name])
        ttk.Button(self.param_frame, text="Defaults", command=self._param_defaults).grid(
            row=2 * n, column=0, sticky="w", pady=(4, 0))
        ttk.Label(self.param_frame, text="For attract_proto.py (select to copy):").grid(
            row=2 * n + 1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Entry(self.param_frame, textvariable=self.param_line, state="readonly", width=34).grid(
            row=2 * n + 2, column=0, columnspan=2, sticky="ew")
        self._update_param_line()

    def _update_param_line(self):
        name = self.effect.get()
        self.param_line.set(f"{name} {attract_proto.param_args(self.effect_params[name])}")

    def _on_param(self):
        # Tk also fires this for the initial value when a Scale is built,
        # deferred to idle, which just costs one redundant render.
        name = self.effect.get()
        for key, var in self.param_vars.items():
            try:
                self.effect_params[name][key] = int(var.get())
            except (ValueError, self.tk.TclError):
                return
        self._update_param_line()
        # Tuning a pattern you can't see is no use, so a slider moves the
        # preview onto the effect.
        if self.source.get() != "effect":
            self.source.set("effect")
            self._sync_scale()
        if self._playing is None:
            self.refresh()  # while playing, the next tick picks it up

    def _param_defaults(self):
        name = self.effect.get()
        self.effect_params[name] = attract_proto.default_params(name)
        self._build_params()
        self._on_param()

    def _show_effect(self):
        if self.source.get() != "effect":
            self.source.set("effect")
            self._sync_scale()
            self.refresh()

    # -- changing effect: fade to black, switch, fade up (attract_proto) --

    def _change_frames(self):
        """Frames into the current change, on wall-clock time like Play, so
        the fade takes its real 3 s whether or not the effect is playing."""
        return int((time.monotonic() - self._change["start"]) * EFFECT_FPS)

    def _effect_on_screen(self):
        """(effect, fade level) the preview shows right now."""
        if self._change is not None:
            k = self._change_frames()
            if k < attract_proto.FADE_FRAMES:
                fade = attract_proto.fade_factor(k)
                if attract_proto.fade_showing_new(k):
                    self._shown_effect = self.effect.get()
                    return self._shown_effect, fade
                return self._change["old"], fade
            self._change = None
        self._shown_effect = self.effect.get()
        return self._shown_effect, 15

    def _start_change(self):
        """Begin a fade from whatever is on screen to self.effect."""
        now = time.monotonic()
        half = attract_proto.FADE_FRAMES // 2
        if self._change is not None:
            k = self._change_frames()
            if k < half:
                return  # still fading out: the new target just changes
            if k < attract_proto.FADE_FRAMES:
                # Mid fade-in: fade the half-risen effect back out from its
                # current brightness, not from full.
                fade = attract_proto.fade_factor(k)
                self._change = {"old": self._shown_effect,
                                "start": now - ((15 - fade) * half // 16) / EFFECT_FPS}
                self._pump_fade()
                return
        if self._shown_effect in (None, self.effect.get()):
            self._change = None
            return
        self._change = {"old": self._shown_effect, "start": now}
        self._pump_fade()

    def _pump_fade(self):
        """Keep the fade moving while paused; while playing, _tick redraws
        every frame anyway."""
        if self._fade_job is not None:
            return

        def step():
            self._fade_job = None
            if self._change is None or self.source.get() != "effect":
                return
            if self._playing is None:
                self.refresh()
            self._fade_job = self.root.after(max(MIN_GAP_MS, 1000 // EFFECT_FPS), step)

        step()

    def _effect_changed(self):
        self._build_params()
        if self.source.get() == "effect":
            self._start_change()  # the picture fades; the sliders switch now
        else:
            self._shown_effect = None
            self._show_effect()
        self.refresh()

    def _tab_changed(self):
        if self.tabs.select() == str(self.pattern_tab):
            self._show_effect()
        elif self.tabs.select() == str(self.digit_tab):
            # Proportions are judged moving, and the zone plate is what the
            # chip actually draws into them. The Generator source is a still
            # -- and only ever the chip's own digit -- so leave it behind.
            if self.source.get() == "generator":
                self.effect.set("zoneplate")
                self._build_params()
                self._shown_effect = None
            self._show_effect()

    def _update_digit_info(self):
        """The readouts, which follow the cell and not the palette -- so they
        stay off the redraw path, where validate() and owner() would be paid
        for on every frame of playback.

        They are not decoration: these sliders change the cell, and the cell
        changes how many digits there are and how fast the host has to feed
        them, which is the part that decides whether a shape could be built."""
        cell = self.digit
        mask, bands = shapes.validate(cell)
        self.digit_info.set(
            "\n".join(shapes.grid(cell)
                      + [f"{len(cell)}/{shapes.NIBBLES} nibbles, "
                         f"slot 1 when {shapes.mask_text(cell, mask)}"])
        )
        self.digit_bands.set(shapes.band_text(cell, bands))
        self.digit_line.set(shapes.call_text(self.digit_params))

    def _digit_changed(self):
        """A proportion slider moved: rebuild the cell.

        Every combination of the six is drawable now that the cell follows
        them, so there is nothing to refuse and nothing to narrow -- what
        changes underneath is the grid, and _update_digit_info() reports it."""
        # Tk fires a Scale's command for its initial value too, deferred to
        # idle, so this runs once per slider on startup -- which just rebuilds
        # the same cell.
        if self._digit_syncing:
            return  # mid-way through setting all six: wait for the last one
        try:
            params = {key: int(var.get()) for key, var in self.digit_vars.items()}
        except (ValueError, self.tk.TclError):
            return
        if params != self.digit_params:
            try:
                cell = shapes.digit(**params)
                shapes.validate(cell)  # the prefetch's rules, not just the drawing
            except ValueError as e:
                self._say(f"digit: {e}", bad=True)
                return
            self.digit_params, self.digit = params, cell
            self._update_digit_info()
        # The Generator source is test/gold/generator.png and a clip is a .seg
        # file: both are bytes for the chip's own 64x37 grid of 12x16 cells,
        # so neither can be read into any other cell. A variant is drawn from
        # an effect, which is computed rather than stored.
        chip_grid = self.digit.key == shapes.CHIP.key
        self.gen_radio.config(
            state="normal" if chip_grid and self.gen_idx is not None else "disabled")
        self.clip_radio.config(state="normal" if chip_grid and self.clips else "disabled")
        if not chip_grid and self.source.get() in ("generator", "droplet"):
            self.source.set("effect")
            self._sync_scale()
        if self._playing is None:
            self.refresh()  # while playing, the next tick picks it up

    def _set_digit(self, params):
        """Put `params` on the sliders, rebuilding once rather than six
        times -- each set() fires the Scale's command on its own."""
        self._digit_syncing = True
        try:
            for key, var in self.digit_vars.items():
                var.set(params[key])
        finally:
            self._digit_syncing = False

    def _digit_defaults(self):
        """Back to the chip's own proportions."""
        self._set_digit(shapes.DEFAULTS)
        self._digit_changed()

    def _edit_channels(self):
        """The channels a drag moves: all three with RGB selected."""
        ch = self.channel.get()
        return list(CHANNELS) if ch == ALL_CHANNELS else [ch]

    def _handles(self, ch):
        """(knee, end) for `ch`, in plot units."""
        pts = self.curve[ch]
        return tuple(pts[:2]), curve_end(*pts)

    def _on_press(self, e):
        # Nearest handle wins, over every channel being edited -- so in RGB
        # mode the one you grab is the one that follows the cursor. A tie
        # keeps the knee, which is drawn on top.
        best = None
        for ch in self._edit_channels():
            for handle, (x, y) in enumerate(self._handles(ch)):
                cx, cy = self._to_canvas(x, y)
                d = (e.x - cx) ** 2 + (e.y - cy) ** 2
                if best is None or d < best[0]:
                    best = (d, handle, ch)
        _d, self._drag, self._drag_ref = best
        self._drag_from = {ch: list(self.curve[ch]) for ch in self._edit_channels()}
        self._on_drag(e)

    def _on_drag(self, e):
        if self._drag is None:
            return
        if self._drag == 0:
            kx, ky = self._from_canvas(e.x, e.y)
            kx = min(kx, 14)
            to = (kx, 0 if kx == 0 else ky)
        else:
            to = self._from_canvas_f(e.x, e.y)
        self.curve.update(drag_handles(self._drag_from, self._drag, self._drag_ref, to))
        self.refresh()

    def _set_curve(self, curve):
        self.curve = {ch: list(curve[ch]) for ch in CHANNELS}
        self.refresh()

    def _load_preset(self, n):
        self._set_curve(segments.PRESETS[n])
        self.name.set(segments.PRESETS[n]["name"])
        self.slot.set(n)

    def _save(self):
        name = safe_name(self.name.get())
        PALETTE_DIR.mkdir(exist_ok=True)
        path = PALETTE_DIR / f"{name}.json"
        path.write_text(to_json(self.curve, name))
        self._say(f"saved {path.relative_to(REPO)}")

    def _open(self):
        from tkinter import filedialog

        PALETTE_DIR.mkdir(exist_ok=True)
        path = filedialog.askopenfilename(initialdir=PALETTE_DIR, filetypes=[("palette", "*.json")])
        if not path:
            return
        try:
            curve, fitted = from_json(Path(path).read_text())
        except (OSError, ValueError) as e:
            self._say(f"open: {e}", bad=True)
            return
        self._set_curve(curve)
        self.name.set(Path(path).stem)
        if fitted:
            self._say(f"{Path(path).name} was a 16-entry table: fitted to the nearest curve")

    def _export(self):
        name = safe_name(self.name.get())
        EXPORT_DIR.mkdir(exist_ok=True)
        path = EXPORT_DIR / f"{name}.txt"
        path.write_text(export_text(self.curve, name))
        self._say(f"exported {path.relative_to(REPO)}")

    def _save_slot(self):
        slot = int(self.slot.get())
        save_preset(self.curve, slot, safe_name(self.name.get()))
        self._say(
            f"saved to presets.json slot {slot} -- press Write RTL to regenerate "
            "src/palette_presets.v (Load from slot reads it back; the preset buttons "
            "above keep the file as it was at startup)"
        )

    def _load_slot(self):
        # From disk, not segments.PRESETS: that was read at import, so it
        # misses anything saved to a slot since the builder started.
        slot = int(self.slot.get())
        try:
            preset = segments.load_presets()[slot]
            self._set_curve(preset)
        except (OSError, ValueError, KeyError, IndexError) as e:
            self._say(f"load slot {slot}: {e}", bad=True)
            return
        self.name.set(preset["name"])
        self._say(f"loaded presets.json slot {slot} ({preset['name']})")

    def _write_rtl(self):
        path = write_rtl()
        self._say(f"wrote {Path(path).resolve().relative_to(REPO)} from presets.json")

    # -- drawing --

    def _current_index_image(self):
        shape = self.digit
        if self.source.get() == "generator":
            self._chip_preset = None
            if self.gen_idx is None:
                raise ValueError(f"generator frame unavailable: {self.gen_error}")
            if shape.key != shapes.CHIP.key:
                # _digit_changed() disables the radio, so this is only
                # reachable if that ever stops being true.
                raise ValueError("the generator capture is of the chip's own digit; "
                                 "a variant needs an effect")
            return self.gen_idx
        if self.source.get() == "effect":
            name, fade = self._effect_on_screen()
            params = self.effect_params[name]
            key = (shape.key, name, int(self.frame_scale.get()), tuple(sorted(params.items())), fade)
            # The chip drives its own palette from ui_in[4:1], so refresh()
            # colours it with that instead of the curve on the Colour tab.
            # Every other effect is a pattern to tune a curve against.
            self._chip_preset = (attract_proto.chip_preset(key[2], **params)
                                 if name == "chip" else None)
            if self._effect_idx[0] != key:
                self._effect_idx = (key, index_image_from_effect(name, key[2], params, fade, shape))
            return self._effect_idx[1]
        self._chip_preset = None
        if not self.clips:
            raise ValueError("no current-geometry .seg clips found in the repo root")
        path, _ = self.clips[self.clip_box.current()]
        n = int(self.frame_scale.get())
        if self._clip_idx[:3] != (path, n, shape.key):
            self._clip_idx = (path, n, shape.key,
                              index_image_from_frame(read_frame(path, n), shape))
        return self._clip_idx[3]

    # -- preview magnifier --

    def _lens_hide(self, _e=None):
        self._lens_at = None
        self.preview.itemconfigure(self._lens_item, state="hidden")

    def _lens_show(self, e):
        """Magnify the square centred on the cursor. Drawn beside the cursor
        (see LENS_OFF), and flipped to its other side at the right and bottom
        edges rather than clamped, so it stays inside the picture."""
        if self._img is None:
            return
        x, y = int(self.preview.canvasx(e.x)), int(self.preview.canvasy(e.y))
        if not (0 <= x < WIDTH and 0 <= y < HEIGHT):
            self._lens_hide()
            return
        self._lens_at = (x, y)
        self._draw_lens()
        lx = x + LENS_OFF if x + LENS_OFF + LENS_PX <= WIDTH else x - LENS_OFF - LENS_PX
        ly = y + LENS_OFF if y + LENS_OFF + LENS_PX <= HEIGHT else y - LENS_OFF - LENS_PX
        self.preview.coords(self._lens_item, lx, ly)
        self.preview.itemconfigure(self._lens_item, state="normal")

    def _draw_lens(self):
        """The magnified crop, from the frame the preview last drew -- so it
        follows playback as well as the cursor."""
        if self._lens_at is None or self._img is None:
            return
        x, y = self._lens_at
        n, PL = LENS_PX // LENS, LENS_PX
        # At the edges the square stays whole and stops following the cursor,
        # rather than shrinking or showing black.
        x0 = min(max(x - n // 2, 0), WIDTH - n)
        y0 = min(max(y - n // 2, 0), HEIGHT - n)
        crop = self._img[y0 : y0 + n, x0 : x0 + n]
        big = np.repeat(np.repeat(crop, LENS, axis=0), LENS, axis=1)
        # A two-pixel white-on-black edge, painted into the image rather than
        # drawn as a second canvas item: one item is one lot of damage to
        # repaint, and the pattern underneath is as often white as black.
        for ring, v in ((0, 255), (1, 0)):
            big[ring, ring:PL - ring] = big[PL - 1 - ring, ring:PL - ring] = v
            big[ring:PL - ring, ring] = big[ring:PL - ring, PL - 1 - ring] = v
        self._lens_photo.put(b"P6\n%d %d\n255\n" % (LENS_PX, LENS_PX) + big.tobytes())

    def _say(self, msg, bad=False):
        # White on dark red, not red on the window's own background: red
        # against green is the one pair a red/green colour blind reader can't
        # tell apart, and dark red text is hard to read on a dark theme
        # besides. The warning is a filled block; anything fine is plain text.
        if bad:
            self.status.config(text=msg, fg="white", bg="#b00020")
        else:
            self.status.config(text=msg, fg=self._status_fg, bg=self._status_bg)

    def _draw_plot(self, table):
        c = self.plot
        c.delete("all")
        for i in range(16):
            x, _ = self._to_canvas(i, 0)
            _, y = self._to_canvas(0, i)
            c.create_line(x, PAD, x, PAD + 15 * PLOT, fill="#eeeeee")
            c.create_line(PAD, y, PAD + 15 * PLOT, y, fill="#eeeeee")
        active = self._edit_channels()
        for ch_i, ch in enumerate(CHANNELS):
            col = CH_COLOURS[ch]
            width = 3 if ch in active else 1
            # What the chip draws: the quantised values, as a stepped line.
            pts = [self._to_canvas(i, table[i][ch_i]) for i in range(16)]
            c.create_line(*[v for p in pts for v in p], fill=col, width=width)
            for px, py in pts:
                c.create_oval(px - 2, py - 2, px + 2, py + 2, fill=col, outline=col)
        # The other channels' knee and end, faded and dashed, so it's clear
        # where they sit without looking grabbable. Drawn first, so the
        # edited channels' handles land on top where they coincide. In RGB
        # mode there are none: every handle is grabbable.
        for ch in CHANNELS:
            if ch in active:
                continue
            pts = self.curve[ch]
            for x, y in (pts[:2], curve_end(*pts)):
                cx, cy = self._to_canvas(x, y)
                c.create_rectangle(cx - 5, cy - 5, cx + 5, cy + 5,
                                   outline=_faded(CH_COLOURS[ch]), width=2, dash=(2, 2))
        # The start: fixed at black, because index 0 is also the background
        # (rule 1), so it's a dot, not a handle.
        sx, sy = self._to_canvas(0, 0)
        c.create_oval(sx - 4, sy - 4, sx + 4, sy + 4, fill="#777777", outline="#777777")
        c.create_text(sx + 8, sy - 6, text="start", anchor="sw", fill="#777777")
        # Handles for the channels being edited: the knee, and the end --
        # where the line meets the edge, not the stored through-point. With
        # three sets of handles up, only the last one grabbed is labelled;
        # three "knee"/"end" pairs on one plot is noise.
        labelled = self._drag_ref if len(active) > 1 else active[0]
        for ch in active:
            for (x, y), label, where in zip(self._handles(ch), ("knee", "end"), ("below", "left")):
                cx, cy = self._to_canvas(x, y)
                c.create_rectangle(cx - 6, cy - 6, cx + 6, cy + 6, outline=CH_COLOURS[ch], width=2)
                if ch != labelled:
                    continue
                if where == "below":
                    c.create_text(cx + 10, cy + 10, text=label, anchor="nw", fill=CH_COLOURS[ch])
                else:
                    c.create_text(cx - 10, cy + 10, text=label, anchor="ne", fill=CH_COLOURS[ch])

    def _draw_cell(self, table):
        """The digit at CELL_TILES cells across, in the palette being edited.
        Alternate segments are drawn a few levels down, so neighbouring
        segments are told apart at a glance -- and so it shows when a segment
        grows into the one next to it in the next cell, which is what the
        leftover column and rows exist to stop."""
        c = self.cell
        c.delete("all")
        cell = self.digit
        lut = palette_to_lut(table, self.bits.get())
        w, h = cell.cell_w, cell.cell_h
        # As big as CELL_TILES of this cell will go in the canvas: a 12x16
        # cell gets 8 pixels each, a 40x50 one gets 2.
        z = max(1, min(CELL_PX // (CELL_TILES * w), CELL_PX * 4 // 3 // (CELL_TILES * h)))
        for gy in range(CELL_TILES):
            for gx in range(CELL_TILES):
                ox, oy = gx * w * z, gy * h * z
                for seg in range(len(cell)):
                    r, g, b = (int(v) for v in lut[0 if cell.is_dark(seg) else 15 - 4 * (seg & 1)])
                    colour = f"#{r:02x}{g:02x}{b:02x}"
                    x0, x1, y0, y1 = cell.rect(seg)
                    c.create_rectangle(ox + x0 * z, oy + y0 * z,
                                       ox + (x1 + 1) * z, oy + (y1 + 1) * z,
                                       fill=colour, outline=colour)
        # The middle cell is labelled: each segment's name at the point the
        # generator samples it, which is the one pixel of a segment whose
        # level the zone plate actually decides.
        ox, oy = w * z, h * z
        for seg in range(len(cell)):
            sx, sy = cell.sample(seg)
            px, py = ox + sx * z + z // 2, oy + sy * z + z // 2
            ink = "#ffffff" if cell.is_dark(seg) else "#000000"
            c.create_oval(px - 2, py - 2, px + 2, py + 2, fill=ink, outline="")
            c.create_text(px + 4, py - 4, text=cell.name(seg), anchor="sw", fill=ink,
                          font=("TkFixedFont", 8))

    def refresh(self):
        try:
            table = curve_table(self.curve)
        except ValueError as e:
            self._say(f"curve: {e}", bad=True)
            return
        self._draw_plot(table)
        self._draw_cell(table)
        lut = palette_to_lut(table, self.bits.get())
        for i in range(16):
            r, g, b = (int(v) for v in lut[i])
            self.swatches[i].config(bg=f"#{r:02x}{g:02x}{b:02x}", fg="white" if r + g + b < 300 else "black")
        try:
            idx = self._current_index_image()
        except (ValueError, IndexError, OSError) as e:
            self._say(str(e), bad=True)
            return
        # The chip effect is the chip, palette included: it holds ui_in[3:1]
        # in manual mode and steps through the presets otherwise, fading
        # through black across each change. Only the preview follows it --
        # the plot and the swatches above still show the curve being edited,
        # because that is what Save and Export write.
        if self._chip_preset is not None:
            lut = palette_to_lut(
                segments.curve_palette(segments.PRESET_PARAMS[self._chip_preset]),
                self.bits.get(),
            )
        img = lut[idx]
        self._img = img
        self._photo.put(b"P6\n%d %d\n255\n" % (WIDTH, HEIGHT) + img.tobytes())
        self._draw_lens()
        # Proportions that leave no gap to the next digit are legal, and the
        # picture may even be what's wanted, but it is worth saying out loud:
        # those leftover columns and rows are what stop the grid reading as a
        # mesh (CLAUDE.md).
        warns = check_palette(table) + shapes.warnings(self.digit)
        msg = "\n".join(warns) if warns else "All 16 entries distinct at 12-bit and 6-bit; entry 0 is black."
        if self._chip_preset is not None and not warns:
            msg = f"preview is on the chip's palette {self._chip_preset}; the curve below is unchanged"
        self._say(msg, bool(warns))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--write-rtl",
        action="store_true",
        help="regenerate src/palette_presets.v from presets.json and exit (no display needed)",
    )
    args = ap.parse_args()
    if args.write_rtl:
        path = write_rtl()
        print(f"wrote {Path(path).resolve()}")
        return

    import tkinter as tk

    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
