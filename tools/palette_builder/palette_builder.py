#!/usr/bin/env python3
"""
Interactive palette builder for the Multi Segment Monitor.

    ./palette_builder.py              # the editor
    ./palette_builder.py --write-rtl  # regenerate src/palette_presets.v

A palette is three per-channel curves (see "Colour palettes" in
tools/segments.py): each a line from (0, 0) to a knee, then on through a
second point until it clips at 15. Drag the two handles of the selected
channel on the curve plot (or type the numbers) and see the result on a frame
of the generator or of a droplet clip, as the 12-bit PmodVGA (4 bits/channel)
or the 6-bit Tiny VGA Pmod (2 bits/channel) would show it. What's drawn is
always the curve after the chip's own quantisation -- slopes in eighths,
rounded as the RTL rounds -- never the idealised line.

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

import png  # noqa: E402
import segments  # noqa: E402

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

_SEG_MASK = None  # flat pixel indices covered by any segment
_SEG_NIBBLE = None  # which nibble of the frame each of those pixels reads


def _segment_tables():
    global _SEG_MASK, _SEG_NIBBLE
    if _SEG_MASK is None:
        seg_map = np.full((HEIGHT, WIDTH), -1, dtype=np.int32)
        for row in range(segments.ROWS):
            for col in range(segments.COLS):
                for seg in range(8):
                    x0, x1, y0, y1 = segments.segment_pixels(col, row, seg)
                    # nibble n of a frame is digit (n // 8), segment (n % 8):
                    # low nibble first within a byte, 4 bytes per digit.
                    seg_map[y0 : y1 + 1, x0 : x1 + 1] = (row * segments.COLS + col) * 8 + seg
        flat = seg_map.ravel()
        _SEG_MASK = np.flatnonzero(flat >= 0)
        _SEG_NIBBLE = flat[_SEG_MASK]
    return _SEG_MASK, _SEG_NIBBLE


def index_image_from_frame(frame):
    """One .seg frame (FRAME_BYTES) -> (600, 800) uint8 of stored intensities."""
    mask, nibble_of = _segment_tables()
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


def list_clips(directory):
    """(path, frame_count) for every .seg in `directory` that is a whole number
    of current-geometry frames.  Clips from the 640x480 era are not, and would
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


_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def gradient_palette(stops):
    """Colour stops ('#rrggbb', at least two) spread evenly over entries 1-15,
    linearly interpolated.  Entry 0 stays black.  8-bit stops are quantised to
    the palette's 4 bits. A table, not a curve: fit_curve() it to use it."""
    if len(stops) < 2:
        raise ValueError("a gradient needs at least two colour stops")
    cols = []
    for s in stops:
        if not _HEX.match(s):
            raise ValueError(f"{s!r} is not a #rrggbb colour")
        cols.append(tuple(int(s[i : i + 2], 16) / 17 for i in (1, 3, 5)))
    pal = [(0, 0, 0)]
    for i in range(1, 16):
        pos = (i - 1) / 14 * (len(cols) - 1)
        lo = min(int(pos), len(cols) - 2)
        t = pos - lo
        pal.append(
            tuple(
                min(15, int(cols[lo][ch] * (1 - t) + cols[lo + 1][ch] * t + 0.5))
                for ch in range(3)
            )
        )
    return pal


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

PLOT = 20  # pixels per index / level on the curve plot
PLAY_MS = 1000 // 24  # clip playback frame period; video2seg.py's default fps
PAD = 24
CH_COLOURS = {"r": "#d32f2f", "g": "#2e7d32", "b": "#1565c0"}


class App:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        self.root = root
        root.title("Palette builder")
        self.curve = {ch: list(segments.PRESETS[0][ch]) for ch in CHANNELS}
        self._photo = None
        self._clip_idx = (None, None, None)  # (path, frame, index image) cache
        self._drag = None  # which handle (0 = knee, 1 = second point) is held
        self._syncing = False
        self._playing = None  # the pending after() id while a clip plays

        self.channel = tk.StringVar(value="r")
        self.source = tk.StringVar(value="generator")
        self.bits = tk.IntVar(value=12)
        self.name = tk.StringVar(value="custom")
        self.stops = tk.StringVar(value="#0000ff #00ffff #ffffff")
        self.slot = tk.IntVar(value=0)
        self.point_vars = [tk.IntVar() for _ in range(4)]

        try:
            self.gen_idx = index_image_from_png(GENERATOR_PNG)
            self.gen_error = None
        except (OSError, ValueError) as e:
            self.gen_idx, self.gen_error = None, str(e)
        self.clips = list_clips(REPO)

        self._build()
        if self.gen_idx is None and self.clips:
            self.source.set("droplet")
        self._channel_changed()

    # -- layout --

    def _build(self):
        tk, ttk = self.tk, self.ttk
        left = ttk.Frame(self.root, padding=8)
        left.grid(row=0, column=0, sticky="ns")
        right = ttk.Frame(self.root, padding=8)
        right.grid(row=0, column=1, sticky="nsew")
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        chans = ttk.Frame(left)
        chans.grid(row=0, column=0, sticky="w")
        ttk.Label(chans, text="Edit channel:").pack(side="left")
        for ch in CHANNELS:
            ttk.Radiobutton(
                chans, text=ch.upper(), value=ch, variable=self.channel, command=self._channel_changed
            ).pack(side="left")

        size = 2 * PAD + 15 * PLOT
        self.plot = tk.Canvas(left, width=size, height=size, bg="white", highlightthickness=1)
        self.plot.grid(row=1, column=0, pady=4)
        self.plot.bind("<Button-1>", self._on_press)
        self.plot.bind("<B1-Motion>", self._on_drag)
        self.plot.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_drag", None))

        pts = ttk.Frame(left)
        pts.grid(row=2, column=0, sticky="w")
        for i, label in enumerate(("knee x", "y", "  then x", "y")):
            ttk.Label(pts, text=label).pack(side="left")
            sb = tk.Spinbox(pts, from_=0, to=15, width=3, textvariable=self.point_vars[i],
                            command=self._on_spin)
            sb.bind("<Return>", lambda _e: self._on_spin())
            sb.pack(side="left")

        self.swatch_row = tk.Frame(left)
        self.swatch_row.grid(row=3, column=0, pady=4)
        self.swatches = []
        for i in range(16):
            sw = tk.Label(self.swatch_row, width=2, height=1, relief="sunken", text=f"{i:X}",
                          font=("TkFixedFont", 7))
            sw.pack(side="left")
            self.swatches.append(sw)

        pre = ttk.LabelFrame(left, text="Start from a built-in preset", padding=4)
        pre.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        for n, p in enumerate(segments.PRESETS):
            ttk.Button(pre, text=f"{n} {p['name']}", width=9, command=lambda n=n: self._load_preset(n)).grid(
                row=n // 4, column=n % 4
            )

        grad = ttk.LabelFrame(left, text="Fit to a gradient over entries 1-15", padding=4)
        grad.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        ttk.Entry(grad, textvariable=self.stops, width=30).grid(row=0, column=0)
        ttk.Button(grad, text="Fit", command=self._apply_gradient).grid(row=0, column=1)

        io = ttk.LabelFrame(left, text="Save / export", padding=4)
        io.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        ttk.Entry(io, textvariable=self.name, width=14).grid(row=0, column=0)
        ttk.Button(io, text="Save", width=6, command=self._save).grid(row=0, column=1)
        ttk.Button(io, text="Open...", width=7, command=self._open).grid(row=0, column=2)
        ttk.Button(io, text="Export", width=7, command=self._export).grid(row=0, column=3)

        chip = ttk.LabelFrame(left, text="Chip presets (presets.json -> src/)", padding=4)
        chip.grid(row=7, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(chip, text="slot").grid(row=0, column=0)
        ttk.Spinbox(chip, from_=0, to=segments.N_PRESETS - 1, width=3, textvariable=self.slot).grid(row=0, column=1)
        ttk.Button(chip, text="Save to slot", command=self._save_slot).grid(row=0, column=2)
        ttk.Button(chip, text="Write RTL", command=self._write_rtl).grid(row=0, column=3)

        top = ttk.Frame(right)
        top.grid(row=0, column=0, sticky="w")
        ttk.Label(top, text="Source:").pack(side="left")
        for val, label in (("generator", "Generator"), ("droplet", "Droplet")):
            ttk.Radiobutton(top, text=label, value=val, variable=self.source, command=self.refresh).pack(side="left")
        ttk.Label(top, text="   Output:").pack(side="left")
        for val, label in ((12, "12-bit PmodVGA"), (6, "6-bit Tiny VGA")):
            ttk.Radiobutton(top, text=label, value=val, variable=self.bits, command=self.refresh).pack(side="left")
        ttk.Label(top, text="   (keys: s source, b bits, space play)").pack(side="left")

        clip = ttk.Frame(right)
        clip.grid(row=1, column=0, sticky="ew", pady=4)
        self.clip_box = ttk.Combobox(
            clip, state="readonly", width=28, values=[os.path.basename(p) for p, _ in self.clips]
        )
        self.clip_box.pack(side="left")
        if self.clips:
            best = next((i for i, (p, _) in enumerate(self.clips) if "waterdrop" in p), 0)
            self.clip_box.current(best)
        self.clip_box.bind("<<ComboboxSelected>>", lambda _e: self._clip_changed())
        self.frame_scale = tk.Scale(clip, from_=0, to=0, orient="horizontal", length=420, command=self._on_frame_scale)
        self.frame_scale.pack(side="left", padx=8)
        self.play_button = ttk.Button(clip, text="Play", width=6, command=self._toggle_play,
                                      state="normal" if self.clips else "disabled")
        self.play_button.pack(side="left")
        self._clip_changed(refresh=False)

        self.preview = tk.Label(right, bd=1, relief="sunken")
        self.preview.grid(row=2, column=0)
        self.status = tk.Label(right, justify="left", anchor="w", wraplength=WIDTH)
        self.status.grid(row=3, column=0, sticky="ew", pady=4)

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

    # -- state changes --

    def _on_key(self, e):
        if isinstance(e.widget, (self.tk.Entry, self.ttk.Entry, self.ttk.Combobox, self.tk.Spinbox, self.ttk.Spinbox)):
            return
        if e.char == "s":
            self.source.set("droplet" if self.source.get() == "generator" else "generator")
            self.refresh()
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
        if not self.clips:
            return
        self.source.set("droplet")
        self.play_button.config(text="Pause")
        self._tick()

    def _tick(self):
        """Advance one frame and loop at the end. Paced at the converter's
        default 24 fps, less however long the frame took to draw, so a slow
        render slows playback rather than piling up callbacks."""
        start = time.monotonic()
        n = self.clips[self.clip_box.current()][1]
        self.frame_scale.set((int(self.frame_scale.get()) + 1) % max(n, 1))
        self.refresh()
        spent = int((time.monotonic() - start) * 1000)
        self._playing = self.root.after(max(1, PLAY_MS - spent), self._tick)

    def _clip_changed(self, refresh=True):
        n = self.clips[self.clip_box.current()][1] if self.clips else 1
        self.frame_scale.config(to=max(n - 1, 0))
        self.frame_scale.set(min(self.frame_scale.get(), n - 1))
        if refresh:
            self.refresh()

    def _channel_changed(self):
        self._syncing = True
        for var, v in zip(self.point_vars, self.curve[self.channel.get()]):
            var.set(v)
        self._syncing = False
        self.refresh()

    def _set_points(self, pts):
        """Try new points for the selected channel; nudge them into the
        hardware's legal range rather than refuse, so a drag never sticks."""
        x1, y1, x2, y2 = pts
        x1 = min(x1, 14)
        if x1 == 0:
            y1 = 0
        x2 = max(x2, x1 + 1)
        y2 = max(y2, y1)
        self.curve[self.channel.get()] = [x1, y1, x2, y2]
        self._channel_changed()

    def _on_spin(self):
        if self._syncing:
            return
        try:
            self._set_points([int(v.get()) for v in self.point_vars])
        except (ValueError, self.tk.TclError):
            pass

    def _on_press(self, e):
        x1, y1, x2, y2 = self.curve[self.channel.get()]
        d = [
            (e.x - cx) ** 2 + (e.y - cy) ** 2
            for cx, cy in (self._to_canvas(x1, y1), self._to_canvas(x2, y2))
        ]
        self._drag = 0 if d[0] <= d[1] else 1
        self._on_drag(e)

    def _on_drag(self, e):
        if self._drag is None:
            return
        x, y = self._from_canvas(e.x, e.y)
        pts = list(self.curve[self.channel.get()])
        pts[2 * self._drag : 2 * self._drag + 2] = [x, y]
        self._set_points(pts)

    def _set_curve(self, curve):
        self.curve = {ch: list(curve[ch]) for ch in CHANNELS}
        self._channel_changed()

    def _load_preset(self, n):
        self._set_curve(segments.PRESETS[n])
        self.name.set(segments.PRESETS[n]["name"])
        self.slot.set(n)

    def _apply_gradient(self):
        try:
            self._set_curve(fit_curve(gradient_palette(re.split(r"[\s,]+", self.stops.get().strip()))))
        except ValueError as e:
            self._say(f"gradient: {e}", bad=True)

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
            "src/palette_presets.v (restart the builder to see it in the preset buttons)"
        )

    def _write_rtl(self):
        path = write_rtl()
        self._say(f"wrote {Path(path).resolve().relative_to(REPO)} from presets.json")

    # -- drawing --

    def _current_index_image(self):
        if self.source.get() == "generator":
            if self.gen_idx is None:
                raise ValueError(f"generator frame unavailable: {self.gen_error}")
            return self.gen_idx
        if not self.clips:
            raise ValueError("no current-geometry .seg clips found in the repo root")
        path, _ = self.clips[self.clip_box.current()]
        n = int(self.frame_scale.get())
        if self._clip_idx[:2] != (path, n):
            self._clip_idx = (path, n, index_image_from_frame(read_frame(path, n)))
        return self._clip_idx[2]

    def _say(self, msg, bad=False):
        self.status.config(text=msg, fg="#b00020" if bad else "#1b5e20")

    def _draw_plot(self, table):
        c = self.plot
        c.delete("all")
        for i in range(16):
            x, _ = self._to_canvas(i, 0)
            _, y = self._to_canvas(0, i)
            c.create_line(x, PAD, x, PAD + 15 * PLOT, fill="#eeeeee")
            c.create_line(PAD, y, PAD + 15 * PLOT, y, fill="#eeeeee")
        active = self.channel.get()
        for ch_i, ch in enumerate(CHANNELS):
            col = CH_COLOURS[ch]
            width = 3 if ch == active else 1
            # What the chip draws: the quantised values, as a stepped line.
            pts = [self._to_canvas(i, table[i][ch_i]) for i in range(16)]
            c.create_line(*[v for p in pts for v in p], fill=col, width=width)
            for px, py in pts:
                c.create_oval(px - 2, py - 2, px + 2, py + 2, fill=col, outline=col)
        # Handles for the channel being edited: the knee and the second point.
        x1, y1, x2, y2 = self.curve[active]
        for (x, y), label in (((x1, y1), "knee"), ((x2, y2), "")):
            cx, cy = self._to_canvas(x, y)
            c.create_rectangle(cx - 6, cy - 6, cx + 6, cy + 6, outline=CH_COLOURS[active], width=2)
            if label:
                c.create_text(cx + 10, cy + 10, text=label, anchor="nw", fill=CH_COLOURS[active])

    def refresh(self):
        try:
            table = curve_table(self.curve)
        except ValueError as e:
            self._say(f"curve: {e}", bad=True)
            return
        self._draw_plot(table)
        lut = palette_to_lut(table, self.bits.get())
        for i in range(16):
            r, g, b = (int(v) for v in lut[i])
            self.swatches[i].config(bg=f"#{r:02x}{g:02x}{b:02x}", fg="white" if r + g + b < 300 else "black")
        try:
            img = lut[self._current_index_image()]
        except (ValueError, IndexError, OSError) as e:
            self._say(str(e), bad=True)
            return
        self._photo = self.tk.PhotoImage(data=b"P6\n%d %d\n255\n" % (WIDTH, HEIGHT) + img.tobytes())
        self.preview.config(image=self._photo)
        warns = check_palette(table)
        self._say("\n".join(warns) if warns else "All 16 entries distinct at 12-bit and 6-bit; entry 0 is black.", bool(warns))


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
